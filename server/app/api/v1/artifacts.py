from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.api.deps import AppContainer, get_container, get_current_device
from app.domain.models import Device

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("/{artifact_id}/download")
def download_artifact(
    artifact_id: str,
    device: Device = Depends(get_current_device),
    app_container: AppContainer = Depends(get_container),
) -> FileResponse:
    file_name, file_path, mime_type = app_container.artifact_service.get_owned_artifact_path(artifact_id, device)
    return FileResponse(path=file_path, media_type=mime_type, filename=file_name)
