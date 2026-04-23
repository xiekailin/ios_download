from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import AppContainer, get_container

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health(app_container: AppContainer = Depends(get_container)) -> dict[str, object]:
    return {
        "data": {
            "status": "ok",
            "app_name": app_container.settings.app_name,
            "environment": app_container.settings.env,
        }
    }
