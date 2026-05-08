from __future__ import annotations

from datetime import datetime

from app.schemas.common import APIModel


class YouTubeCookieStatusResponse(APIModel):
    is_configured: bool
    file_size: int | None
    updated_at: datetime | None
