from __future__ import annotations

from datetime import datetime

from app.schemas.common import APIModel


class ArtifactResponse(APIModel):
    id: str
    job_id: str
    file_name: str
    mime_type: str
    file_size: int
    created_at: datetime
