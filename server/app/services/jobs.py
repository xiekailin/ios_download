from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
import threading

from app.core.config import Settings
from app.core.errors import ConflictAppError, NotFoundAppError
from app.domain.models import Device, Job, JobStatus
from app.services.repositories import ArtifactRepository, JobRepository
from app.services.url_tools import normalize_source_url
from app.workers.download_job_worker import DownloadJobWorker

_ACTIVE_JOB_STATUSES = {
    JobStatus.QUEUED,
    JobStatus.RESOLVING,
    JobStatus.RESOLVED,
    JobStatus.DOWNLOADING,
    JobStatus.MUXING,
    JobStatus.STORING,
}


@dataclass(slots=True)
class BackgroundJobRunner:
    worker: DownloadJobWorker
    max_jobs: int
    enabled: bool = True
    _executor: ThreadPoolExecutor = field(init=False)
    _lock: threading.Lock = field(init=False)
    _running_job_ids: set[str] = field(init=False)

    def __post_init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max(1, self.max_jobs), thread_name_prefix="xdl-worker")
        self._lock = threading.Lock()
        self._running_job_ids = set()

    def dispatch(self, job_id: str) -> bool:
        if not self.enabled:
            return False
        with self._lock:
            if job_id in self._running_job_ids:
                return False
            self._running_job_ids.add(job_id)
        self._executor.submit(self._run_job, job_id)
        return True

    def _run_job(self, job_id: str) -> None:
        try:
            self.worker.run(job_id)
        finally:
            with self._lock:
                self._running_job_ids.discard(job_id)

    def close(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=not wait)


class JobService:
    def __init__(
        self,
        repository: JobRepository,
        runner: BackgroundJobRunner,
        artifacts: ArtifactRepository,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._runner = runner
        self._artifacts = artifacts
        self._settings = settings

    def close(self) -> None:
        self._runner.close(wait=True)

    def create(self, *, device: Device, source_url: str, preferred_quality: str | None) -> Job:
        normalized_url = normalize_source_url(source_url)
        active_job = self._repository.get_active_for_device_url(device.id, normalized_url)
        if active_job is not None:
            return active_job
        try:
            job = self._repository.create(
                device_id=device.id,
                source_url=source_url,
                normalized_url=normalized_url,
                selected_quality=preferred_quality,
            )
        except ConflictAppError as exc:
            active_job = self._repository.get_active_for_device_url(device.id, normalized_url)
            if active_job is None:
                raise ConflictAppError("active job state changed", "相同链接已有任务在处理中。") from exc
            return active_job
        self._runner.dispatch(job.id)
        return self._repository.get(job.id) or job

    def list_for_device(self, device: Device) -> list[Job]:
        return self._repository.list_for_device(device.id)

    def get_owned(self, job_id: str, device: Device) -> Job:
        job = self._repository.get(job_id)
        if job is None:
            raise NotFoundAppError("job")
        if job.device_id != device.id:
            raise NotFoundAppError("job")
        return job

    def retry(self, job_id: str, device: Device) -> Job:
        job = self.get_owned(job_id, device)
        if job.status not in {JobStatus.FAILED, JobStatus.CANCELED}:
            raise ConflictAppError("job is not retryable", "只有失败或已取消的任务才能重试。")
        self._repository.update_status(
            job.id,
            status=JobStatus.QUEUED,
            progress=0,
            downloaded_bytes=None,
            total_bytes=None,
            speed_bytes_per_sec=None,
            eta_seconds=None,
            error_code=None,
            error_message=None,
            user_message=None,
            finished_at=None,
        )
        self._runner.dispatch(job.id)
        return self.get_owned(job.id, device)

    def cancel(self, job_id: str, device: Device) -> Job:
        job = self.get_owned(job_id, device)
        if job.status not in _ACTIVE_JOB_STATUSES:
            raise ConflictAppError("job is not cancelable", "当前任务不能取消。")
        self._repository.update_status(
            job.id,
            status=JobStatus.CANCELED,
            progress=job.progress,
            downloaded_bytes=job.downloaded_bytes,
            total_bytes=job.total_bytes,
            speed_bytes_per_sec=job.speed_bytes_per_sec,
            eta_seconds=job.eta_seconds,
        )
        return self.get_owned(job.id, device)

    def delete(self, job_id: str, device: Device) -> Job:
        job = self.get_owned(job_id, device)
        if job.status in _ACTIVE_JOB_STATUSES:
            raise ConflictAppError("job is not deletable", "处理中任务不能删除。")
        if job.artifact_id:
            artifact = self._artifacts.get(job.artifact_id)
            if artifact is not None and artifact.job_id == job.id:
                self._delete_artifact_file(artifact.storage_path)
                self._artifacts.delete(artifact.id)
        self._repository.delete(job.id)
        return job

    def _delete_artifact_file(self, storage_path: str) -> None:
        file_path = Path(storage_path).resolve()
        artifacts_root = self._settings.artifacts_dir.resolve()
        try:
            file_path.relative_to(artifacts_root)
        except ValueError:
            return
        if file_path.exists() and file_path.is_file():
            file_path.unlink()
