from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
from urllib.parse import parse_qsl, urlparse

from app.core.errors import ValidationAppError


def _default_data_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data"


def _default_artifacts_dir() -> Path:
    return Path.home() / "Downloads" / "XDownloader"


def _default_youtube_cookies_file() -> Path:
    return _default_data_dir() / "youtube" / "cookies.txt"


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


def _get_nonnegative_int_env(name: str, default: int) -> int:
    value = _get_int_env(name, default)
    if value < 0:
        raise ValidationAppError(f"invalid nonnegative integer for {name}", f"环境变量 {name} 必须大于或等于 0。")
    return value


def _get_performance_mode_env() -> str:
    raw = os.getenv("XDL_PERFORMANCE_MODE", "balanced").strip().lower().replace("-", "_")
    aliases = {
        "automatic": "auto",
        "smart": "auto",
        "low": "low_power",
        "power_saver": "low_power",
        "normal": "balanced",
        "high": "performance",
        "high_performance": "performance",
    }
    mode = aliases.get(raw, raw)
    if mode not in {"auto", "low_power", "balanced", "performance"}:
        raise ValidationAppError("invalid performance mode", "环境变量 XDL_PERFORMANCE_MODE 配置不正确。")
    return mode


def _effective_performance_mode(mode: str) -> str:
    if mode != "auto":
        return mode
    cpu_count = os.cpu_count() or 4
    if cpu_count >= 8:
        return "performance"
    return "balanced"


def _get_ytdlp_format_strategy_env() -> str:
    raw = os.getenv("XDL_YTDLP_FORMAT_STRATEGY", "balanced").strip().lower().replace("-", "_")
    aliases = {
        "auto": "adaptive",
        "smart": "adaptive",
        "fast": "speed",
        "quick": "speed",
        "best": "quality",
        "best_quality": "quality",
    }
    strategy = aliases.get(raw, raw)
    if strategy not in {"adaptive", "speed", "balanced", "quality"}:
        raise ValidationAppError("invalid yt-dlp format strategy", "环境变量 XDL_YTDLP_FORMAT_STRATEGY 配置不正确。")
    return strategy


def _performance_worker_defaults(mode: str) -> tuple[int, int]:
    match mode:
        case "low_power":
            return 1, 1
        case "performance":
            return 4, 1
        case _:
            return 2, 1


def _performance_fragment_defaults(mode: str) -> int:
    match mode:
        case "low_power":
            return 1
        case "performance":
            return 8
        case _:
            return 4


def _performance_direct_download_defaults(mode: str) -> int:
    match mode:
        case "low_power":
            return 1
        case "performance":
            return 8
        case _:
            return 4


def _is_youtube_url(source_url: str) -> bool:
    parsed = urlparse(source_url.strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/")
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if path == "/watch":
            return bool(dict(parse_qsl(parsed.query, keep_blank_values=True)).get("v"))
        return path.startswith("/shorts/") and bool(path.removeprefix("/shorts/").split("/", maxsplit=1)[0])
    return host == "youtu.be" and bool(parsed.path.strip("/")) and "/" not in parsed.path.strip("/")


@dataclass(slots=True)
class Settings:
    app_name: str = "X Downloader API"
    env: str = "development"
    cloud_mode: bool = False
    bootstrap_code: str = ""
    local_secret: str = ""
    database_path: Path = field(default_factory=lambda: _default_data_dir() / "app.db")
    artifacts_dir: Path = field(default_factory=_default_artifacts_dir)
    yt_dlp_binary: str = "yt-dlp"
    youtube_cookies_from_browser: str | None = "chrome"
    youtube_cookies_disabled: bool = False
    youtube_cookies_file: Path = field(default_factory=_default_youtube_cookies_file)
    youtube_cookies_max_bytes: int = 2 * 1024 * 1024
    youtube_js_runtime: str | None = None
    youtube_remote_components: str | None = None
    ffmpeg_binary: str = "ffmpeg"
    provider_timeout_seconds: int = 180
    download_max_bytes: int = 10 * 1024 * 1024 * 1024
    worker_enabled: bool = True
    worker_max_jobs: int = 2
    performance_mode: str = "balanced"
    download_worker_max_jobs: int = 2
    audio_separation_worker_max_jobs: int = 1
    ytdlp_concurrent_fragments: int = 4
    direct_download_max_connections: int = 4
    direct_download_segment_min_bytes: int = 8 * 1024 * 1024
    direct_download_segment_size: int = 4 * 1024 * 1024
    ytdlp_format_strategy: str = "balanced"
    ffmpeg_threads: int = 0
    download_rate_limit: str = ""
    ytdlp_external_downloader: str = ""
    ytdlp_external_downloader_args: str = ""
    register_rate_limit: int = 5
    register_window_seconds: int = 300
    audio_upload_max_bytes: int = 200 * 1024 * 1024
    audio_separation_command: str = ""
    audio_separation_timeout_seconds: int = 1800

    @property
    def yt_dlp_command(self) -> list[str]:
        if not self.yt_dlp_binary.strip():
            raise ValidationAppError("invalid yt-dlp binary", "环境变量 XDL_YT_DLP_BINARY 配置不正确。")
        return [self.yt_dlp_binary]

    def youtube_runtime_args(self, source_url: str) -> list[str]:
        if not _is_youtube_url(source_url):
            return []
        args: list[str] = []
        if self.youtube_js_runtime:
            args.extend(["--js-runtimes", self.youtube_js_runtime])
        if self.youtube_remote_components:
            args.extend(["--remote-components", self.youtube_remote_components])
        return args

    def youtube_cookie_retry_args(self, source_url: str) -> list[str]:
        if not _is_youtube_url(source_url) or self.youtube_cookies_disabled:
            return []
        args: list[str] = []
        if self.youtube_js_runtime:
            args.extend(["--js-runtimes", self.youtube_js_runtime])
        if self.youtube_cookies_file.exists() and self.youtube_cookies_file.stat().st_size > 0:
            args.extend(["--cookies", str(self.youtube_cookies_file)])
            return args
        if not self.youtube_cookies_from_browser:
            return []
        args.extend(["--cookies-from-browser", self.youtube_cookies_from_browser])
        return args

    def youtube_cookie_retry_command(self, command: list[str], source_url: str) -> list[str]:
        retry_args = self.youtube_cookie_retry_args(source_url)
        if not retry_args:
            return []
        return [*self._without_remote_components(command[:-2]), *retry_args, *command[-2:]]

    def _without_remote_components(self, args: list[str]) -> list[str]:
        cleaned: list[str] = []
        skip_next = False
        for arg in args:
            if skip_next:
                skip_next = False
                continue
            if arg == "--remote-components":
                skip_next = True
                continue
            cleaned.append(arg)
        return cleaned

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("XDL_DATA_DIR", _default_data_dir()))
        cloud_mode = _get_bool_env("XDL_CLOUD_MODE", False)
        bootstrap_code = os.getenv("XDL_BOOTSTRAP_CODE", "")
        if cloud_mode and not bootstrap_code:
            raise ValueError("XDL_BOOTSTRAP_CODE is required when XDL_CLOUD_MODE is true")
        performance_mode = _get_performance_mode_env()
        effective_performance_mode = _effective_performance_mode(performance_mode)
        download_default, audio_separation_default = _performance_worker_defaults(effective_performance_mode)
        fragment_default = _performance_fragment_defaults(effective_performance_mode)
        direct_download_default = _performance_direct_download_defaults(effective_performance_mode)
        worker_max_jobs = _get_positive_int_env("XDL_WORKER_MAX_JOBS", download_default)
        return cls(
            app_name=os.getenv("XDL_APP_NAME", "X Downloader API"),
            env=os.getenv("XDL_ENV", "development"),
            cloud_mode=cloud_mode,
            bootstrap_code=bootstrap_code,
            local_secret=os.getenv("XDL_LOCAL_SECRET", ""),
            database_path=Path(os.getenv("XDL_DATABASE_PATH", data_dir / "app.db")),
            artifacts_dir=Path(os.getenv("XDL_ARTIFACTS_DIR", _default_artifacts_dir())),
            yt_dlp_binary=os.getenv("XDL_YT_DLP_BINARY", "yt-dlp"),
            youtube_cookies_from_browser=os.getenv("XDL_YOUTUBE_COOKIES_FROM_BROWSER") or "chrome",
            youtube_cookies_disabled=_get_bool_env("XDL_YOUTUBE_COOKIES_DISABLED", False),
            youtube_cookies_file=Path(os.getenv("XDL_YOUTUBE_COOKIES_FILE", data_dir / "youtube" / "cookies.txt")),
            youtube_cookies_max_bytes=_get_positive_int_env("XDL_YOUTUBE_COOKIES_MAX_BYTES", 2 * 1024 * 1024),
            youtube_js_runtime=os.getenv("XDL_YOUTUBE_JS_RUNTIME") or None,
            youtube_remote_components=os.getenv("XDL_YOUTUBE_REMOTE_COMPONENTS") or None,
            ffmpeg_binary=os.getenv("XDL_FFMPEG_BINARY", "ffmpeg"),
            provider_timeout_seconds=_get_positive_int_env("XDL_PROVIDER_TIMEOUT", 180),
            download_max_bytes=_get_positive_int_env("XDL_DOWNLOAD_MAX_BYTES", 10 * 1024 * 1024 * 1024),
            worker_enabled=_get_bool_env("XDL_WORKER_ENABLED", True),
            worker_max_jobs=worker_max_jobs,
            performance_mode=performance_mode,
            download_worker_max_jobs=_get_positive_int_env("XDL_DOWNLOAD_WORKER_MAX_JOBS", worker_max_jobs),
            audio_separation_worker_max_jobs=_get_positive_int_env("XDL_AUDIO_SEPARATION_WORKER_MAX_JOBS", audio_separation_default),
            ytdlp_concurrent_fragments=_get_positive_int_env("XDL_YTDLP_CONCURRENT_FRAGMENTS", fragment_default),
            direct_download_max_connections=_get_positive_int_env("XDL_DIRECT_DOWNLOAD_MAX_CONNECTIONS", direct_download_default),
            direct_download_segment_min_bytes=_get_positive_int_env("XDL_DIRECT_DOWNLOAD_SEGMENT_MIN_BYTES", 8 * 1024 * 1024),
            direct_download_segment_size=_get_positive_int_env("XDL_DIRECT_DOWNLOAD_SEGMENT_SIZE", 4 * 1024 * 1024),
            ytdlp_format_strategy=_get_ytdlp_format_strategy_env(),
            ffmpeg_threads=_get_nonnegative_int_env("XDL_FFMPEG_THREADS", 0),
            download_rate_limit=os.getenv("XDL_DOWNLOAD_RATE_LIMIT", "").strip(),
            ytdlp_external_downloader=os.getenv("XDL_YTDLP_EXTERNAL_DOWNLOADER", "").strip(),
            ytdlp_external_downloader_args=os.getenv("XDL_YTDLP_EXTERNAL_DOWNLOADER_ARGS", "").strip(),
            register_rate_limit=_get_positive_int_env("XDL_REGISTER_RATE_LIMIT", 5),
            register_window_seconds=_get_positive_int_env("XDL_REGISTER_WINDOW_SECONDS", 300),
            audio_upload_max_bytes=_get_positive_int_env("XDL_AUDIO_UPLOAD_MAX_BYTES", 200 * 1024 * 1024),
            audio_separation_command=os.getenv("XDL_AUDIO_SEPARATION_COMMAND", ""),
            audio_separation_timeout_seconds=_get_positive_int_env("XDL_AUDIO_SEPARATION_TIMEOUT", 1800),
        )

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
