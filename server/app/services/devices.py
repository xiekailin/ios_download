from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.core.config import Settings
from app.core.errors import AuthenticationError, AuthorizationError, TooManyRequestsAppError
from app.core.security import generate_token, hash_token
from app.domain.models import Device
from app.schemas.devices import RegisterDeviceRequest
from app.services.database import utc_now
from app.services.repositories import DeviceRepository, RegisterAttemptRepository


@dataclass(slots=True)
class RegisterRateLimiter:
    settings: Settings
    repository: RegisterAttemptRepository

    def check(self, client_key: str) -> None:
        window_start = utc_now() - timedelta(seconds=self.settings.register_window_seconds)
        allowed = self.repository.check_and_record(
            client_key,
            window_start=window_start,
            limit=self.settings.register_rate_limit,
        )
        if not allowed:
            raise TooManyRequestsAppError("too many register attempts", "注册过于频繁，请稍后再试。")


class DeviceService:
    def __init__(
        self,
        settings: Settings,
        repository: DeviceRepository,
        register_limiter: RegisterRateLimiter,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._register_limiter = register_limiter

    def register(self, payload: RegisterDeviceRequest, *, client_key: str) -> tuple[Device, str]:
        self._register_limiter.check(client_key)
        if self._settings.cloud_mode and (
            not self._settings.bootstrap_code or payload.bootstrap_code != self._settings.bootstrap_code
        ):
            raise AuthorizationError("invalid bootstrap code")
        token = generate_token()
        device = self._repository.create(
            name=payload.device_name,
            platform=payload.platform,
            app_version=payload.app_version,
            token_hash=hash_token(token),
        )
        return device, token

    def authenticate(self, token: str) -> Device:
        device = self._repository.get_by_token_hash(hash_token(token))
        if device is None:
            raise AuthenticationError()
        self._repository.touch(device.id)
        return device
