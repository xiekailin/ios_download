from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
import tempfile
from threading import Lock

from app.core.config import Settings
from app.core.errors import ValidationAppError


@dataclass(slots=True)
class YouTubeCookieStatus:
    is_configured: bool
    file_size: int | None
    updated_at: datetime | None


class PlatformCookieService:
    _locks: dict[Path, Lock] = {}
    _locks_guard = Lock()

    def __init__(self, *, path: Path, max_bytes: int, supported_domains: tuple[str, ...], invalid_message: str) -> None:
        self._path = path
        self._max_bytes = max_bytes
        self._supported_domains = supported_domains
        self._invalid_message = invalid_message
        resolved_path = path.resolve()
        with self._locks_guard:
            self._lock = self._locks.setdefault(resolved_path, Lock())

    def status(self) -> YouTubeCookieStatus:
        with self._lock:
            return self._status_unlocked()

    def save(self, content: bytes) -> YouTubeCookieStatus:
        self._validate(content)
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self._path.parent, 0o700)
            fd, temp_name = tempfile.mkstemp(prefix="cookies-", suffix=".tmp", dir=self._path.parent)
            temp_path = Path(temp_name)
            try:
                with os.fdopen(fd, "wb") as file:
                    file.write(content)
                os.chmod(temp_path, 0o600)
                os.replace(temp_path, self._path)
                os.chmod(self._path, 0o600)
            finally:
                if temp_path.exists():
                    temp_path.unlink()
            return self._status_unlocked()

    def delete(self) -> YouTubeCookieStatus:
        with self._lock:
            try:
                self._path.unlink()
            except FileNotFoundError:
                pass
            return self._status_unlocked()

    def _status_unlocked(self) -> YouTubeCookieStatus:
        try:
            stat = self._path.stat()
        except FileNotFoundError:
            return YouTubeCookieStatus(is_configured=False, file_size=None, updated_at=None)
        if stat.st_size <= 0:
            return YouTubeCookieStatus(is_configured=False, file_size=None, updated_at=None)
        return YouTubeCookieStatus(
            is_configured=True,
            file_size=stat.st_size,
            updated_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
        )

    def _validate(self, content: bytes) -> None:
        if not content:
            raise ValidationAppError("cookie file is empty", "Cookie 文件为空。")
        if len(content) > self._max_bytes:
            raise ValidationAppError("cookie file is too large", "Cookie 文件太大。")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationAppError("cookie file must be utf-8", "Cookie 文件格式不正确。") from exc
        has_cookie_line = False
        has_supported_domain = False
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.startswith("#HttpOnly_"):
                line = line.removeprefix("#HttpOnly_")
            elif not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            has_cookie_line = True
            if self._is_supported_cookie_domain(parts[0]):
                has_supported_domain = True
        if not has_cookie_line or not has_supported_domain:
            raise ValidationAppError("invalid cookie file", self._invalid_message)

    def _is_supported_cookie_domain(self, domain: str) -> bool:
        normalized = domain.strip().lower().lstrip(".")
        return normalized in self._supported_domains or any(normalized.endswith(f".{domain}") for domain in self._supported_domains)


class YouTubeCookieService(PlatformCookieService):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            path=settings.youtube_cookies_file,
            max_bytes=settings.youtube_cookies_max_bytes,
            supported_domains=("youtube.com", "google.com"),
            invalid_message="请选择 YouTube/Google 的 Netscape cookies.txt 文件。",
        )
