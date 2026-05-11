from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile

from app.api.deps import AppContainer, get_container, get_current_device
from app.core.errors import ValidationAppError

from app.domain.models import Device
from app.schemas.jobs import ArtifactSummaryResponse, CreateJobRequest, DeleteHistoryResponse, JobArtifactsResponse, JobLogEventResponse, JobLogsResponse, JobPreviewResponse, JobResponse, JobsListResponse, PreviewJobRequest, UpdateJobPriorityRequest
from app.schemas.responses import DataResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/preview", response_model=DataResponse[JobPreviewResponse])
def preview_job(
    payload: PreviewJobRequest,
    device: Device = Depends(get_current_device),
    app_container: AppContainer = Depends(get_container),
) -> dict[str, JobPreviewResponse]:
    preview = app_container.job_service.preview_url(device=device, source_url=payload.url, job_type=payload.job_type)
    return {"data": JobPreviewResponse.model_validate(preview)}


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


@router.post("/audio-download", response_model=DataResponse[JobResponse])
def create_audio_download_job(
    payload: CreateJobRequest,
    device: Device = Depends(get_current_device),
    app_container: AppContainer = Depends(get_container),
) -> dict[str, JobResponse]:
    job = app_container.job_service.create_audio_download(
        device=device,
        source_url=payload.url,
    )
    return {"data": JobResponse.model_validate(job)}


@router.post("/audio-separation", response_model=DataResponse[JobResponse])
async def create_audio_separation_job(
    file: UploadFile = File(...),
    device: Device = Depends(get_current_device),
    app_container: AppContainer = Depends(get_container),
) -> dict[str, JobResponse]:
    content = await file.read(app_container.settings.audio_upload_max_bytes + 1)
    if len(content) > app_container.settings.audio_upload_max_bytes:
        raise ValidationAppError("audio file is too large", "音频文件太大。")
    job = app_container.job_service.create_audio_separation(
        device=device,
        file_name=file.filename or "audio",
        content=content,
    )
    return {"data": JobResponse.model_validate(job)}


@router.get("", response_model=DataResponse[JobsListResponse])
def list_jobs(
    device: Device = Depends(get_current_device),
    app_container: AppContainer = Depends(get_container),
) -> dict[str, JobsListResponse]:
    jobs = app_container.job_service.list_for_device(device)
    return {"data": JobsListResponse(items=[JobResponse.model_validate(job) for job in jobs])}


@router.delete("/history", response_model=DataResponse[DeleteHistoryResponse])
def delete_history(
    device: Device = Depends(get_current_device),
    app_container: AppContainer = Depends(get_container),
) -> dict[str, DeleteHistoryResponse]:
    result = app_container.job_service.delete_history(device)
    return {
        "data": DeleteHistoryResponse(
            deleted_count=result.deleted_count,
            skipped_active_count=result.skipped_active_count,
            deleted_job_ids=result.deleted_job_ids,
        )
    }


@router.post("/batch-retry", response_model=DataResponse[JobsListResponse])
def batch_retry_jobs(
    device: Device = Depends(get_current_device),
    app_container: AppContainer = Depends(get_container),
) -> dict[str, JobsListResponse]:
    jobs = app_container.job_service.retry_many(device)
    return {"data": JobsListResponse(items=[JobResponse.model_validate(job) for job in jobs])}


@router.get("/{job_id}/artifacts", response_model=DataResponse[JobArtifactsResponse])
def list_job_artifacts(
    job_id: str,
    device: Device = Depends(get_current_device),
    app_container: AppContainer = Depends(get_container),
) -> dict[str, JobArtifactsResponse]:
    artifacts = app_container.job_service.list_artifacts(job_id, device)
    return {
        "data": JobArtifactsResponse(
            items=[
                ArtifactSummaryResponse(
                    id=artifact.id,
                    job_id=artifact.job_id,
                    file_name=artifact.file_name,
                    mime_type=artifact.mime_type,
                    role=artifact.role,
                    file_size=artifact.file_size,
                    local_path=app_container.job_service.safe_local_artifact_path(artifact.storage_path, device),
                    thumbnail_local_path=app_container.job_service.safe_local_artifact_path(artifact.thumbnail_path, device) if artifact.thumbnail_path else None,
                    duration_seconds=artifact.duration_seconds,
                    width=artifact.width,
                    height=artifact.height,
                    video_codec=artifact.video_codec,
                    audio_codec=artifact.audio_codec,
                    bitrate_kbps=artifact.bitrate_kbps,
                    container_format=artifact.container_format,
                    created_at=artifact.created_at,
                )
                for artifact in artifacts
            ]
        )
    }


@router.get("/{job_id}/logs", response_model=DataResponse[JobLogsResponse])
def list_job_logs(
    job_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    after_id: int | None = Query(default=None, ge=0),
    device: Device = Depends(get_current_device),
    app_container: AppContainer = Depends(get_container),
) -> dict[str, JobLogsResponse]:
    events = app_container.job_service.list_events(job_id, device, limit=limit, after_id=after_id)
    return {
        "data": JobLogsResponse(
            job_id=job_id,
            items=[JobLogEventResponse.model_validate(event) for event in events],
        )
    }


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


@router.post("/{job_id}/pause", response_model=DataResponse[JobResponse])
def pause_job(
    job_id: str,
    device: Device = Depends(get_current_device),
    app_container: AppContainer = Depends(get_container),
) -> dict[str, JobResponse]:
    job = app_container.job_service.pause(job_id, device)
    return {"data": JobResponse.model_validate(job)}


@router.post("/{job_id}/resume", response_model=DataResponse[JobResponse])
def resume_job(
    job_id: str,
    device: Device = Depends(get_current_device),
    app_container: AppContainer = Depends(get_container),
) -> dict[str, JobResponse]:
    job = app_container.job_service.resume(job_id, device)
    return {"data": JobResponse.model_validate(job)}


@router.post("/{job_id}/priority", response_model=DataResponse[JobResponse])
def set_job_priority(
    job_id: str,
    payload: UpdateJobPriorityRequest,
    device: Device = Depends(get_current_device),
    app_container: AppContainer = Depends(get_container),
) -> dict[str, JobResponse]:
    job = app_container.job_service.set_priority(job_id, device, payload.priority)
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
