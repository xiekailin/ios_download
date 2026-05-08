from __future__ import annotations

from datetime import datetime

from app.schemas.common import APIModel


class DeleteArtifactResponse(APIModel):
    deleted: bool


class ArtifactResponse(APIModel):
    id: str
    job_id: str
    file_name: str
    mime_type: str
    file_size: int
    duration_seconds: float | None
    width: int | None
    height: int | None
    video_codec: str | None
    audio_codec: str | None
    bitrate_kbps: int | None
    container_format: str | None
    created_at: datetime
