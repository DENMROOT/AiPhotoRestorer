import base64
import io
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from google import genai
from PIL import Image
from rich.console import Console

from .processor import resize_image, image_to_base64

console = Console()

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

TERMINAL_STATES = {
    "JOB_STATE_SUCCEEDED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
}


class BatchJob:
    """
    Wraps the Gemini Batch API for large-scale photo restoration.

    Flow:
        prepare_jsonl() → upload() → submit() → poll() → save_results()

    For long-running jobs, use submit() with no-wait and later call
    collect() once the job completes.
    """

    def __init__(self, config: dict, api_key: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._api_key = api_key
        self._model = config["model"]
        self._prompt = config["prompt"].strip()
        self._output_cfg = config["output"]
        self._image_size = config["output"].get("image_size")
        self._poll_interval = config.get("batch", {}).get("poll_interval_seconds", 60)

    def prepare_jsonl(self, photos: list[Path], filename: str = "batch_input.jsonl") -> Path:
        """Encode all photos into a JSONL request file. Returns path to file."""
        jsonl_path = Path(filename)
        fmt = self._output_cfg["format"]
        mime = "image/jpeg" if fmt.upper() == "JPEG" else "image/png"

        generation_config: dict = {"responseModalities": ["TEXT", "IMAGE"]}
        if self._image_size:
            generation_config["imageConfig"] = {"imageSize": self._image_size}

        with open(jsonl_path, "w") as f:
            for photo in photos:
                image = Image.open(photo).convert("RGB")
                image = resize_image(
                    image,
                    self._output_cfg["max_width"],
                    self._output_cfg["max_height"],
                )
                b64 = image_to_base64(image, fmt)
                entry = {
                    "key": photo.name,
                    "request": {
                        "contents": [
                            {
                                "role": "user",
                                "parts": [
                                    {"text": self._prompt},
                                    {"inlineData": {"mimeType": mime, "data": b64}},
                                ],
                            }
                        ],
                        "generationConfig": generation_config,
                    },
                }
                f.write(json.dumps(entry) + "\n")

        console.print(f"Prepared [bold]{len(photos)}[/bold] request(s) → {jsonl_path}")
        return jsonl_path

    def upload(self, jsonl_path: Path, retries: int = 3) -> str:
        """Upload JSONL via direct REST resumable upload. Returns the file resource name.

        Bypasses the SDK's client.files.upload() which has a known bug finalizing
        large files (raises 'Upload has already been terminated').
        Each retry starts a fresh upload session (503s invalidate the session URL).
        """
        content = jsonl_path.read_bytes()
        size = len(content)
        console.print(f"Uploading request file ({size / 1024 / 1024:.1f} MB) to Gemini File API...")

        last_error: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            if attempt > 1:
                wait = 5 * attempt
                console.print(f"  [yellow]Retrying upload (attempt {attempt}/{retries}) in {wait}s...[/yellow]")
                time.sleep(wait)

            try:
                # Step 1: initiate a fresh resumable upload session
                start_resp = requests.post(
                    "https://generativelanguage.googleapis.com/upload/v1beta/files",
                    params={"key": self._api_key},
                    headers={
                        "X-Goog-Upload-Protocol": "resumable",
                        "X-Goog-Upload-Command": "start",
                        "X-Goog-Upload-Header-Content-Length": str(size),
                        "X-Goog-Upload-Header-Content-Type": "text/plain",
                        "Content-Type": "application/json",
                    },
                    json={"file": {"display_name": jsonl_path.name}},
                    timeout=30,
                )
                start_resp.raise_for_status()
                upload_url = start_resp.headers["X-Goog-Upload-URL"]

                # Step 2: upload content and finalize
                upload_resp = requests.post(
                    upload_url,
                    headers={
                        "X-Goog-Upload-Command": "upload, finalize",
                        "X-Goog-Upload-Offset": "0",
                        "Content-Length": str(size),
                    },
                    data=content,
                    timeout=300,
                )
                upload_resp.raise_for_status()

                file_name = upload_resp.json()["file"]["name"]
                console.print(f"  File resource: [dim]{file_name}[/dim]")
                return file_name

            except Exception as exc:
                last_error = exc
                console.print(f"  [red]Upload attempt {attempt} failed: {exc}[/red]")

        raise RuntimeError(f"Upload failed after {retries} attempts") from last_error

    def submit(self, file_name: str) -> str:
        """Submit a batch job against the uploaded JSONL file. Returns job name."""
        job = self._client.batches.create(
            model=self._model,
            src=file_name,
        )
        console.print(f"Job submitted: [bold cyan]{job.name}[/bold cyan]")
        return job.name

    def get_status(self, job_name: str):
        """Fetch current job status without blocking. Returns SDK job object."""
        return self._client.batches.get(name=job_name)

    def poll(self, job_name: str):
        """Block until job reaches a terminal state. Returns final SDK job object."""
        start = time.monotonic()
        while True:
            job = self._client.batches.get(name=job_name)
            state = job.state.name if job.state else "UNKNOWN"
            elapsed = int(time.monotonic() - start)
            timestamp = datetime.now().strftime("%H:%M:%S")
            console.print(
                f"  [{timestamp}] State: [cyan]{state}[/cyan]  "
                f"[dim](elapsed {elapsed}s, next check in {self._poll_interval}s)[/dim]"
            )

            if state in TERMINAL_STATES:
                if state != "JOB_STATE_SUCCEEDED":
                    raise RuntimeError(f"Batch job ended with state: {state}")
                return job

            for remaining in range(self._poll_interval, 0, -1):
                print(f"\r  Waiting... {remaining:3d}s remaining", end="", flush=True)
                time.sleep(1)
            print("\r" + " " * 40 + "\r", end="", flush=True)  # clear the countdown line

    def save_results(self, job, output_dir: Path) -> list[str]:
        """
        Download result JSONL from completed job, decode images, save to output_dir.
        Returns list of successfully saved filenames.

        Note: uses direct REST download as a workaround for SDK bug #1759
        (batch output file IDs exceed the SDK's 40-character validation limit).
        """
        fmt = self._output_cfg["format"]
        quality = self._output_cfg.get("quality", 90)
        save_kwargs = {"quality": quality} if fmt.upper() == "JPEG" else {}

        # Extract result file ID from job — direct REST download bypasses SDK bug #1759
        dest = job.dest if hasattr(job, "dest") else None
        result_file_id = (
            getattr(dest, "file_name", None)
            or getattr(dest, "fileName", None)
        ) if dest else None
        if not result_file_id:
            raise ValueError(f"No result file found in completed job: {job.name}")

        url = f"{GEMINI_API_BASE}/{result_file_id}:download"
        resp = requests.get(
            url, params={"key": self._api_key, "alt": "media"}, timeout=300
        )
        resp.raise_for_status()

        saved = []
        for line in resp.text.splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            key = entry.get("key")
            response = entry.get("response")
            if not key or not response:
                console.print(f"[yellow]Skipping — no response for: {key}[/yellow]")
                continue

            image_saved = False
            for candidate in response.get("candidates", []):
                for part in candidate.get("content", {}).get("parts", []):
                    inline = part.get("inlineData") or part.get("inline_data")
                    if inline:
                        img_bytes = base64.b64decode(inline["data"])
                        image = Image.open(io.BytesIO(img_bytes))
                        image.save(output_dir / key, format=fmt, **save_kwargs)
                        saved.append(key)
                        image_saved = True
                        break
                if image_saved:
                    break

            if not image_saved:
                console.print(f"[red]No image in response for: {key}[/red]")

        return saved
