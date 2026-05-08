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
        allowed_roots = (
            self._settings.artifacts_dir.resolve(),
            (self._settings.database_path.parent / "Artifacts").resolve(),
        )
        if not any(_is_relative_to(file_path, root) for root in allowed_roots):
            raise NotFoundAppError("artifact file")
        if not file_path.exists() or not file_path.is_file():
            raise NotFoundAppError("artifact file")
        return artifact.file_name, file_path, artifact.mime_type


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
