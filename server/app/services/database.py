from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import threading

from app.core.config import Settings


class Database:
    def __init__(self, settings: Settings) -> None:
        self._database_path = settings.database_path
        self._lock = threading.RLock()

    def initialize(self) -> None:
        Path(self._database_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    app_version TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    is_active INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS register_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_key TEXT NOT NULL,
                    attempted_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_register_attempts_client_time
                ON register_attempts (client_key, attempted_at);

                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    normalized_url TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL,
                    is_active INTEGER NOT NULL,
                    provider TEXT,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    downloaded_bytes INTEGER,
                    total_bytes INTEGER,
                    speed_bytes_per_sec INTEGER,
                    eta_seconds INTEGER,
                    error_code TEXT,
                    error_message TEXT,
                    user_message TEXT,
                    media_title TEXT,
                    author_handle TEXT,
                    thumbnail_url TEXT,
                    artifact_id TEXT,
                    selected_quality TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT,
                    FOREIGN KEY(device_id) REFERENCES devices(id)
                );
                DROP INDEX IF EXISTS idx_jobs_active_dedupe;
                CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active_dedupe
                ON jobs (dedupe_key) WHERE is_active = 1;

                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
                """
            )
            self._ensure_jobs_schema(conn)
            conn.commit()

    def _ensure_jobs_schema(self, conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        missing_columns = {
            "downloaded_bytes": "INTEGER",
            "total_bytes": "INTEGER",
            "speed_bytes_per_sec": "INTEGER",
            "eta_seconds": "INTEGER",
        }
        for name, column_type in missing_columns.items():
            if name in columns:
                continue
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {column_type}")

    @contextmanager
    def connection(self) -> sqlite3.Connection:
        with self._lock:
            conn = sqlite3.connect(self._database_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA busy_timeout = 30000;")
            try:
                yield conn
            finally:
                conn.close()


def utc_now() -> datetime:
    return datetime.now(tz=UTC)
