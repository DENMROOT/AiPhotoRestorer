import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

from src.config import Settings, load_config
from src.batch import iter_batches
from src.tracker import (
    init_db,
    get_processed,
    mark_done,
    clear_processed,
    save_batch_job,
    update_batch_status,
    list_batch_jobs,
    mark_resized,
    get_resized,
    clear_resized,
)
from src.processor import PhotoProcessor
from src.batch_job import BatchJob, TERMINAL_STATES
from src.resizer import parse_size, resize_photos, PRESETS

app = typer.Typer(help="Batch photo restoration using Gemini AI.")
console = Console()

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp"}


def _resolve_input_dir(preferred: Path, fallback: Path) -> Path:
    if preferred.exists() and any(
        p.suffix.lower() in IMAGE_EXTENSIONS for p in preferred.iterdir()
    ):
        return preferred
    return fallback


@app.command()
def resize(
    input_dir: Path = typer.Option(Path("input"), "--input", "-i", help="Folder with source photos"),
    output_dir: Path = typer.Option(None, "--output", "-o", help="Folder for resized photos (default: from config)"),
    size: Optional[str] = typer.Option(None, "--size", "-s", help="Size preset (4k/2k/fhd/hd) or max pixels"),
    quality: Optional[int] = typer.Option(None, "--quality", "-q", help="JPEG quality 1-100"),
    fmt: Optional[str] = typer.Option(None, "--format", "-f", help="Output format: JPEG or PNG"),
    config_path: str = typer.Option("config.yaml", "--config", "-c", help="Path to config.yaml"),
    dry_run: bool = typer.Option(False, "--dry-run", help="List photos to resize without writing files"),
    force: bool = typer.Option(False, "--force", help="Re-resize all photos, ignoring previous progress"),
) -> None:
    """Resize phase: input/ → resized/ (inspectable before AI processing)."""
    config = load_config(config_path)
    resize_cfg = config.get("resize", {})

    resolved_size = size or resize_cfg.get("size", "fhd")
    resolved_quality = quality or resize_cfg.get("quality", 95)
    resolved_fmt = fmt or resize_cfg.get("format", "JPEG")
    resolved_output = output_dir or Path(resize_cfg.get("output_dir", "resized"))

    max_px = parse_size(resolved_size)

    init_db()

    if force:
        clear_resized()
        console.print("[yellow]Progress reset — all photos will be re-resized.[/yellow]")

    photos = [
        p for p in input_dir.iterdir()
        if p.suffix.lower() in IMAGE_EXTENSIONS
    ] if input_dir.exists() else []

    if not photos:
        console.print("[green]No photos found in input folder.[/green]")
        raise typer.Exit()

    already_resized = get_resized()
    pending = [p for p in photos if p.name not in already_resized]
    skipped = len(photos) - len(pending)

    console.print(
        f"Found [bold]{len(photos)}[/bold] photo(s): "
        f"[bold]{len(pending)}[/bold] to resize, [dim]{skipped} already done[/dim]."
    )
    console.print(f"Target size: [bold]{resolved_size}[/bold] ({max_px}px longest edge) → {resolved_output}/")

    if dry_run:
        for p in pending:
            console.print(f"  [dim]{p.name}[/dim]")
        raise typer.Exit()

    if not pending:
        console.print("[green]Nothing to resize — all photos already resized.[/green]")
        raise typer.Exit()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Resizing photos...", total=len(photos))
        processed_count, skipped_count = resize_photos(
            input_dir, resolved_output, max_px, resolved_quality, resolved_fmt, progress, task
        )

    console.print(
        f"[bold green]Done.[/bold green] Resized {processed_count} photo(s), skipped {skipped_count}."
    )


@app.command()
def run(
    input_dir: Optional[Path] = typer.Option(None, "--input", "-i", help="Folder with source photos (auto-detects resized/ if populated)"),
    output_dir: Path = typer.Option(Path("processed"), "--output", "-o", help="Folder for restored photos"),
    config_path: str = typer.Option("config.yaml", "--config", "-c", help="Path to config.yaml"),
    dry_run: bool = typer.Option(False, "--dry-run", help="List photos to process without calling the API"),
    force: bool = typer.Option(False, "--force", help="Re-process all photos, ignoring previous progress"),
) -> None:
    """Real-time processing — one photo at a time with rate limiting."""
    if input_dir is None:
        input_dir = _resolve_input_dir(Path("resized"), Path("input"))

    config = load_config(config_path)
    settings = Settings()

    output_dir.mkdir(parents=True, exist_ok=True)
    init_db()

    if force:
        clear_processed()
        console.print("[yellow]Progress reset — all photos will be re-processed.[/yellow]")

    processed = get_processed()
    batches = list(iter_batches(input_dir, config["batch_size"], processed))
    total = sum(len(b) for b in batches)

    if total == 0:
        console.print("[green]Nothing to process — all photos are already done.[/green]")
        raise typer.Exit()

    console.print(f"Found [bold]{total}[/bold] photo(s) to process in {len(batches)} batch(es).")
    console.print(f"Input: [dim]{input_dir}/[/dim]")

    if dry_run:
        for batch in batches:
            for photo in batch:
                console.print(f"  [dim]{photo.name}[/dim]")
        raise typer.Exit()

    processor = PhotoProcessor(config, settings.gemini_api_key)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Restoring photos...", total=total)

        for batch in batches:
            for photo in batch:
                progress.update(task, description=f"Processing [cyan]{photo.name}[/cyan]")
                output_path = processor.process(photo, output_dir)
                if output_path:
                    mark_done(photo.name)
                    console.print(f"  [green]✓[/green] {photo.name} → {output_path}")
                progress.advance(task)

    console.print("[bold green]Done.[/bold green]")


def _poll_jobs_until_done(
    job: BatchJob,
    job_names: list[str],
    output_dir: Path,
    poll_interval: int,
) -> None:
    pending = list(job_names)
    start = time.monotonic()
    console.print(f"\nPolling {len(pending)} job(s) every {poll_interval}s (Ctrl+C to exit and collect later)...")

    while pending:
        still_pending = []
        elapsed = int(time.monotonic() - start)
        timestamp = datetime.now().strftime("%H:%M:%S")

        for job_name in pending:
            j = job.get_status(job_name)
            state = j.state.name if j.state else "UNKNOWN"
            short_name = job_name.split("/")[-1][:24]
            console.print(
                f"  [{timestamp}] {short_name}: [cyan]{state}[/cyan]  [dim](elapsed {elapsed}s)[/dim]"
            )

            if state == "JOB_STATE_SUCCEEDED":
                update_batch_status(job_name, "succeeded")
                saved = job.save_results(j, output_dir)
                for filename in saved:
                    mark_done(filename)
                    console.print(f"  [green]✓[/green] {filename} → {output_dir / filename}")
            elif state in TERMINAL_STATES:
                console.print(f"  [red]Job ended with {state}: {job_name}[/red]")
            else:
                still_pending.append(job_name)

        pending = still_pending
        if pending:
            for remaining in range(poll_interval, 0, -1):
                print(
                    f"\r  Waiting {remaining:3d}s... ({len(pending)} job(s) still running)",
                    end="", flush=True,
                )
                time.sleep(1)
            print("\r" + " " * 60 + "\r", end="", flush=True)


@app.command()
def batch(
    input_dir: Optional[Path] = typer.Option(None, "--input", "-i", help="Folder with source photos (auto-detects resized/ if populated)"),
    output_dir: Path = typer.Option(Path("processed"), "--output", "-o", help="Folder for restored photos"),
    config_path: str = typer.Option("config.yaml", "--config", "-c", help="Path to config.yaml"),
    no_wait: bool = typer.Option(False, "--no-wait", help="Submit job and exit; use `collect` later to retrieve results"),
    force: bool = typer.Option(False, "--force", help="Re-process all photos, ignoring previous progress"),
) -> None:
    """
    Async batch processing via Gemini Batch API (50% cheaper, up to 24h turnaround).

    Large sets are automatically split into jobs of batch.job_size photos each.
    Use --no-wait to submit without blocking, then run `collect` for each job name.
    """
    if input_dir is None:
        input_dir = _resolve_input_dir(Path("resized"), Path("input"))

    config = load_config(config_path)
    settings = Settings()

    output_dir.mkdir(parents=True, exist_ok=True)
    init_db()

    if force:
        clear_processed()
        console.print("[yellow]Progress reset — all photos will be re-processed.[/yellow]")

    processed = get_processed()
    all_photos = [
        p for batch_group in iter_batches(input_dir, 9999, processed)
        for p in batch_group
    ]

    if not all_photos:
        console.print("[green]Nothing to process — all photos are already done.[/green]")
        raise typer.Exit()

    job_size = config.get("batch", {}).get("job_size", 20)
    chunks = [all_photos[i:i + job_size] for i in range(0, len(all_photos), job_size)]
    poll_interval = config.get("batch", {}).get("poll_interval_seconds", 60)

    console.print(
        f"Found [bold]{len(all_photos)}[/bold] photo(s) → "
        f"[bold]{len(chunks)}[/bold] job(s) of up to {job_size} each.  "
        f"Input: [dim]{input_dir}/[/dim]"
    )

    job = BatchJob(config, settings.gemini_api_key)
    submitted_jobs: list[str] = []

    for i, chunk in enumerate(chunks, 1):
        console.print(f"\n[bold]Job {i}/{len(chunks)}[/bold] — {len(chunk)} photo(s)")
        jsonl_path = job.prepare_jsonl(chunk, filename=f"batch_input_{i}.jsonl")
        file_name = job.upload(jsonl_path)
        job_name = job.submit(file_name)
        save_batch_job(job_name)
        submitted_jobs.append(job_name)

    console.print(f"\nSubmitted [bold]{len(submitted_jobs)}[/bold] job(s).")

    if no_wait:
        console.print("Exiting without waiting. Collect each job when ready:")
        for jn in submitted_jobs:
            console.print(f"  python3 main.py collect [cyan]{jn}[/cyan]")
        raise typer.Exit()

    try:
        _poll_jobs_until_done(job, submitted_jobs, output_dir, poll_interval)
    except KeyboardInterrupt:
        console.print("\nInterrupted. Collect remaining jobs with:")
        for jn in submitted_jobs:
            console.print(f"  python3 main.py collect [cyan]{jn}[/cyan]")
        raise typer.Exit()

    console.print(f"\n[bold green]Done.[/bold green]")


@app.command()
def collect(
    job_name: str = typer.Argument(..., help="Batch job name returned from `batch --no-wait`"),
    output_dir: Path = typer.Option(Path("processed"), "--output", "-o", help="Folder to save restored photos"),
    config_path: str = typer.Option("config.yaml", "--config", "-c", help="Path to config.yaml"),
    wait: bool = typer.Option(False, "--wait", help="Poll until job completes before collecting"),
) -> None:
    """Retrieve and save results from a previously submitted batch job."""
    config = load_config(config_path)
    settings = Settings()

    output_dir.mkdir(parents=True, exist_ok=True)
    init_db()

    job = BatchJob(config, settings.gemini_api_key)

    if wait:
        console.print(f"Polling [bold]{job_name}[/bold] until complete...")
        completed_job = job.poll(job_name)
    else:
        completed_job = job.get_status(job_name)
        state = completed_job.state.name if completed_job.state else "UNKNOWN"
        if state != "JOB_STATE_SUCCEEDED":
            console.print(f"[yellow]Job state is [bold]{state}[/bold] — not ready yet.[/yellow]")
            console.print("Run with [bold]--wait[/bold] to block until complete.")
            raise typer.Exit(1)

    update_batch_status(job_name, "succeeded")

    saved = job.save_results(completed_job, output_dir)
    for filename in saved:
        mark_done(filename)
        console.print(f"  [green]✓[/green] {filename} → {output_dir / filename}")

    console.print(f"\n[bold green]Done.[/bold green] Saved {len(saved)} photo(s).")


@app.command()
def jobs() -> None:
    """List all previously submitted batch jobs."""
    init_db()
    rows = list_batch_jobs()

    if not rows:
        console.print("No batch jobs found.")
        raise typer.Exit()

    table = Table(title="Batch Jobs")
    table.add_column("Job Name", style="cyan")
    table.add_column("Submitted At")
    table.add_column("Status", style="bold")

    for row in rows:
        status_color = "green" if row["status"] == "succeeded" else "yellow"
        table.add_row(
            row["job_name"],
            row["submitted_at"],
            f"[{status_color}]{row['status']}[/{status_color}]",
        )

    console.print(table)


if __name__ == "__main__":
    app()
