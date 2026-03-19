from pathlib import Path
from typing import Generator

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp"}


def iter_batches(
    input_dir: str | Path,
    batch_size: int,
    processed_names: set[str],
) -> Generator[list[Path], None, None]:
    """Yield batches of unprocessed image paths from input_dir."""
    input_path = Path(input_dir)
    photos = sorted(
        p for p in input_path.iterdir()
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_EXTENSIONS
        and p.name not in processed_names
    )

    for i in range(0, len(photos), batch_size):
        yield photos[i : i + batch_size]
