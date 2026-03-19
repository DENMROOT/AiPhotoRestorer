import sqlite3
from pathlib import Path


DB_PATH = Path("progress.db")


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed (
                filename TEXT PRIMARY KEY,
                processed_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS batch_jobs (
                job_name TEXT PRIMARY KEY,
                submitted_at TEXT DEFAULT (datetime('now')),
                status TEXT DEFAULT 'pending'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resized (
                filename TEXT PRIMARY KEY,
                resized_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS batch_queued (
                filename TEXT PRIMARY KEY,
                job_name TEXT,
                queued_at TEXT DEFAULT (datetime('now'))
            )
            """
        )


def save_batch_job(job_name: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO batch_jobs (job_name) VALUES (?)", (job_name,)
        )


def update_batch_status(job_name: str, status: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE batch_jobs SET status = ? WHERE job_name = ?", (status, job_name)
        )


def list_batch_jobs() -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT job_name, submitted_at, status FROM batch_jobs ORDER BY submitted_at DESC"
        ).fetchall()
    return [{"job_name": r[0], "submitted_at": r[1], "status": r[2]} for r in rows]


def mark_queued(filenames: list, job_name: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO batch_queued (filename, job_name) VALUES (?, ?)",
            [(f, job_name) for f in filenames],
        )


def get_queued() -> set:
    """Returns filenames queued in a batch job but not yet processed."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT filename FROM batch_queued "
            "WHERE filename NOT IN (SELECT filename FROM processed)"
        ).fetchall()
    return {row[0] for row in rows}


def clear_queued() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM batch_queued")


def clear_processed() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM processed")


def mark_done(filename: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO processed (filename) VALUES (?)", (filename,)
        )


def get_processed() -> set[str]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT filename FROM processed").fetchall()
    return {row[0] for row in rows}


def clear_resized() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM resized")


def mark_resized(filename: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO resized (filename) VALUES (?)", (filename,)
        )


def get_resized() -> set[str]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT filename FROM resized").fetchall()
    return {row[0] for row in rows}
