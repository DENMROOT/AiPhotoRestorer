# Requirements

**Project:** Photo Restorer
**Version:** 1.0
**Date:** 2026-03-13

---

## Overview

CLI tool for batch restoration of old photographs using the Google Gemini image API (Nano Banana models). Photos are read from a local folder, optionally resized, sent to the AI for restoration, and saved to an output folder. Progress is tracked in SQLite so runs can be safely interrupted and resumed.

---

## Functional Requirements

### Photo Discovery
- Scan a configurable `input/` directory for images (JPEG, PNG, TIFF, BMP, WebP)
- Skip files already recorded as processed
- Group discovered photos into configurable-size batches, sorted alphabetically

### Image Pre-processing
- Resize each image to fit within configurable max dimensions before API submission (preserves aspect ratio)
- Convert to RGB and encode as base64 inline with the API request

### Real-time Mode (`python main.py run`)
- Process photos one at a time, saving results immediately
- Enforce a configurable requests-per-minute rate limit
- Retry failed calls a configurable number of times
- Skip and log photos that fail after all retries; continue with remaining photos
- Show a live progress bar during processing
- Support `--dry-run` to list pending photos without calling the API

### Batch Mode (`python main.py batch`)
- Encode all unprocessed photos into a JSONL file and upload via Gemini File API
- Submit a single async batch job (50% cheaper, up to 24h turnaround)
- Persist the job name in SQLite
- By default, poll until the job completes, then save results
- `--no-wait` flag: submit and exit immediately; use `collect` to retrieve results later

### Result Collection (`python main.py collect JOB_NAME`)
- Download result JSONL from a completed batch job
- Decode and save each restored image to the output directory
- Mark saved files as processed in SQLite
- `--wait` flag: poll until job completes before collecting

### Job History (`python main.py jobs`)
- Display a table of all submitted batch jobs with name, submission time, and status

### Configuration
- All parameters (model, batch size, output format/quality, rate limits, prompt) via `config.yaml`
- Restoration prompt editable in config without touching code
- API key via `GEMINI_API_KEY` environment variable or `.env` file
- Input/output directories and config path overridable via CLI flags

---

## Non-Functional Requirements

| Area | Requirement |
|---|---|
| **Cost control** | Images resized before submission to reduce token usage; batch mode available for 50% savings |
| **Resumability** | Progress persisted per file; re-running skips already-processed photos |
| **Security** | API key never in source control; `input/`, `processed/`, `.env`, `progress.db` gitignored |
| **Usability** | Clear progress output, descriptive errors, `--help` on all commands |
| **Maintainability** | Prompt and model configurable without code changes; modules have single responsibilities |

---

## Constraints

- Batch API input file limit: 2 GB; split large archives into multiple jobs
- Batch jobs expire after 48 hours — collect results within this window
- All output images include a Google SynthID watermark (cannot be disabled)
- Python 3.11+ required
- Internet connection required; no offline mode

---

## Out of Scope (v1.0)

- GUI
- Recursive subdirectory scanning
- EXIF metadata preservation
- Cloud storage (S3, GCS)
- Cost / token usage reporting
- Concurrent multi-process execution
