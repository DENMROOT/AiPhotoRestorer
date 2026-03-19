# Architecture

**Project:** Photo Restorer
**Version:** 1.0
**Date:** 2026-03-13

---

## Overview

Single-process Python CLI application. No server, no UI, no external database. All AI work is delegated to the Google Gemini API; the application handles orchestration, pre-processing, rate control, and persistence.

---

## Module Structure

```
main.py                  CLI entry point — commands: run, batch, collect, jobs
├── src/batch.py         Photo discovery — scans input/, yields batches, skips processed
├── src/processor.py     Real-time mode — resize → encode → API call → save
├── src/batch_job.py     Batch mode — JSONL build → upload → submit → poll → save
├── src/rate_limiter.py  Sliding-window rate limiter (used by processor.py only)
├── src/tracker.py       SQLite persistence — processed files + batch job history
└── src/config.py        Settings loader — reads config.yaml + GEMINI_API_KEY from .env
```

---

## Processing Modes

### Real-time (`run`)

```
input/
  └─ batch.py ──► processor.py ──► RateLimiter.acquire()
                      │                     │
                      │              Gemini SDK
                      │          (generate_content)
                      │                     │
                      ▼                     ▼
                 tracker.py            processed/
               (mark_done)
```

One photo at a time. Blocks on the rate limiter between requests. Results are saved and tracked immediately — safe to interrupt at any point.

### Batch (`batch` → `collect`)

```
input/
  └─ batch_job.py
       │
       ├─ 1. prepare_jsonl()  — encode all photos → batch_input.jsonl
       ├─ 2. upload()         — File API → files/abc123
       ├─ 3. submit()         — POST batchGenerateContent → batches/xyz
       │                              │
       │                        tracker.py (save_batch_job)
       │
       ├─ 4. poll()           — GET batches/xyz until JOB_STATE_SUCCEEDED
       │
       └─ 5. save_results()   — download result JSONL → decode → processed/
                                        │
                                  tracker.py (mark_done)
```

All photos encoded into one JSONL file and submitted as a single async job. The submit and collect steps can run in separate sessions via `--no-wait` + `collect`.

---

## Data Flow

```
config.yaml ──► Settings
.env        ──►   │
                  ▼
              main.py (CLI)
                  │
         ┌────────┴────────┐
         ▼                 ▼
    PhotoProcessor      BatchJob
         │                 │
    [resize + b64]    [resize + b64 × N]
         │                 │
    Gemini SDK         REST API
  (real-time)          (batch)
         │                 │
         └────────┬────────┘
                  ▼
             processed/
             progress.db
```

---

## State and Persistence

SQLite (`progress.db`) is the sole persistence layer.

| Table | Columns | Purpose |
|---|---|---|
| `processed` | `filename`, `processed_at` | Tracks completed photos; prevents reprocessing |
| `batch_jobs` | `job_name`, `submitted_at`, `status` | Tracks submitted batch jobs for later collection |

The database is created on first run. All writes are idempotent (`INSERT OR IGNORE` / `INSERT OR REPLACE`).

---

## Key Design Decisions

**Why two modes?**
Real-time mode gives immediate feedback and suits small jobs or one-off runs. Batch mode cuts API cost by 50% and removes real-time rate limit pressure — better for large archives where turnaround time is acceptable.

**Why SQLite over file markers?**
A single database file is easier to inspect, query, and back up than scattered `.done` marker files. It also naturally supports the batch job history table.

**Why direct REST calls for batch operations?**
The `google-generativeai` Python SDK does not expose stable high-level methods for batch job lifecycle (submit/poll/collect). REST calls via `requests` keep the integration explicit and easy to update if the API changes.

**Why pre-resize before sending?**
Sending smaller images reduces base64 payload size, lowers token consumption, and speeds up upload — especially relevant for batch mode where all images are encoded into one file.

---

## Technology Stack

| Layer | Library | Version |
|---|---|---|
| CLI | `typer` + `rich` | 0.12 / 13 |
| Image processing | `Pillow` | 10+ |
| Gemini real-time | `google-generativeai` SDK | 0.8+ |
| Gemini batch / REST | `requests` | 2.31+ |
| Configuration | `pydantic-settings` + `pyyaml` | 2 / 6 |
| Retry logic | `tenacity` | 8+ |
| Persistence | `sqlite3` (stdlib) | — |
