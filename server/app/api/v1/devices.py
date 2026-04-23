from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.deps import AppContainer, get_container, get_current_device
from app.domain.models import Device
from app.schemas.devices import DeviceResponse, RegisterDeviceData, RegisterDeviceRequest
from app.schemas.responses import DataResponse

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post("/register", response_model=DataResponse[RegisterDeviceData])
def register_device(
    payload: RegisterDeviceRequest,
    request: Request,
    app_container: AppContainer = Depends(get_container),
) -> dict[str, RegisterDeviceData]:
    client_host = request.client.host if request.client else "unknown"
    device, token = app_container.device_service.register(payload, client_key=client_host)
    return {
        "data": RegisterDeviceData(
            device_id=device.id,
            access_token=token,
        )
    }


@router.get("/me", response_model=DataResponse[DeviceResponse])
def get_device(device: Device = Depends(get_current_device)) -> dict[str, DeviceResponse]:
    return {"data": DeviceResponse.model_validate(device)}
