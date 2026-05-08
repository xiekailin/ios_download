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
                    job_type TEXT NOT NULL DEFAULT 'download',
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
                    thumbnail_path TEXT,
                    role TEXT NOT NULL DEFAULT 'media',
                    file_size INTEGER NOT NULL,
                    duration_seconds REAL,
                    width INTEGER,
                    height INTEGER,
                    video_codec TEXT,
                    audio_codec TEXT,
                    bitrate_kbps INTEGER,
                    container_format TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                );

                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    level TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
                """
            )
            self._ensure_jobs_schema(conn)
            self._migrate_job_dedupe_keys(conn)
            self._ensure_artifacts_schema(conn)
            self._ensure_indexes(conn)
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
            "job_type": "TEXT NOT NULL DEFAULT 'download'",
        }
        for name, column_type in missing_columns.items():
            if name in columns:
                continue
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {column_type}")

    def _migrate_job_dedupe_keys(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            UPDATE jobs
            SET dedupe_key = device_id || ':' || job_type || ':' || normalized_url
            WHERE dedupe_key = device_id || ':' || normalized_url
            """
        )

    def _ensure_indexes(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_jobs_completed_lookup
            ON jobs (device_id, normalized_url, job_type, status, finished_at DESC, updated_at DESC, created_at DESC)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_job_events_job_id_id
            ON job_events (job_id, id)
            """
        )

    def _ensure_artifacts_schema(self, conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(artifacts)").fetchall()
        }
        missing_columns = {
            "role": "TEXT NOT NULL DEFAULT 'media'",
            "duration_seconds": "REAL",
            "width": "INTEGER",
            "height": "INTEGER",
            "video_codec": "TEXT",
            "audio_codec": "TEXT",
            "bitrate_kbps": "INTEGER",
            "container_format": "TEXT",
            "thumbnail_path": "TEXT",
        }
        for name, column_type in missing_columns.items():
            if name in columns:
                continue
            conn.execute(f"ALTER TABLE artifacts ADD COLUMN {name} {column_type}")

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
