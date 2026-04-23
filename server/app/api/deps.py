from __future__ import annotations

from dataclasses import dataclass
from fastapi import Depends, Header, Request

from app.core.config import Settings
from app.core.errors import AuthenticationError
from app.domain.models import Device
from app.extractors.selector import ProviderSelector
from app.extractors.x_provider import XProvider
from app.extractors.ytdlp_provider import YtDlpProvider
from app.services.artifacts import ArtifactService
from app.services.database import Database
from app.services.devices import DeviceService, RegisterRateLimiter
from app.services.jobs import BackgroundJobRunner, JobService
from app.services.repositories import ArtifactRepository, DeviceRepository, JobRepository, RegisterAttemptRepository
from app.workers.download_job_worker import DownloadJobWorker


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    database: Database
    device_service: DeviceService
    job_service: JobService
    artifact_service: ArtifactService

    def close(self) -> None:
        self.job_service.close()


def build_container() -> AppContainer:
    settings = Settings.from_env()
    settings.ensure_directories()
    database = Database(settings)
    database.initialize()
    device_repository = DeviceRepository(database)
    register_attempt_repository = RegisterAttemptRepository(database)
    job_repository = JobRepository(database)
    artifact_repository = ArtifactRepository(database)
    selector = ProviderSelector([XProvider(), YtDlpProvider(settings)])
    worker = DownloadJobWorker(
        settings=settings,
        jobs=job_repository,
        artifacts=artifact_repository,
        selector=selector,
    )
    return AppContainer(
        settings=settings,
        database=database,
        device_service=DeviceService(
            settings,
            device_repository,
            RegisterRateLimiter(settings, register_attempt_repository),
        ),
        job_service=JobService(
            job_repository,
            BackgroundJobRunner(
                worker,
                max_jobs=settings.worker_max_jobs,
                enabled=settings.worker_enabled,
            ),
            artifact_repository,
            settings,
        ),
        artifact_service=ArtifactService(settings, artifact_repository, job_repository),
    )


def get_container(request: Request) -> AppContainer:
    return request.app.state.container


def get_current_device(
    authorization: str | None = Header(default=None),
    app_container: AppContainer = Depends(get_container),
) -> Device:
    if authorization is None or not authorization.lower().startswith("bearer "):
        raise AuthenticationError()
    token = authorization.split(" ", maxsplit=1)[1].strip()
    if not token:
        raise AuthenticationError()
    return app_container.device_service.authenticate(token)
