from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile

from app.api.deps import AppContainer, get_container, get_current_device
from app.core.errors import AuthorizationError
from app.domain.models import Device, Platform
from app.schemas.responses import DataResponse
from app.schemas.youtube import YouTubeCookieStatusResponse
from app.services.youtube_cookies import YouTubeCookieService, YouTubeCookieStatus

router = APIRouter(prefix="/youtube", tags=["youtube"])


def _response(status: YouTubeCookieStatus) -> YouTubeCookieStatusResponse:
    return YouTubeCookieStatusResponse(
        is_configured=status.is_configured,
        file_size=status.file_size,
        updated_at=status.updated_at,
    )


def _require_cookie_manager(device: Device) -> None:
    if device.platform != Platform.MACOS:
        raise AuthorizationError("youtube cookie management requires macos device")


@router.get("/cookies/status", response_model=DataResponse[YouTubeCookieStatusResponse])
def youtube_cookie_status(
    _: Device = Depends(get_current_device),
    app_container: AppContainer = Depends(get_container),
) -> dict[str, YouTubeCookieStatusResponse]:
    service = YouTubeCookieService(app_container.settings)
    return {"data": _response(service.status())}


@router.post("/cookies", response_model=DataResponse[YouTubeCookieStatusResponse])
async def upload_youtube_cookies(
    file: UploadFile = File(...),
    device: Device = Depends(get_current_device),
    app_container: AppContainer = Depends(get_container),
) -> dict[str, YouTubeCookieStatusResponse]:
    _require_cookie_manager(device)
    content = await file.read(app_container.settings.youtube_cookies_max_bytes + 1)
    service = YouTubeCookieService(app_container.settings)
    return {"data": _response(service.save(content))}


@router.delete("/cookies", response_model=DataResponse[YouTubeCookieStatusResponse])
def delete_youtube_cookies(
    device: Device = Depends(get_current_device),
    app_container: AppContainer = Depends(get_container),
) -> dict[str, YouTubeCookieStatusResponse]:
    _require_cookie_manager(device)
    service = YouTubeCookieService(app_container.settings)
    return {"data": _response(service.delete())}
