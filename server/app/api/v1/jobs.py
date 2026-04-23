from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import AppContainer, get_container, get_current_device
from app.domain.models import Device
from app.schemas.jobs import CreateJobRequest, JobResponse, JobsListResponse
from app.schemas.responses import DataResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=DataResponse[JobResponse])
def create_job(
    payload: CreateJobRequest,
    device: Device = Depends(get_current_device),
    app_container: AppContainer = Depends(get_container),
) -> dict[str, JobResponse]:
    job = app_container.job_service.create(
        device=device,
        source_url=payload.url,
        preferred_quality=payload.preferred_quality,
    )
    return {"data": JobResponse.model_validate(job)}


@router.get("", response_model=DataResponse[JobsListResponse])
def list_jobs(
    device: Device = Depends(get_current_device),
    app_container: AppContainer = Depends(get_container),
) -> dict[str, JobsListResponse]:
    jobs = app_container.job_service.list_for_device(device)
    return {"data": JobsListResponse(items=[JobResponse.model_validate(job) for job in jobs])}


@router.get("/{job_id}", response_model=DataResponse[JobResponse])
def get_job(
    job_id: str,
    device: Device = Depends(get_current_device),
    app_container: AppContainer = Depends(get_container),
) -> dict[str, JobResponse]:
    job = app_container.job_service.get_owned(job_id, device)
    return {"data": JobResponse.model_validate(job)}


@router.post("/{job_id}/retry", response_model=DataResponse[JobResponse])
def retry_job(
    job_id: str,
    device: Device = Depends(get_current_device),
    app_container: AppContainer = Depends(get_container),
) -> dict[str, JobResponse]:
    job = app_container.job_service.retry(job_id, device)
    return {"data": JobResponse.model_validate(job)}


@router.post("/{job_id}/cancel", response_model=DataResponse[JobResponse])
def cancel_job(
    job_id: str,
    device: Device = Depends(get_current_device),
    app_container: AppContainer = Depends(get_container),
) -> dict[str, JobResponse]:
    job = app_container.job_service.cancel(job_id, device)
    return {"data": JobResponse.model_validate(job)}


@router.delete("/{job_id}", response_model=DataResponse[JobResponse])
def delete_job(
    job_id: str,
    device: Device = Depends(get_current_device),
    app_container: AppContainer = Depends(get_container),
) -> dict[str, JobResponse]:
    job = app_container.job_service.delete(job_id, device)
    return {"data": JobResponse.model_validate(job)}
