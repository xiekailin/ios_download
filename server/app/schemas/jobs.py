from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from app.domain.models import ArtifactRole, JobStatus, JobType
from app.schemas.common import APIModel


class CreateJobRequest(APIModel):
    url: str = Field(min_length=1, max_length=1000)
    preferred_quality: str | None = Field(default=None, max_length=30)
    auto_download: bool = True

    @field_validator("url")
    @classmethod
    def strip_url(cls, value: str) -> str:
        return value.strip()


class RetryJobRequest(APIModel):
    pass


class PreviewJobRequest(APIModel):
    url: str = Field(min_length=1, max_length=1000)
    job_type: JobType = JobType.DOWNLOAD

    @field_validator("url")
    @classmethod
    def strip_url(cls, value: str) -> str:
        return value.strip()


class JobPreviewResponse(APIModel):
    source_url: str
    normalized_url: str
    provider: str
    title: str | None
    author_handle: str | None
    thumbnail_url: str | None
    file_extension: str
    recommended_job_type: JobType
    existing_job_id: str | None
    existing_artifact_id: str | None
    existing_file_name: str | None
    existing_local_path: str | None
    can_reuse_existing: bool


class JobResponse(APIModel):
    id: str
    device_id: str
    source_url: str
    normalized_url: str
    job_type: JobType
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


class ArtifactSummaryResponse(APIModel):
    id: str
    job_id: str
    file_name: str
    mime_type: str
    role: ArtifactRole
    file_size: int
    local_path: str | None = None
    thumbnail_local_path: str | None = None
    duration_seconds: float | None
    width: int | None
    height: int | None
    video_codec: str | None
    audio_codec: str | None
    bitrate_kbps: int | None
    container_format: str | None
    created_at: datetime


class JobArtifactsResponse(APIModel):
    items: list[ArtifactSummaryResponse]


class JobLogEventResponse(APIModel):
    id: int
    job_id: str
    level: str
    event_type: str
    message: str
    created_at: datetime


class JobLogsResponse(APIModel):
    job_id: str
    items: list[JobLogEventResponse]


class JobsListResponse(APIModel):
    items: list[JobResponse]


class DeleteHistoryResponse(APIModel):
    deleted_count: int
    skipped_active_count: int
    deleted_job_ids: list[str]
