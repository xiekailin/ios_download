from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.artifacts import router as artifacts_router
from app.api.v1.devices import router as devices_router
from app.api.v1.health import router as health_router
from app.api.v1.jobs import router as jobs_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router)
api_router.include_router(devices_router)
api_router.include_router(jobs_router)
api_router.include_router(artifacts_router)
