from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from app.domain.models import JobStatus
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


class JobResponse(APIModel):
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


class JobsListResponse(APIModel):
    items: list[JobResponse]
