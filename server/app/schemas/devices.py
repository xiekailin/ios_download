from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.domain.models import Platform
from app.schemas.common import APIModel


class RegisterDeviceRequest(APIModel):
    device_name: str = Field(min_length=1, max_length=100)
    platform: Platform
    app_version: str = Field(min_length=1, max_length=50)
    bootstrap_code: str | None = Field(default=None, min_length=1, max_length=100)


class RegisterDeviceData(APIModel):
    device_id: str
    access_token: str
    token_type: str = "bearer"


class DeviceResponse(APIModel):
    id: str
    name: str
    platform: Platform
    app_version: str
    created_at: datetime
    last_seen_at: datetime
    is_active: bool
