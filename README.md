# AiPhotoRestorer

Batch photo restoration tool using the Google Gemini image editing API.

## Features

- **Standalone resize phase** — `input/` → `resized/` (inspectable before AI processing)
- Named size presets: `4k` (3840px), `2k` (2560px), `fhd` (1920px), `hd` (1280px)
- Restores photos using Gemini AI (remove scratches, enhance detail, fix exposure)
- **Native 4K output** via `gemini-3-pro-image-preview` with `image_size: "4K"` in config
- Real-time (`run`) and async batch (`batch`) processing modes
- Editable restoration prompt in `config.yaml`
- Rate limiting to stay within API quotas
- Tracks progress in SQLite — safe to resume after interruption; failed photos retry automatically on next run
- Dry-run mode to preview without API calls

## Setup

```bash
# 1. Create a virtual environment
python3 -m venv .venv 
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. Install dependencies
python3 -m pip install --upgrade pip 
python3 -m pip install -r requirements.txt

# 3. Set your Gemini API key
echo "GEMINI_API_KEY=your_key_here" > .env
```

Get a Gemini API key at https://aistudio.google.com/app/apikey

## Usage

### Recommended workflow

```bash
# Step 1: Resize photos (input/ → resized/)
python3 main.py resize

# Step 2: Inspect resized/ — confirm quality before spending API credits

# Step 3: Run AI restoration (auto-picks resized/ if populated)
python3 main.py run
```

### Resize command

```bash
# Resize using defaults from config.yaml (fhd preset, JPEG quality 95)
python3 main.py resize

# Choose a size preset
python3 main.py resize --size 4k
python3 main.py resize --size 2k
python3 main.py resize --size hd

# Or specify exact pixel count for the longest edge
python3 main.py resize --size 1600

# Custom folders, quality, format
python3 main.py resize --input /path/to/photos --output /path/to/resized --quality 90 --format PNG

# Preview without writing files
python3 main.py resize --dry-run

# Re-resize all photos (ignore previous progress, overwrite resized/)
python3 main.py resize --force

# Change settings and redo everything
python3 main.py resize --force --size 2k
```

### Real-time processing (`run`)

```bash
# Process photos — auto-detects resized/ if populated, otherwise uses input/
python3 main.py run

# Force a specific input folder
python3 main.py run --input input/

# Preview without API calls
python3 main.py run --dry-run

# Re-process all photos (ignore previous progress)
python3 main.py run --force
```

Failed photos are never marked as done — just run again to retry them automatically.

### Async batch processing (`batch`)

50% cheaper than real-time; jobs may take up to 24 hours.

```bash
# Submit and wait
python3 main.py batch

# Submit and exit immediately
python3 main.py batch --no-wait

# Re-process all photos (ignore previous progress)
python3 main.py batch --force

# Collect results later
python3 main.py collect <job-name>
python3 main.py collect <job-name> --wait   # block until complete

# List all submitted jobs
python3 main.py jobs
```

## Configuration

Edit `config.yaml` to change:

- **model** — `gemini-3-pro-image-preview` (4K output) or `gemini-2.5-flash-image` (faster, cheaper, 1K output)
- **batch_size** — photos per batch
- **output** — format (JPEG/PNG), quality, max dimensions before API call, `image_size` (`"1K"` / `"2K"` / `"4K"`)
- **resize** — default size preset, quality, format, output folder
- **rate_limit** — requests per minute, retry attempts/wait (retries happen automatically on API errors)
- **prompt** — the restoration instructions sent to the AI (faces, colors, text preservation, etc.)

## Project Structure

```
photoRestorer/
├── config.yaml          # model, batch, rate limit, resize settings, AI prompt
├── main.py              # CLI entry point
├── requirements.txt
├── input/               # place source photos here (gitignored)
├── resized/             # resized photos ready for AI (gitignored)
├── processed/           # restored photos appear here (gitignored)
└── src/
    ├── batch.py         # folder walker + batch generator
    ├── config.py        # settings loader
    ├── processor.py     # token-budget resize + Gemini API call
    ├── rate_limiter.py  # sliding-window rate limiter
    ├── resizer.py       # standalone resize phase (presets + longest-edge)
    └── tracker.py       # SQLite progress tracker
```
