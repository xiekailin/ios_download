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
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class DeliveryMode(StrEnum):
    DIRECT = "direct"
    DELEGATE_YTDLP = "delegate_ytdlp"


class JobType(StrEnum):
    DOWNLOAD = "download"
    AUDIO_DOWNLOAD = "audio_download"
    AUDIO_SEPARATION = "audio_separation"


class ArtifactRole(StrEnum):
    MEDIA = "media"
    VOCALS = "vocals"
    ACCOMPANIMENT = "accompaniment"


@dataclass(slots=True)
class JobEvent:
    id: int
    job_id: str
    level: str
    event_type: str
    message: str
    created_at: datetime


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
    job_type: JobType
    provider: str | None
    status: JobStatus
    progress: int
    priority: int
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
    thumbnail_path: str | None
    role: ArtifactRole
    file_size: int
    duration_seconds: float | None
    width: int | None
    height: int | None
    video_codec: str | None
    audio_codec: str | None
    bitrate_kbps: int | None
    container_format: str | None
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
