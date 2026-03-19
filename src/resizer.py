from pathlib import Path

import typer
from PIL import Image

from src.tracker import get_resized, mark_resized

# Large scanned photos can exceed Pillow's default 89MP safety threshold.
# These are known user files, not untrusted input, so disable the limit.
Image.MAX_IMAGE_PIXELS = None

PRESETS: dict[str, int] = {
    "4k": 3840,
    "2k": 2560,
    "fhd": 1920,
    "hd": 1280,
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp"}


def parse_size(value: str) -> int:
    lower = value.lower()
    if lower in PRESETS:
        return PRESETS[lower]
    try:
        px = int(value)
        if px <= 0:
            raise ValueError
        return px
    except ValueError:
        raise typer.BadParameter(
            f"Invalid size '{value}'. Use a preset ({', '.join(PRESETS)}) or a positive integer (pixels)."
        )


def resize_longest_edge(image: Image.Image, max_px: int) -> Image.Image:
    w, h = image.size
    longest = max(w, h)
    if longest <= max_px:
        return image
    scale = max_px / longest
    new_w = round(w * scale)
    new_h = round(h * scale)
    return image.resize((new_w, new_h), Image.LANCZOS)


def resize_photos(
    input_dir: Path,
    output_dir: Path,
    max_px: int,
    quality: int,
    fmt: str,
    progress,
    task_id,
) -> tuple[int, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    already_resized = get_resized()

    photos = [
        p for p in input_dir.iterdir()
        if p.suffix.lower() in IMAGE_EXTENSIONS
    ]

    processed_count = 0
    skipped_count = 0

    for photo in photos:
        progress.update(task_id, description=f"Resizing [cyan]{photo.name}[/cyan]")

        if photo.name in already_resized:
            skipped_count += 1
            progress.advance(task_id)
            continue

        img = Image.open(photo)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        resized = resize_longest_edge(img, max_px)

        ext = ".jpg" if fmt.upper() == "JPEG" else f".{fmt.lower()}"
        out_path = output_dir / (photo.stem + ext)
        save_kwargs: dict = {"format": fmt}
        if fmt.upper() == "JPEG":
            save_kwargs["quality"] = quality
            save_kwargs["optimize"] = True

        resized.save(out_path, **save_kwargs)
        mark_resized(photo.name)
        processed_count += 1
        progress.advance(task_id)

    return processed_count, skipped_count
