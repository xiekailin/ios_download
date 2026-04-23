from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import status


@dataclass(slots=True)
class AppError(Exception):
    code: str
    message: str
    user_message: str
    status_code: int = status.HTTP_400_BAD_REQUEST
    details: dict[str, Any] | None = None

    def to_response(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "user_message": self.user_message,
                "details": self.details or {},
            }
        }


class AuthenticationError(AppError):
    def __init__(self, message: str = "authentication failed") -> None:
        super().__init__(
            code="authentication_failed",
            message=message,
            user_message="认证失败，请重新注册设备。",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


class AuthorizationError(AppError):
    def __init__(self, message: str = "access denied") -> None:
        super().__init__(
            code="access_denied",
            message=message,
            user_message="没有权限访问该资源。",
            status_code=status.HTTP_403_FORBIDDEN,
        )


class ValidationAppError(AppError):
    def __init__(self, message: str, user_message: str) -> None:
        super().__init__(
            code="validation_error",
            message=message,
            user_message=user_message,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )


class NotFoundAppError(AppError):
    def __init__(self, resource: str) -> None:
        super().__init__(
            code="not_found",
            message=f"{resource} not found",
            user_message="目标资源不存在。",
            status_code=status.HTTP_404_NOT_FOUND,
        )


class ConflictAppError(AppError):
    def __init__(self, message: str, user_message: str) -> None:
        super().__init__(
            code="conflict",
            message=message,
            user_message=user_message,
            status_code=status.HTTP_409_CONFLICT,
        )


class TooManyRequestsAppError(AppError):
    def __init__(self, message: str, user_message: str) -> None:
        super().__init__(
            code="too_many_requests",
            message=message,
            user_message=user_message,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )


class ProviderAppError(AppError):
    def __init__(self, message: str, user_message: str = "解析下载资源失败。") -> None:
        super().__init__(
            code="provider_error",
            message=message,
            user_message=user_message,
            status_code=status.HTTP_502_BAD_GATEWAY,
        )


class ProviderUnavailableError(ProviderAppError):
    def __init__(self, message: str) -> None:
        super().__init__(message=message, user_message="当前解析器暂时不可用。")


class DownloadAppError(AppError):
    def __init__(self, message: str, user_message: str = "下载视频失败，请稍后重试。") -> None:
        super().__init__(
            code="download_error",
            message=message,
            user_message=user_message,
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
