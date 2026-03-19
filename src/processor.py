import base64
import io
from pathlib import Path

from google import genai
from google.genai import types
from PIL import Image
from tenacity import Retrying, stop_after_attempt, wait_fixed
from rich.console import Console

from .rate_limiter import RateLimiter

console = Console()


def resize_image(image: Image.Image, max_width: int, max_height: int) -> Image.Image:
    """Resize image to fit within max dimensions, preserving aspect ratio."""
    image.thumbnail((max_width, max_height), Image.LANCZOS)
    return image


def image_to_base64(image: Image.Image, fmt: str = "JPEG") -> str:
    buf = io.BytesIO()
    image.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


class PhotoProcessor:
    def __init__(self, config: dict, api_key: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model_name = config["model"]
        self._prompt = config["prompt"].strip()
        self._output_cfg = config["output"]
        self._image_size = config["output"].get("image_size")  # e.g. "4K", "2K", "1K", or None
        self._rate_limiter = RateLimiter(config["rate_limit"]["requests_per_minute"])
        self._retry_attempts = config["rate_limit"]["retry_attempts"]
        self._retry_wait = config["rate_limit"]["retry_wait_seconds"]

    def process(self, photo_path: Path, output_dir: Path) -> Path | None:
        """Resize, restore via Gemini, and save result. Returns output path or None on failure."""
        try:
            result = self._run(photo_path, output_dir)
            return result
        except Exception as exc:
            console.print(f"[red]Failed {photo_path.name}: {exc}[/red]")
            return None

    def _run(self, photo_path: Path, output_dir: Path) -> Path:
        image = Image.open(photo_path).convert("RGB")
        image = resize_image(
            image,
            self._output_cfg["max_width"],
            self._output_cfg["max_height"],
        )

        fmt = self._output_cfg["format"]
        mime = "image/jpeg" if fmt.upper() == "JPEG" else "image/png"

        buf = io.BytesIO()
        image.save(buf, format=fmt)
        img_bytes = buf.getvalue()

        self._rate_limiter.acquire()
        for attempt in Retrying(
            stop=stop_after_attempt(self._retry_attempts),
            wait=wait_fixed(self._retry_wait),
            reraise=True,
        ):
            with attempt:
                response = self._call_api(img_bytes, mime)

        output_path = output_dir / photo_path.name
        self._save_response(response, output_path, fmt)
        return output_path

    def _call_api(self, img_bytes: bytes, mime: str):
        image_config = (
            types.ImageConfig(image_size=self._image_size)
            if self._image_size else None
        )
        cfg = types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
            **({"image_config": image_config} if image_config else {}),
        )
        return self._client.models.generate_content(
            model=self._model_name,
            contents=[
                self._prompt,
                types.Part.from_bytes(data=img_bytes, mime_type=mime),
            ],
            config=cfg,
        )

    def _save_response(self, response, output_path: Path, fmt: str) -> None:
        if not response.parts:
            raise ValueError("API returned no content (safety filter or empty response)")
        for part in response.parts:
            if part.inline_data:
                image = Image.open(io.BytesIO(part.inline_data.data))
                quality = self._output_cfg.get("quality", 90)
                save_kwargs = {"quality": quality} if fmt.upper() == "JPEG" else {}
                image.save(output_path, format=fmt, **save_kwargs)
                return
        raise ValueError("No image data in API response")
