from __future__ import annotations

from datetime import datetime
import sqlite3
import uuid

from app.core.errors import ConflictAppError
from app.domain.models import Artifact, Device, Job, JobStatus, Platform
from app.services.database import Database, utc_now

_ACTIVE_JOB_STATUSES = (
    JobStatus.QUEUED,
    JobStatus.RESOLVING,
    JobStatus.RESOLVED,
    JobStatus.DOWNLOADING,
    JobStatus.MUXING,
    JobStatus.STORING,
)

_TERMINAL_JOB_STATUSES = (
    JobStatus.COMPLETED,
    JobStatus.FAILED,
    JobStatus.CANCELED,
)


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


class DeviceRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create(self, *, name: str, platform: Platform, app_version: str, token_hash: str) -> Device:
        now = utc_now()
        device = Device(
            id=str(uuid.uuid4()),
            name=name,
            platform=platform,
            app_version=app_version,
            token_hash=token_hash,
            created_at=now,
            last_seen_at=now,
            is_active=True,
        )
        with self._database.connection() as conn:
            conn.execute(
                """
                INSERT INTO devices (id, name, platform, app_version, token_hash, created_at, last_seen_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    device.id,
                    device.name,
                    device.platform.value,
                    device.app_version,
                    device.token_hash,
                    device.created_at.isoformat(),
                    device.last_seen_at.isoformat(),
                    int(device.is_active),
                ),
            )
            conn.commit()
        return device

    def get_by_token_hash(self, token_hash: str) -> Device | None:
        with self._database.connection() as conn:
            row = conn.execute(
                "SELECT * FROM devices WHERE token_hash = ? AND is_active = 1",
                (token_hash,),
            ).fetchone()
        return _row_to_device(row) if row else None

    def touch(self, device_id: str) -> None:
        with self._database.connection() as conn:
            conn.execute(
                "UPDATE devices SET last_seen_at = ? WHERE id = ?",
                (utc_now().isoformat(), device_id),
            )
            conn.commit()


class RegisterAttemptRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def check_and_record(self, client_key: str, *, window_start: datetime, limit: int) -> bool:
        with self._database.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM register_attempts WHERE attempted_at < ?",
                (window_start.isoformat(),),
            )
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM register_attempts WHERE client_key = ? AND attempted_at >= ?",
                (client_key, window_start.isoformat()),
            ).fetchone()
            count = int(row["count"])
            if count >= limit:
                conn.rollback()
                return False
            conn.execute(
                "INSERT INTO register_attempts (client_key, attempted_at) VALUES (?, ?)",
                (client_key, utc_now().isoformat()),
            )
            conn.commit()
        return True


class JobRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create(self, *, device_id: str, source_url: str, normalized_url: str, selected_quality: str | None) -> Job:
        now = utc_now()
        job = Job(
            id=str(uuid.uuid4()),
            device_id=device_id,
            source_url=source_url,
            normalized_url=normalized_url,
            provider=None,
            status=JobStatus.QUEUED,
            progress=0,
            downloaded_bytes=None,
            total_bytes=None,
            speed_bytes_per_sec=None,
            eta_seconds=None,
            error_code=None,
            error_message=None,
            user_message=None,
            media_title=None,
            author_handle=None,
            thumbnail_url=None,
            artifact_id=None,
            selected_quality=selected_quality,
            created_at=now,
            updated_at=now,
            finished_at=None,
        )
        dedupe_key = f"{device_id}:{normalized_url}"
        try:
            with self._database.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO jobs (
                        id, device_id, source_url, normalized_url, dedupe_key, is_active, provider, status, progress,
                        downloaded_bytes, total_bytes, speed_bytes_per_sec, eta_seconds,
                        error_code, error_message, user_message, media_title, author_handle,
                        thumbnail_url, artifact_id, selected_quality, created_at, updated_at, finished_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job.id,
                        job.device_id,
                        job.source_url,
                        job.normalized_url,
                        dedupe_key,
                        1,
                        job.provider,
                        job.status.value,
                        job.progress,
                        job.downloaded_bytes,
                        job.total_bytes,
                        job.speed_bytes_per_sec,
                        job.eta_seconds,
                        job.error_code,
                        job.error_message,
                        job.user_message,
                        job.media_title,
                        job.author_handle,
                        job.thumbnail_url,
                        job.artifact_id,
                        job.selected_quality,
                        job.created_at.isoformat(),
                        job.updated_at.isoformat(),
                        job.finished_at.isoformat() if job.finished_at else None,
                    ),
                )
                conn.commit()
        except sqlite3.IntegrityError as exc:
            active_job = self.get_active_for_device_url(device_id, normalized_url)
            if active_job is not None:
                raise ConflictAppError("active job already exists", "相同链接已有任务在处理中。") from exc
            raise
        return job

    def get(self, job_id: str) -> Job | None:
        with self._database.connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def get_active_for_device_url(self, device_id: str, normalized_url: str) -> Job | None:
        placeholders = ",".join("?" for _ in _ACTIVE_JOB_STATUSES)
        params = (device_id, normalized_url, *[status.value for status in _ACTIVE_JOB_STATUSES])
        with self._database.connection() as conn:
            row = conn.execute(
                f"SELECT * FROM jobs WHERE device_id = ? AND normalized_url = ? AND status IN ({placeholders}) ORDER BY created_at DESC LIMIT 1",
                params,
            ).fetchone()
        return _row_to_job(row) if row else None

    def list_for_device(self, device_id: str) -> list[Job]:
        with self._database.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE device_id = ? ORDER BY created_at DESC",
                (device_id,),
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def transition_status(
        self,
        job_id: str,
        *,
        from_statuses: set[JobStatus],
        to_status: JobStatus,
        progress: int,
        provider: str | None = None,
        downloaded_bytes: int | None = None,
        total_bytes: int | None = None,
        speed_bytes_per_sec: int | None = None,
        eta_seconds: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        user_message: str | None = None,
        media_title: str | None = None,
        author_handle: str | None = None,
        thumbnail_url: str | None = None,
        artifact_id: str | None = None,
        finished_at: datetime | None = None,
    ) -> bool:
        from_values = tuple(status.value for status in from_statuses)
        placeholders = ",".join("?" for _ in from_values)
        is_active = 0 if to_status in _TERMINAL_JOB_STATUSES else 1
        with self._database.connection() as conn:
            cursor = conn.execute(
                f"""
                UPDATE jobs
                SET status = ?, is_active = ?, progress = ?, provider = COALESCE(?, provider),
                    downloaded_bytes = ?, total_bytes = ?, speed_bytes_per_sec = ?, eta_seconds = ?,
                    error_code = ?, error_message = ?, user_message = ?,
                    media_title = COALESCE(?, media_title),
                    author_handle = COALESCE(?, author_handle),
                    thumbnail_url = COALESCE(?, thumbnail_url),
                    artifact_id = COALESCE(?, artifact_id),
                    updated_at = ?, finished_at = ?
                WHERE id = ? AND status IN ({placeholders})
                """,
                (
                    to_status.value,
                    is_active,
                    progress,
                    provider,
                    downloaded_bytes,
                    total_bytes,
                    speed_bytes_per_sec,
                    eta_seconds,
                    error_code,
                    error_message,
                    user_message,
                    media_title,
                    author_handle,
                    thumbnail_url,
                    artifact_id,
                    utc_now().isoformat(),
                    finished_at.isoformat() if finished_at else None,
                    job_id,
                    *from_values,
                ),
            )
            conn.commit()
        return cursor.rowcount > 0

    def update_status(
        self,
        job_id: str,
        *,
        status: JobStatus,
        progress: int,
        provider: str | None = None,
        downloaded_bytes: int | None = None,
        total_bytes: int | None = None,
        speed_bytes_per_sec: int | None = None,
        eta_seconds: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        user_message: str | None = None,
        media_title: str | None = None,
        author_handle: str | None = None,
        thumbnail_url: str | None = None,
        artifact_id: str | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        is_active = 0 if status in _TERMINAL_JOB_STATUSES else 1
        with self._database.connection() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, is_active = ?, progress = ?, provider = COALESCE(?, provider),
                    downloaded_bytes = ?, total_bytes = ?, speed_bytes_per_sec = ?, eta_seconds = ?,
                    error_code = ?, error_message = ?, user_message = ?,
                    media_title = COALESCE(?, media_title),
                    author_handle = COALESCE(?, author_handle),
                    thumbnail_url = COALESCE(?, thumbnail_url),
                    artifact_id = COALESCE(?, artifact_id),
                    updated_at = ?, finished_at = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    is_active,
                    progress,
                    provider,
                    downloaded_bytes,
                    total_bytes,
                    speed_bytes_per_sec,
                    eta_seconds,
                    error_code,
                    error_message,
                    user_message,
                    media_title,
                    author_handle,
                    thumbnail_url,
                    artifact_id,
                    utc_now().isoformat(),
                    finished_at.isoformat() if finished_at else None,
                    job_id,
                ),
            )
            conn.commit()

    def delete(self, job_id: str) -> None:
        with self._database.connection() as conn:
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            conn.commit()


class ArtifactRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create(self, *, job_id: str, file_name: str, mime_type: str, storage_path: str, file_size: int) -> Artifact:
        artifact = Artifact(
            id=str(uuid.uuid4()),
            job_id=job_id,
            file_name=file_name,
            mime_type=mime_type,
            storage_path=storage_path,
            file_size=file_size,
            created_at=utc_now(),
        )
        with self._database.connection() as conn:
            conn.execute(
                """
                INSERT INTO artifacts (id, job_id, file_name, mime_type, storage_path, file_size, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.id,
                    artifact.job_id,
                    artifact.file_name,
                    artifact.mime_type,
                    artifact.storage_path,
                    artifact.file_size,
                    artifact.created_at.isoformat(),
                ),
            )
            conn.commit()
        return artifact

    def get(self, artifact_id: str) -> Artifact | None:
        with self._database.connection() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
        return _row_to_artifact(row) if row else None

    def delete(self, artifact_id: str) -> None:
        with self._database.connection() as conn:
            conn.execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))
            conn.commit()


def _row_to_device(row: sqlite3.Row) -> Device:
    return Device(
        id=row["id"],
        name=row["name"],
        platform=Platform(row["platform"]),
        app_version=row["app_version"],
        token_hash=row["token_hash"],
        created_at=datetime.fromisoformat(row["created_at"]),
        last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
        is_active=bool(row["is_active"]),
    )


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        device_id=row["device_id"],
        source_url=row["source_url"],
        normalized_url=row["normalized_url"],
        provider=row["provider"],
        status=JobStatus(row["status"]),
        progress=row["progress"],
        downloaded_bytes=row["downloaded_bytes"],
        total_bytes=row["total_bytes"],
        speed_bytes_per_sec=row["speed_bytes_per_sec"],
        eta_seconds=row["eta_seconds"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        user_message=row["user_message"],
        media_title=row["media_title"],
        author_handle=row["author_handle"],
        thumbnail_url=row["thumbnail_url"],
        artifact_id=row["artifact_id"],
        selected_quality=row["selected_quality"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        finished_at=_parse_datetime(row["finished_at"]),
    )


def _row_to_artifact(row: sqlite3.Row) -> Artifact:
    return Artifact(
        id=row["id"],
        job_id=row["job_id"],
        file_name=row["file_name"],
        mime_type=row["mime_type"],
        storage_path=row["storage_path"],
        file_size=row["file_size"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
