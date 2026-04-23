from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.core.errors import AuthorizationError, NotFoundAppError
from app.domain.models import Device
from app.services.repositories import ArtifactRepository, JobRepository


class ArtifactService:
    def __init__(self, settings: Settings, artifacts: ArtifactRepository, jobs: JobRepository) -> None:
        self._settings = settings
        self._artifacts = artifacts
        self._jobs = jobs

    def get_owned_artifact_path(self, artifact_id: str, device: Device) -> tuple[str, Path, str]:
        artifact = self._artifacts.get(artifact_id)
        if artifact is None:
            raise NotFoundAppError("artifact")
        job = self._jobs.get(artifact.job_id)
        if job is None:
            raise NotFoundAppError("job")
        if job.device_id != device.id:
            raise AuthorizationError()
        file_path = Path(artifact.storage_path).resolve()
        artifacts_root = self._settings.artifacts_dir.resolve()
        try:
            file_path.relative_to(artifacts_root)
        except ValueError as exc:
            raise NotFoundAppError("artifact file") from exc
        if not file_path.exists() or not file_path.is_file():
            raise NotFoundAppError("artifact file")
        return artifact.file_name, file_path, artifact.mime_type
