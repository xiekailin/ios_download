from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
import re
import threading
import uuid

from app.core.config import Settings
from app.core.errors import ConflictAppError, NotFoundAppError
from app.domain.models import Device, Job, JobEvent, JobStatus, JobType, Platform
from app.extractors.selector import ProviderSelector
from app.services.repositories import ArtifactRepository, JobEventRepository, JobRepository
from app.services.url_tools import normalize_extraction_source_url, normalize_source_url
from app.workers.download_job_worker import DownloadJobWorker

_AUDIO_INPUT_INVALID_CHARS_RE = re.compile(r"[\x00-\x1f\\/:*?\"<>|]+")
_ALLOWED_AUDIO_EXTENSIONS = frozenset({"mp3", "wav", "m4a", "aac", "flac"})
_ACTIVE_JOB_STATUSES = {
    JobStatus.QUEUED,
    JobStatus.RESOLVING,
    JobStatus.RESOLVED,
    JobStatus.DOWNLOADING,
    JobStatus.MUXING,
    JobStatus.STORING,
}


@dataclass(slots=True)
class DeleteHistoryResult:
    deleted_count: int
    skipped_active_count: int
    deleted_job_ids: list[str]


@dataclass(slots=True)
class JobPreview:
    source_url: str
    normalized_url: str
    provider: str
    title: str | None
    author_handle: str | None
    thumbnail_url: str | None
    file_extension: str
    recommended_job_type: JobType
    existing_job_id: str | None
    existing_artifact_id: str | None
    existing_file_name: str | None
    existing_local_path: str | None
    can_reuse_existing: bool


@dataclass(slots=True)
class BackgroundJobRunner:
    worker: object
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
        events: JobEventRepository,
        settings: Settings,
        selector: ProviderSelector | None = None,
    ) -> None:
        self._repository = repository
        self._runner = runner
        self._artifacts = artifacts
        self._events = events
        self._settings = settings
        self._selector = selector

    def close(self) -> None:
        self._runner.close(wait=True)

    def create(self, *, device: Device, source_url: str, preferred_quality: str | None) -> Job:
        return self._create_url_job(
            device=device,
            source_url=source_url,
            preferred_quality=preferred_quality,
            job_type=JobType.DOWNLOAD,
        )

    def create_audio_download(self, *, device: Device, source_url: str) -> Job:
        return self._create_url_job(
            device=device,
            source_url=source_url,
            preferred_quality=None,
            job_type=JobType.AUDIO_DOWNLOAD,
        )

    def preview_url(self, *, device: Device, source_url: str, job_type: JobType = JobType.DOWNLOAD) -> JobPreview:
        if self._selector is None:
            raise NotFoundAppError("preview provider")
        normalized_url = normalize_source_url(source_url)
        extracted = self._selector.extract(normalize_extraction_source_url(source_url))
        existing_job = self._repository.get_latest_completed_for_device_url(device.id, normalized_url, job_type=job_type)
        existing_artifact_id: str | None = None
        existing_file_name: str | None = None
        existing_local_path: str | None = None
        if existing_job is not None and existing_job.artifact_id is not None:
            artifact = self._artifacts.get(existing_job.artifact_id)
            if artifact is not None and artifact.job_id == existing_job.id:
                existing_artifact_id = artifact.id
                existing_file_name = artifact.file_name
                existing_local_path = self.safe_local_artifact_path(artifact.storage_path, device)
        return JobPreview(
            source_url=source_url,
            normalized_url=normalized_url,
            provider=extracted.provider,
            title=extracted.title,
            author_handle=extracted.author_handle,
            thumbnail_url=extracted.thumbnail_url,
            file_extension=extracted.file_extension,
            recommended_job_type=job_type,
            existing_job_id=existing_job.id if existing_job else None,
            existing_artifact_id=existing_artifact_id,
            existing_file_name=existing_file_name,
            existing_local_path=existing_local_path,
            can_reuse_existing=existing_local_path is not None,
        )

    def _create_url_job(self, *, device: Device, source_url: str, preferred_quality: str | None, job_type: JobType) -> Job:
        normalized_url = normalize_source_url(source_url)
        active_job = self._repository.get_active_for_device_url(device.id, normalized_url, job_type=job_type)
        if active_job is not None:
            return active_job
        try:
            job = self._repository.create(
                device_id=device.id,
                source_url=source_url,
                normalized_url=normalized_url,
                selected_quality=preferred_quality,
                job_type=job_type,
            )
        except ConflictAppError as exc:
            active_job = self._repository.get_active_for_device_url(device.id, normalized_url, job_type=job_type)
            if active_job is None:
                raise ConflictAppError("active job state changed", "相同链接已有任务在处理中。") from exc
            return active_job
        self.record_event(job.id, level="info", event_type="queued", message="任务已加入队列")
        self._runner.dispatch(job.id)
        return self._repository.get(job.id) or job

    def create_audio_separation(self, *, device: Device, file_name: str, content: bytes) -> Job:
        ext = file_name.rsplit(".", maxsplit=1)[-1].lower() if "." in file_name else ""
        if ext not in _ALLOWED_AUDIO_EXTENSIONS:
            from app.core.errors import ValidationAppError

            raise ValidationAppError("unsupported audio file", "请选择 mp3、wav、m4a、aac 或 flac 音频文件。")
        if len(content) > self._settings.audio_upload_max_bytes:
            from app.core.errors import ValidationAppError

            raise ValidationAppError("audio file is too large", "音频文件太大。")
        self._settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
        stem = self._safe_upload_stem(file_name.rsplit(".", maxsplit=1)[0])
        input_path = self._settings.artifacts_dir / f"{device.id}-{uuid.uuid4().hex}.input.{ext}"
        input_path.write_bytes(content)
        job = self._repository.create(
            device_id=device.id,
            source_url=f"upload:{file_name}",
            normalized_url=f"file:{input_path}",
            selected_quality=None,
            job_type=JobType.AUDIO_SEPARATION,
            media_title=stem,
        )
        self.record_event(job.id, level="info", event_type="queued", message="任务已加入队列")
        self._runner.dispatch(job.id)
        return self._repository.get(job.id) or job

    def list_artifacts(self, job_id: str, device: Device):
        self.get_owned(job_id, device)
        return self._artifacts.list_for_job(job_id)

    def record_event(self, job_id: str, *, level: str, event_type: str, message: str) -> JobEvent:
        return self._events.create(job_id=job_id, level=level, event_type=event_type, message=message)

    def list_events(self, job_id: str, device: Device | None = None, *, limit: int = 200, after_id: int | None = None) -> list[JobEvent]:
        if device is not None:
            self.get_owned(job_id, device)
        return self._events.list_for_job(job_id, limit=limit, after_id=after_id)

    def delete_artifact(self, artifact_id: str, device: Device) -> bool:
        artifact = self._artifacts.get(artifact_id)
        if artifact is None:
            raise NotFoundAppError("artifact")
        job = self.get_owned(artifact.job_id, device)
        if job.status in _ACTIVE_JOB_STATUSES:
            raise ConflictAppError("job is active", "处理中任务不能删除源文件。")
        self._validate_artifact_file_path(artifact.storage_path)
        if artifact.thumbnail_path:
            self._validate_artifact_file_path(artifact.thumbnail_path)
        self._delete_artifact_file(artifact.storage_path)
        if artifact.thumbnail_path:
            self._delete_artifact_file(artifact.thumbnail_path)
        self._artifacts.delete(artifact.id)
        self._repository.clear_artifact(job.id, artifact.id)
        return True

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
        active_job = self._repository.get_active_for_device_url(device.id, job.normalized_url, job_type=job.job_type)
        if active_job is not None and active_job.id != job.id:
            return active_job
        self.record_event(job.id, level="info", event_type="retry", message="任务重新加入队列")
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
        self.record_event(job.id, level="warning", event_type="canceled", message="任务已取消")
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
        self._validate_terminal_job_files_can_be_deleted(job)
        self._delete_terminal_job_files_and_record(job)
        return job

    def delete_history(self, device: Device) -> DeleteHistoryResult:
        jobs = self._repository.list_for_device(device.id)
        terminal_jobs = [job for job in jobs if job.status not in _ACTIVE_JOB_STATUSES]
        for job in terminal_jobs:
            self._validate_terminal_job_files_can_be_deleted(job)
        deleted_job_ids: list[str] = []
        for job in terminal_jobs:
            self._delete_terminal_job_files_and_record(job)
            deleted_job_ids.append(job.id)
        return DeleteHistoryResult(
            deleted_count=len(deleted_job_ids),
            skipped_active_count=len(jobs) - len(terminal_jobs),
            deleted_job_ids=deleted_job_ids,
        )

    def _validate_terminal_job_files_can_be_deleted(self, job: Job) -> None:
        for artifact in self._artifacts.list_for_job(job.id):
            self._validate_artifact_file_path(artifact.storage_path)
            if artifact.thumbnail_path:
                self._validate_artifact_file_path(artifact.thumbnail_path)
        if job.job_type == JobType.AUDIO_SEPARATION and job.normalized_url.startswith("file:"):
            self._validate_artifact_file_path(job.normalized_url.removeprefix("file:"))

    def _delete_terminal_job_files_and_record(self, job: Job) -> None:
        for artifact in self._artifacts.list_for_job(job.id):
            self._delete_artifact_file(artifact.storage_path)
            if artifact.thumbnail_path:
                self._delete_artifact_file(artifact.thumbnail_path)
            self._artifacts.delete(artifact.id)
        self._delete_audio_input_file(job)
        self._repository.delete(job.id)

    def _safe_upload_stem(self, value: str) -> str:
        cleaned = _AUDIO_INPUT_INVALID_CHARS_RE.sub(" ", value).strip().strip(".")
        return cleaned[:120] or "audio"

    def _delete_audio_input_file(self, job: Job) -> None:
        if job.job_type != JobType.AUDIO_SEPARATION or not job.normalized_url.startswith("file:"):
            return
        self._delete_artifact_file(job.normalized_url.removeprefix("file:"))

    def _delete_artifact_file(self, storage_path: str) -> None:
        file_path = self._validate_artifact_file_path(storage_path)
        if file_path.exists() and file_path.is_file():
            file_path.unlink()

    def safe_local_artifact_path(self, storage_path: str, device: Device) -> str | None:
        if self._settings.cloud_mode or device.platform != Platform.MACOS:
            return None
        try:
            file_path = self._validate_artifact_file_path(storage_path)
        except ConflictAppError:
            return None
        return str(file_path) if file_path.exists() else None

    def _validate_artifact_file_path(self, storage_path: str) -> Path:
        raw_path = Path(storage_path)
        if raw_path.is_symlink():
            raise ConflictAppError("artifact path is a symlink", "文件路径异常，已拒绝删除。")
        raw_roots = (
            self._settings.artifacts_dir,
            self._settings.database_path.parent / "Artifacts",
        )
        if any(root.is_symlink() for root in raw_roots):
            raise ConflictAppError("artifact root is a symlink", "文件目录配置异常，已拒绝删除。")
        file_path = raw_path.resolve()
        allowed_roots = tuple(root.resolve() for root in raw_roots)
        if any(self._is_relative_to(file_path, root) for root in allowed_roots):
            return file_path
        raise ConflictAppError("artifact path is outside allowed roots", "文件不在允许删除的目录中，已拒绝删除。")

    def _is_relative_to(self, path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
        except ValueError:
            return False
        return True
