from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Platform(StrEnum):
    IOS = "ios"
    MACOS = "macos"


class JobStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RESOLVING = "resolving"
    RESOLVED = "resolved"
    DOWNLOADING = "downloading"
    MUXING = "muxing"
    STORING = "storing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class DeliveryMode(StrEnum):
    DIRECT = "direct"
    DELEGATE_YTDLP = "delegate_ytdlp"


@dataclass(slots=True)
class Device:
    id: str
    name: str
    platform: Platform
    app_version: str
    token_hash: str
    created_at: datetime
    last_seen_at: datetime
    is_active: bool


@dataclass(slots=True)
class Job:
    id: str
    device_id: str
    source_url: str
    normalized_url: str
    provider: str | None
    status: JobStatus
    progress: int
    downloaded_bytes: int | None
    total_bytes: int | None
    speed_bytes_per_sec: int | None
    eta_seconds: int | None
    error_code: str | None
    error_message: str | None
    user_message: str | None
    media_title: str | None
    author_handle: str | None
    thumbnail_url: str | None
    artifact_id: str | None
    selected_quality: str | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


@dataclass(slots=True)
class Artifact:
    id: str
    job_id: str
    file_name: str
    mime_type: str
    storage_path: str
    file_size: int
    created_at: datetime


@dataclass(slots=True)
class ExtractedMedia:
    provider: str
    title: str | None
    author_handle: str | None
    thumbnail_url: str | None
    direct_url: str | None
    direct_url_addresses: tuple[str, ...] = ()
    webpage_url: str = ""
    file_extension: str = "mp4"
    delivery_mode: DeliveryMode = DeliveryMode.DIRECT
