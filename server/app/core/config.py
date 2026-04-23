from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os

from app.core.errors import ValidationAppError


def _default_data_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data"


def _default_artifacts_dir() -> Path:
    return Path.home() / "Downloads" / "XDownloader"


def _get_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValidationAppError(f"invalid integer for {name}", f"环境变量 {name} 配置不正确。") from exc
    return value


def _get_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ValidationAppError(f"invalid boolean for {name}", f"环境变量 {name} 配置不正确。")


def _get_positive_int_env(name: str, default: int) -> int:
    value = _get_int_env(name, default)
    if value < 1:
        raise ValidationAppError(f"invalid positive integer for {name}", f"环境变量 {name} 必须大于 0。")
    return value


@dataclass(slots=True)
class Settings:
    app_name: str = "X Downloader API"
    env: str = "development"
    bootstrap_code: str = ""
    database_path: Path = field(default_factory=lambda: _default_data_dir() / "app.db")
    artifacts_dir: Path = field(default_factory=_default_artifacts_dir)
    yt_dlp_binary: str = "yt-dlp"
    youtube_cookies_from_browser: str | None = None
    youtube_js_runtime: str | None = None
    youtube_remote_components: str | None = None
    ffmpeg_binary: str = "ffmpeg"
    provider_timeout_seconds: int = 180
    download_max_bytes: int = 512 * 1024 * 1024
    worker_enabled: bool = True
    worker_max_jobs: int = 2
    register_rate_limit: int = 5
    register_window_seconds: int = 300

    @property
    def yt_dlp_command(self) -> list[str]:
        if not self.yt_dlp_binary.strip() or any(char.isspace() for char in self.yt_dlp_binary):
            raise ValidationAppError("invalid yt-dlp binary", "环境变量 XDL_YT_DLP_BINARY 配置不正确。")
        return [self.yt_dlp_binary]

    def youtube_runtime_args(self, source_url: str) -> list[str]:
        if "youtube.com/" not in source_url and "youtu.be/" not in source_url:
            return []
        args: list[str] = []
        if self.youtube_js_runtime:
            args.extend(["--js-runtimes", self.youtube_js_runtime])
        if self.youtube_remote_components:
            args.extend(["--remote-components", self.youtube_remote_components])
        if self.youtube_cookies_from_browser:
            args.extend(["--cookies-from-browser", self.youtube_cookies_from_browser])
        return args

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("XDL_DATA_DIR", _default_data_dir()))
        return cls(
            app_name=os.getenv("XDL_APP_NAME", "X Downloader API"),
            env=os.getenv("XDL_ENV", "development"),
            bootstrap_code=os.getenv("XDL_BOOTSTRAP_CODE", ""),
            database_path=Path(os.getenv("XDL_DATABASE_PATH", data_dir / "app.db")),
            artifacts_dir=Path(os.getenv("XDL_ARTIFACTS_DIR", _default_artifacts_dir())),
            yt_dlp_binary=os.getenv("XDL_YT_DLP_BINARY", "yt-dlp"),
            youtube_cookies_from_browser=os.getenv("XDL_YOUTUBE_COOKIES_FROM_BROWSER") or None,
            youtube_js_runtime=os.getenv("XDL_YOUTUBE_JS_RUNTIME") or None,
            youtube_remote_components=os.getenv("XDL_YOUTUBE_REMOTE_COMPONENTS") or None,
            ffmpeg_binary=os.getenv("XDL_FFMPEG_BINARY", "ffmpeg"),
            provider_timeout_seconds=_get_positive_int_env("XDL_PROVIDER_TIMEOUT", 180),
            download_max_bytes=_get_positive_int_env("XDL_DOWNLOAD_MAX_BYTES", 512 * 1024 * 1024),
            worker_enabled=_get_bool_env("XDL_WORKER_ENABLED", True),
            worker_max_jobs=_get_positive_int_env("XDL_WORKER_MAX_JOBS", 2),
            register_rate_limit=_get_positive_int_env("XDL_REGISTER_RATE_LIMIT", 5),
            register_window_seconds=_get_positive_int_env("XDL_REGISTER_WINDOW_SECONDS", 300),
        )

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
