from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import json
import logging
import mimetypes
import os
import math
import re
import shutil
import signal
import subprocess
import threading
from subprocess import run as subprocess_run
import time

from app.core.config import Settings
from app.core.errors import DownloadAppError, ProviderAppError
from app.core.ytdlp_errors import is_ytdlp_login_required, ytdlp_safe_error_text, ytdlp_user_message
from app.domain.models import DeliveryMode, JobStatus, JobType
from app.extractors.selector import ProviderSelector
from app.services.media_downloader import MediaDownloader
from app.services.repositories import ArtifactRepository, JobRepository
from app.services.url_tools import detect_source_platform, normalize_extraction_source_url

logger = logging.getLogger(__name__)

_YTDLP_PERCENT_RE = re.compile(r"(?P<percent>\d+(?:\.\d+)?)%")
_YTDLP_TOTAL_RE = re.compile(r"of\s+~?\s*(?P<size>[0-9.]+(?:[KMGTPE]?i?B|B))")
_YTDLP_SPEED_RE = re.compile(r"at\s+(?P<size>[0-9.]+(?:[KMGTPE]?i?B|B))/s")
_YTDLP_ETA_RE = re.compile(r"ETA\s+(?P<eta>[0-9:]+)")
_YTDLP_DOWNLOADED_RE = re.compile(r"^\[download\]\s+(?P<size>[0-9.]+(?:[KMGTPE]?i?B|B))(?=\s+at|\s*$)")
_ARTIFACT_INVALID_CHARS_RE = re.compile(r"[\x00-\x1f\\/:*?\"<>|]+")
_ARTIFACT_WHITESPACE_RE = re.compile(r"\s+")
_BEST_MP4_FORMAT_SELECTOR = "bestvideo*[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo*+bestaudio/best"
_FAST_MP4_FORMAT_SELECTOR = "best[ext=mp4]/bestvideo*[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
_ARIA2C_DEFAULT_DOWNLOADER_ARGS = "aria2c:-x 8 -s 8 -k 1M"
_ALLOWED_ARTIFACT_EXTENSIONS = frozenset({"mp4", "mov", "webm", "m4a", "mp3", "jpg", "jpeg", "png", "webp", "gif"})
_PROGRESS_UPDATE_INTERVAL_SECONDS = 0.5
_MAX_DELEGATE_STDERR_LINES = 200
_DELEGATE_DOWNLOAD_RETRY_COUNT = 3
_DELEGATE_RETRYABLE_ERROR_MARKERS = (
    "timed out",
    "timeout",
    "http error 429",
    "http error 500",
    "http error 502",
    "http error 503",
    "http error 504",
    "service unavailable",
    "too many requests",
    "connection reset",
    "connection aborted",
    "connection refused",
    "remote end closed connection",
    "temporary failure",
    "temporarily unavailable",
    "network is unreachable",
)
_DELEGATE_NON_RETRYABLE_ERROR_MARKERS = (
    "canceled",
    "cancelled",
    "binary not found",
    "exceeds size limit",
    "login verification required",
    "requested format",
    "format is not available",
    "no video formats",
    "no artifact",
    "private",
    "has been removed",
    "not available",
    "video unavailable",
)


class DownloadJobWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        jobs: JobRepository,
        artifacts: ArtifactRepository,
        selector: ProviderSelector,
        downloader: MediaDownloader | None = None,
    ) -> None:
        self._settings = settings
        self._jobs = jobs
        self._artifacts = artifacts
        self._selector = selector
        self._downloader = downloader or MediaDownloader(max_bytes=self._settings.download_max_bytes)

    def run(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return

        artifact_path: Path | None = None
        thumbnail_path: Path | None = None
        artifact_id: str | None = None
        delegate_output_prefix: str | None = None
        phase_timings_ms = {"resolve": 0, "download": 0, "merge": 0, "store": 0, "total": 0}
        overall_started_at = time.monotonic()
        try:
            if not self._transition_status_with_event(
                job_id,
                from_statuses={JobStatus.QUEUED},
                to_status=JobStatus.RESOLVING,
                progress=5,
                message="开始解析链接",
            ):
                return
            job = self._jobs.get(job_id)
            if job is None:
                return
            resolve_started_at = time.monotonic()
            extracted = self._selector.extract(normalize_extraction_source_url(job.source_url))
            phase_timings_ms["resolve"] = self._elapsed_ms(resolve_started_at)
            if not self._transition_status_with_event(
                job_id,
                from_statuses={JobStatus.RESOLVING},
                to_status=JobStatus.RESOLVED,
                progress=20,
                provider=extracted.provider,
                media_title=extracted.title,
                author_handle=extracted.author_handle,
                thumbnail_url=extracted.thumbnail_url,
                message="链接解析完成",
            ):
                return
            if not self._transition_status_with_event(
                job_id,
                from_statuses={JobStatus.RESOLVED},
                to_status=JobStatus.DOWNLOADING,
                progress=45,
                provider=extracted.provider,
                downloaded_bytes=None,
                total_bytes=None,
                speed_bytes_per_sec=None,
                eta_seconds=None,
                message="开始下载素材",
            ):
                return
            if job.job_type == JobType.AUDIO_DOWNLOAD and extracted.delivery_mode != DeliveryMode.DELEGATE_YTDLP and extracted.file_extension.lower() in {"jpg", "jpeg", "png", "webp", "gif"}:
                raise DownloadAppError("no extractable audio found", "该链接没有可提取的音频。")
            download_started_at = time.monotonic()
            if extracted.delivery_mode == DeliveryMode.DELEGATE_YTDLP:
                delegate_output_prefix = job_id
                artifact_path = self._download_via_ytdlp(
                    job_id=job_id,
                    title=extracted.title,
                    source_url=normalize_extraction_source_url(job.source_url),
                    ext=extracted.file_extension,
                    audio_only=job.job_type == JobType.AUDIO_DOWNLOAD,
                    selected_quality=job.selected_quality,
                )
                phase_timings_ms["download"] = self._elapsed_ms(download_started_at)
            else:
                artifact_path = self._download_media(
                    job_id=job_id,
                    title=extracted.title,
                    url=extracted.direct_url,
                    allowed_addresses=extracted.direct_url_addresses,
                    ext=extracted.file_extension,
                )
                phase_timings_ms["download"] = self._elapsed_ms(download_started_at)
                if job.job_type == JobType.AUDIO_DOWNLOAD:
                    merge_started_at = time.monotonic()
                    artifact_path = self._extract_mp3(job_id=job_id, title=extracted.title, source_path=artifact_path)
                    phase_timings_ms["merge"] = self._elapsed_ms(merge_started_at)
            store_started_at = time.monotonic()
            current = self._jobs.get(job_id)
            if not self._transition_status_with_event(
                job_id,
                from_statuses={JobStatus.DOWNLOADING},
                to_status=JobStatus.STORING,
                progress=90,
                provider=extracted.provider,
                downloaded_bytes=current.downloaded_bytes if current else None,
                total_bytes=current.total_bytes if current else None,
                speed_bytes_per_sec=current.speed_bytes_per_sec if current else None,
                eta_seconds=current.eta_seconds if current else None,
                message="正在保存文件",
            ):
                self._cleanup_file(artifact_path)
                return
            media_details = self._probe_media_details(artifact_path)
            mime_type = mimetypes.guess_type(artifact_path.name)[0] or "application/octet-stream"
            if self._should_generate_thumbnail(job.job_type, mime_type, media_details):
                thumbnail_path = self._generate_video_thumbnail(job_id, artifact_path, media_details.get("duration_seconds"))
            artifact = self._artifacts.create(
                job_id=job_id,
                file_name=artifact_path.name,
                mime_type=mime_type,
                storage_path=str(artifact_path),
                file_size=artifact_path.stat().st_size,
                thumbnail_path=str(thumbnail_path) if thumbnail_path else None,
                **media_details,
            )
            artifact_id = artifact.id
            completed_size = artifact.file_size
            if not self._transition_status_with_event(
                job_id,
                from_statuses={JobStatus.STORING},
                to_status=JobStatus.COMPLETED,
                progress=100,
                provider=extracted.provider,
                downloaded_bytes=completed_size,
                total_bytes=completed_size,
                speed_bytes_per_sec=None,
                eta_seconds=None,
                artifact_id=artifact.id,
                finished_at=datetime.now(tz=UTC),
                message="素材已保存",
            ):
                self._artifacts.delete(artifact.id)
                if thumbnail_path is not None:
                    self._cleanup_file(thumbnail_path)
                self._cleanup_file(artifact_path)
                return
            phase_timings_ms["store"] = self._elapsed_ms(store_started_at)
            phase_timings_ms["total"] = self._elapsed_ms(overall_started_at)
            self._record_performance_event(job_id, phase_timings_ms)
        except (ProviderAppError, DownloadAppError) as exc:
            self._mark_failed(job_id, error_code=exc.code, error_message=exc.message, user_message=exc.user_message)
            if artifact_id is not None:
                self._artifacts.delete(artifact_id)
            if thumbnail_path is not None:
                self._cleanup_file(thumbnail_path)
            if artifact_path is not None:
                self._cleanup_file(artifact_path)
            if delegate_output_prefix is not None:
                self._cleanup_delegate_outputs(delegate_output_prefix)
        except Exception:
            logger.exception("download job failed unexpectedly job_id=%s", job_id)
            self._mark_failed(
                job_id,
                error_code="internal_error",
                error_message="unexpected worker exception",
                user_message="任务执行失败，请稍后重试。",
            )
            if artifact_id is not None:
                self._artifacts.delete(artifact_id)
            if thumbnail_path is not None:
                self._cleanup_file(thumbnail_path)
            if artifact_path is not None:
                self._cleanup_file(artifact_path)
            if delegate_output_prefix is not None:
                self._cleanup_delegate_outputs(delegate_output_prefix)

    def _transition_status_with_event(self, job_id: str, *, message: str, **kwargs) -> bool:
        changed = self._jobs.transition_status(job_id, **kwargs)
        if changed:
            self._jobs.create_event(job_id, level="info", event_type=kwargs["to_status"].value, message=message)
        return changed

    def _record_performance_event(self, job_id: str, timings_ms: dict[str, int]) -> None:
        self._jobs.create_event(
            job_id,
            level="info",
            event_type="performance",
            message=self._performance_message(timings_ms),
        )

    def _performance_message(self, timings_ms: dict[str, int]) -> str:
        return (
            "性能统计："
            f"解析 {timings_ms.get('resolve', 0)}ms，"
            f"下载 {timings_ms.get('download', 0)}ms，"
            f"合并/转换 {timings_ms.get('merge', 0)}ms，"
            f"保存 {timings_ms.get('store', 0)}ms，"
            f"总计 {timings_ms.get('total', 0)}ms"
        )

    def _elapsed_ms(self, started_at: float) -> int:
        return max(0, int((time.monotonic() - started_at) * 1000))

    def _mark_failed(self, job_id: str, *, error_code: str, error_message: str, user_message: str) -> None:
        current = self._jobs.get(job_id)
        if current is None or current.status == JobStatus.CANCELED:
            return
        self._jobs.create_event(job_id, level="error", event_type="failed", message=user_message)
        self._jobs.update_status(
            job_id,
            status=JobStatus.FAILED,
            progress=current.progress,
            downloaded_bytes=current.downloaded_bytes,
            total_bytes=current.total_bytes,
            speed_bytes_per_sec=current.speed_bytes_per_sec,
            eta_seconds=current.eta_seconds,
            error_code=error_code,
            error_message=error_message,
            user_message=user_message,
            finished_at=datetime.now(tz=UTC),
        )

    def _download_media(
        self,
        *,
        job_id: str,
        title: str | None,
        url: str | None,
        allowed_addresses: tuple[str, ...],
        ext: str,
    ) -> Path:
        if not url:
            raise DownloadAppError("no direct media URL returned")
        if not allowed_addresses:
            raise DownloadAppError("no allowed media addresses returned")

        output_path = self._allocate_artifact_path(
            stem=self._safe_artifact_stem(title, fallback=job_id),
            ext=ext,
            directory=self._artifact_output_dir(JobType.DOWNLOAD),
        )
        temp_path = output_path.with_suffix(output_path.suffix + ".part")
        last_reported_at = 0.0
        last_reported_progress: int | None = None
        last_reported_bytes: int | None = None

        def progress_callback(
            downloaded_bytes: int,
            total_bytes: int | None,
            speed_bytes_per_sec: int | None,
            eta_seconds: int | None,
        ) -> None:
            nonlocal last_reported_at, last_reported_progress, last_reported_bytes
            progress = self._progress_from_bytes(downloaded_bytes, total_bytes)
            force = total_bytes is not None and downloaded_bytes >= total_bytes
            now = time.monotonic()
            if not force and last_reported_progress == progress and last_reported_bytes == downloaded_bytes:
                return
            if not force and now - last_reported_at < _PROGRESS_UPDATE_INTERVAL_SECONDS:
                return
            self._update_download_progress(
                job_id,
                downloaded_bytes=downloaded_bytes,
                total_bytes=total_bytes,
                speed_bytes_per_sec=speed_bytes_per_sec,
                eta_seconds=eta_seconds,
                progress=progress,
            )
            last_reported_at = now
            last_reported_progress = progress
            last_reported_bytes = downloaded_bytes

        try:
            self._downloader.download(
                url=url,
                allowed_addresses=allowed_addresses,
                output_path=temp_path,
                timeout_seconds=self._settings.provider_timeout_seconds,
                progress_callback=progress_callback,
            )
            if output_path.exists():
                output_path.unlink()
            shutil.move(str(temp_path), str(output_path))
            return output_path
        except Exception:
            self._cleanup_file(temp_path)
            self._cleanup_file(output_path)
            raise

    def _download_via_ytdlp(
        self,
        *,
        job_id: str,
        title: str | None,
        source_url: str,
        ext: str,
        audio_only: bool = False,
        selected_quality: str | None = None,
    ) -> Path:
        output_template = self._settings.artifacts_dir / f"{job_id}.%(ext)s"
        ffmpeg_location = self._resolve_ffmpeg_location()
        command = self._delegate_download_command(
            source_url=source_url,
            output_template=output_template,
            ffmpeg_location=ffmpeg_location,
            ext=ext,
            audio_only=audio_only,
            selected_quality=selected_quality,
        )
        self._run_delegate_download_with_retries(job_id=job_id, command=command, source_url=source_url)

        artifact_path = self._find_delegate_output(job_id)
        if artifact_path is None:
            raise DownloadAppError("yt-dlp delegated download produced no artifact")
        if artifact_path.stat().st_size > self._settings.download_max_bytes:
            self._cleanup_file(artifact_path)
            raise DownloadAppError("delegated download exceeds size limit")
        self._cleanup_delegate_partials(job_id)
        return self._finalize_delegate_artifact(artifact_path, title=title, fallback=job_id)

    def _run_delegate_download_with_retries(self, *, job_id: str, command: list[str], source_url: str) -> None:
        for attempt in range(_DELEGATE_DOWNLOAD_RETRY_COUNT + 1):
            self._raise_if_job_canceled(job_id)
            if attempt > 0:
                self._cleanup_delegate_outputs(job_id, keep_partials=True)
            try:
                self._run_delegate_download_once(job_id=job_id, command=command, source_url=source_url)
                return
            except DownloadAppError as exc:
                self._raise_if_job_canceled(job_id)
                if attempt == _DELEGATE_DOWNLOAD_RETRY_COUNT or not self._is_retryable_delegate_download_error(exc):
                    raise

    def _raise_if_job_canceled(self, job_id: str) -> None:
        current = self._jobs.get(job_id)
        if current is None or current.status == JobStatus.CANCELED:
            raise DownloadAppError("yt-dlp download canceled", "任务已取消。")

    def _run_delegate_download_once(self, *, job_id: str, command: list[str], source_url: str) -> None:
        retry_command = self._settings.youtube_cookie_retry_command(command, source_url)
        try:
            self._run_delegate_download(job_id=job_id, command=command)
        except DownloadAppError as exc:
            if not retry_command or not is_ytdlp_login_required(exc.message):
                raise
            self._cleanup_delegate_outputs(job_id)
            try:
                self._run_delegate_download(job_id=job_id, command=retry_command)
            except DownloadAppError as retry_exc:
                if is_ytdlp_login_required(retry_exc.message):
                    raise DownloadAppError(
                        ytdlp_safe_error_text(retry_exc.message),
                        ytdlp_user_message(retry_exc.message, retried_with_cookie=True),
                    ) from retry_exc
                raise

    def _is_retryable_delegate_download_error(self, exc: DownloadAppError) -> bool:
        message = exc.message.lower()
        if any(marker in message for marker in _DELEGATE_NON_RETRYABLE_ERROR_MARKERS):
            return False
        return any(marker in message for marker in _DELEGATE_RETRYABLE_ERROR_MARKERS)

    def _delegate_download_command(
        self,
        *,
        source_url: str,
        output_template: Path,
        ffmpeg_location: str,
        ext: str,
        audio_only: bool,
        selected_quality: str | None = None,
    ) -> list[str]:
        return [
            *self._settings.yt_dlp_command,
            "--ignore-config",
            "--no-warnings",
            "--newline",
            "--no-playlist",
            "--continue",
            "--part",
            "--retries",
            "3",
            "--fragment-retries",
            "5",
            "--concurrent-fragments",
            str(self._settings.ytdlp_concurrent_fragments),
            *self._delegate_download_engine_args(),
            *self._settings.youtube_runtime_args(source_url),
            "--max-filesize",
            str(self._settings.download_max_bytes),
            *self._delegate_format_args(
                source_url=source_url,
                ext=ext,
                audio_only=audio_only,
                selected_quality=selected_quality,
            ),
            *self._delegate_ffmpeg_postprocessor_args(audio_only=audio_only),
            "--ffmpeg-location",
            ffmpeg_location,
            "-o",
            str(output_template),
            "--",
            source_url,
        ]

    def _delegate_download_engine_args(self) -> list[str]:
        args: list[str] = []
        if self._settings.download_rate_limit:
            args.extend(["--limit-rate", self._settings.download_rate_limit])
        downloader = self._resolve_external_downloader()
        if downloader:
            args.extend(["--downloader", downloader])
            downloader_args = self._external_downloader_args(downloader)
            if downloader_args:
                args.extend(["--downloader-args", downloader_args])
        elif self._settings.ytdlp_external_downloader_args and self._settings.ytdlp_external_downloader.strip().lower() != "auto":
            args.extend(["--downloader-args", self._settings.ytdlp_external_downloader_args])
        return args

    def _resolve_external_downloader(self) -> str:
        configured = self._settings.ytdlp_external_downloader.strip()
        if not configured:
            return ""
        if configured.lower() != "auto":
            return configured
        return "aria2c" if shutil.which("aria2c") else ""

    def _external_downloader_args(self, downloader: str) -> str:
        if self._settings.ytdlp_external_downloader_args:
            return self._settings.ytdlp_external_downloader_args
        if self._settings.ytdlp_external_downloader.strip().lower() == "auto" and Path(downloader).name == "aria2c":
            return _ARIA2C_DEFAULT_DOWNLOADER_ARGS
        return ""

    def _delegate_ffmpeg_postprocessor_args(self, *, audio_only: bool) -> list[str]:
        postprocessor = "ExtractAudio+ffmpeg" if audio_only else "Merger+ffmpeg"
        return ["--postprocessor-args", f"{postprocessor}:-threads {self._settings.ffmpeg_threads}"]

    def _run_delegate_download(self, *, job_id: str, command: list[str]) -> None:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise DownloadAppError("yt-dlp binary not found", "当前下载器暂时不可用。") from exc

        output_lines: list[str] = []
        state_lock = threading.Lock()
        last_progress_at: float | None = None
        last_reported_at = 0.0
        last_reported_progress: int | None = None
        last_reported_bytes: int | None = None
        reader_error: Exception | None = None

        def consume_progress() -> None:
            nonlocal last_progress_at, last_reported_at, last_reported_progress, last_reported_bytes, reader_error
            stdout_stream = getattr(process, "stdout", None)
            stderr_stream = getattr(process, "stderr", None)
            stream = stdout_stream if stdout_stream is not None else stderr_stream
            if stream is None:
                return
            try:
                for raw_line in stream:
                    line = raw_line.strip()
                    if not line:
                        continue
                    with state_lock:
                        output_lines.append(line)
                        if len(output_lines) > _MAX_DELEGATE_STDERR_LINES:
                            del output_lines[:-_MAX_DELEGATE_STDERR_LINES]
                    parsed = self._parse_ytdlp_progress(line)
                    if parsed is None:
                        continue
                    with state_lock:
                        last_progress_at = time.monotonic()
                    downloaded_bytes, total_bytes, speed_bytes_per_sec, eta_seconds, progress = parsed
                    force = total_bytes is not None and downloaded_bytes is not None and downloaded_bytes >= total_bytes
                    now = time.monotonic()
                    if not force and last_reported_progress == progress and last_reported_bytes == downloaded_bytes:
                        continue
                    if not force and now - last_reported_at < _PROGRESS_UPDATE_INTERVAL_SECONDS:
                        continue
                    self._update_download_progress(
                        job_id,
                        downloaded_bytes=downloaded_bytes,
                        total_bytes=total_bytes,
                        speed_bytes_per_sec=speed_bytes_per_sec,
                        eta_seconds=eta_seconds,
                        progress=progress,
                    )
                    last_reported_at = now
                    last_reported_progress = progress
                    last_reported_bytes = downloaded_bytes
            except Exception as exc:  # pragma: no cover - defensive guard around parser thread
                with state_lock:
                    reader_error = exc

        reader = threading.Thread(target=consume_progress, name=f"yt-dlp-progress-{job_id}", daemon=True)
        reader.start()
        try:
            while True:
                try:
                    return_code = process.wait(timeout=self._settings.provider_timeout_seconds)
                    break
                except subprocess.TimeoutExpired as exc:
                    current = self._jobs.get(job_id)
                    if current is None or current.status == JobStatus.CANCELED:
                        self._terminate_delegate_process(process)
                        raise DownloadAppError("yt-dlp download canceled", "任务已取消。") from exc
                    with state_lock:
                        progress_at = last_progress_at
                    if progress_at is not None and time.monotonic() - progress_at < self._settings.provider_timeout_seconds:
                        continue
                    self._terminate_delegate_process(process)
                    reader.join(timeout=1)
                    raise DownloadAppError("yt-dlp download timed out", "下载长时间没有进展，请稍后重试。") from exc
        finally:
            reader.join(timeout=1)
            stdout_stream = getattr(process, "stdout", None)
            if stdout_stream is not None:
                stdout_stream.close()
            stderr_stream = getattr(process, "stderr", None)
            if stderr_stream is not None:
                stderr_stream.close()

        with state_lock:
            captured_reader_error = reader_error
            captured_output_lines = list(output_lines)
        if captured_reader_error is not None:
            raise DownloadAppError("yt-dlp progress parsing failed") from captured_reader_error
        if return_code != 0:
            error_text = self._delegate_error_message(captured_output_lines) or "yt-dlp delegated download failed"
            raise DownloadAppError(ytdlp_safe_error_text(error_text), ytdlp_user_message(error_text))

    def _extract_mp3(self, *, job_id: str, title: str | None, source_path: Path) -> Path:
        output_path = self._artifact_output_dir(JobType.AUDIO_DOWNLOAD) / f"{job_id}.audio.mp3"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self._settings.ffmpeg_binary,
            "-threads",
            str(self._settings.ffmpeg_threads),
            "-i",
            str(source_path),
            "-vn",
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "2",
            "-y",
            str(output_path),
        ]
        try:
            subprocess.run(command, capture_output=True, text=True, check=True, timeout=self._settings.provider_timeout_seconds)
            if output_path.stat().st_size > self._settings.download_max_bytes:
                self._cleanup_file(output_path)
                raise DownloadAppError("extracted mp3 exceeds size limit")
            return self._finalize_delegate_artifact(output_path, title=title, fallback=job_id)
        except FileNotFoundError as exc:
            self._cleanup_file(output_path)
            raise DownloadAppError("ffmpeg binary not found", "当前音频转换器暂时不可用。") from exc
        except subprocess.TimeoutExpired as exc:
            self._cleanup_file(output_path)
            raise DownloadAppError("mp3 extraction timed out", "转换 MP3 超时。") from exc
        except subprocess.CalledProcessError as exc:
            self._cleanup_file(output_path)
            raise DownloadAppError("mp3 extraction failed", "转换 MP3 失败。") from exc
        finally:
            self._cleanup_file(source_path)

    def _update_download_progress(
        self,
        job_id: str,
        *,
        downloaded_bytes: int | None,
        total_bytes: int | None,
        speed_bytes_per_sec: int | None,
        eta_seconds: int | None,
        progress: int,
    ) -> None:
        normalized_total = total_bytes if total_bytes is not None and total_bytes > 0 else None
        normalized_downloaded = downloaded_bytes if downloaded_bytes is not None and downloaded_bytes >= 0 else None
        if normalized_downloaded is not None and normalized_total is not None:
            normalized_downloaded = min(normalized_downloaded, normalized_total)
        current = self._jobs.get(job_id)
        if current is not None:
            if current.downloaded_bytes is not None:
                normalized_downloaded = max(normalized_downloaded or 0, current.downloaded_bytes)
            if normalized_total is None:
                normalized_total = current.total_bytes
            elif current.total_bytes is not None:
                normalized_total = max(normalized_total, current.total_bytes)
        self._jobs.transition_status(
            job_id,
            from_statuses={JobStatus.DOWNLOADING},
            to_status=JobStatus.DOWNLOADING,
            progress=max(progress, current.progress if current else 0),
            downloaded_bytes=normalized_downloaded,
            total_bytes=normalized_total,
            speed_bytes_per_sec=speed_bytes_per_sec,
            eta_seconds=eta_seconds,
        )

    def _progress_from_bytes(self, downloaded_bytes: int, total_bytes: int | None) -> int:
        if total_bytes is None or total_bytes <= 0:
            bounded_downloaded_bytes = max(downloaded_bytes, 0)
            if bounded_downloaded_bytes == 0:
                return 45
            progress_delta = int(math.log2((bounded_downloaded_bytes / (256 * 1024)) + 1) * 4)
            return min(89, 45 + progress_delta)
        bounded_downloaded_bytes = min(max(downloaded_bytes, 0), total_bytes)
        percent = (bounded_downloaded_bytes / total_bytes) * 100
        return self._progress_from_percent(percent)

    def _progress_from_percent(self, percent: float) -> int:
        bounded_percent = min(max(percent, 0.0), 100.0)
        return min(89, 45 + int((bounded_percent / 100) * 44))

    def _parse_ytdlp_progress(self, line: str) -> tuple[int | None, int | None, int | None, int | None, int] | None:
        if not line.startswith("[download]"):
            return None
        if "Destination:" in line or "has already been downloaded" in line:
            return None

        percent_match = _YTDLP_PERCENT_RE.search(line)
        if percent_match is not None:
            percent = float(percent_match.group("percent"))
            total_match = _YTDLP_TOTAL_RE.search(line)
            total_bytes = self._parse_human_size(total_match.group("size")) if total_match is not None else None
            downloaded_bytes = None
            if total_bytes is not None:
                downloaded_bytes = min(total_bytes, int(total_bytes * (percent / 100)))
            return (
                downloaded_bytes,
                total_bytes,
                self._parse_human_speed(line),
                self._parse_eta_seconds(line),
                self._progress_from_percent(percent),
            )

        downloaded_match = _YTDLP_DOWNLOADED_RE.search(line)
        if downloaded_match is None:
            return None
        downloaded_bytes = self._parse_human_size(downloaded_match.group("size"))
        return (
            downloaded_bytes,
            None,
            self._parse_human_speed(line),
            self._parse_eta_seconds(line),
            self._progress_from_bytes(downloaded_bytes, None),
        )

    def _parse_human_speed(self, line: str) -> int | None:
        speed_match = _YTDLP_SPEED_RE.search(line)
        if speed_match is None:
            return None
        return self._parse_human_size(speed_match.group("size"))

    def _parse_eta_seconds(self, line: str) -> int | None:
        eta_match = _YTDLP_ETA_RE.search(line)
        if eta_match is None:
            return None
        parts = [int(part) for part in eta_match.group("eta").split(":")]
        if len(parts) == 2:
            minutes, seconds = parts
            return minutes * 60 + seconds
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return hours * 3600 + minutes * 60 + seconds
        return None

    def _parse_human_size(self, value: str) -> int | None:
        match = re.fullmatch(r"(?P<number>\d+(?:\.\d+)?)(?P<unit>[KMGTPE]?i?B|B)", value)
        if match is None:
            return None
        number = float(match.group("number"))
        unit = match.group("unit")
        multipliers = {
            "B": 1,
            "KB": 1000,
            "MB": 1000**2,
            "GB": 1000**3,
            "TB": 1000**4,
            "PB": 1000**5,
            "KiB": 1024,
            "MiB": 1024**2,
            "GiB": 1024**3,
            "TiB": 1024**4,
            "PiB": 1024**5,
        }
        return int(number * multipliers[unit])

    def _should_generate_thumbnail(self, job_type: JobType, mime_type: str, media_details: dict[str, float | int | str | None]) -> bool:
        return job_type == JobType.DOWNLOAD and media_details.get("video_codec") is not None

    def _generate_video_thumbnail(self, job_id: str, artifact_path: Path, duration_seconds: object) -> Path | None:
        thumbnail_path = self._safe_child_dir("Thumbnails") / f"{job_id}.thumbnail.jpg"
        seek_seconds = 1.0
        if isinstance(duration_seconds, int | float) and duration_seconds > 0:
            seek_seconds = min(max(float(duration_seconds) * 0.1, 1.0), 10.0)
        command = [
            self._settings.ffmpeg_binary,
            "-ss",
            f"{seek_seconds:.2f}",
            "-i",
            str(artifact_path),
            "-frames:v",
            "1",
            "-vf",
            "scale='min(640,iw)':-1",
            "-q:v",
            "3",
            "-y",
            str(thumbnail_path),
        ]
        try:
            subprocess.run(command, capture_output=True, text=True, check=True, timeout=30)
            return thumbnail_path if thumbnail_path.exists() else None
        except Exception:
            logger.debug("thumbnail generation failed for artifact path=%s", artifact_path, exc_info=True)
            self._cleanup_file(thumbnail_path)
            return None

    def _probe_media_details(self, artifact_path: Path) -> dict[str, float | int | str | None]:
        command = [
            self._ffprobe_binary(),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(artifact_path),
        ]
        try:
            result = subprocess_run(command, capture_output=True, check=True, timeout=30)
            payload = json.loads(result.stdout.decode("utf-8"))
        except Exception:
            logger.debug("ffprobe failed for artifact path=%s", artifact_path, exc_info=True)
            return {}
        return self._media_details_from_probe(payload)

    def _media_details_from_probe(self, payload: dict) -> dict[str, float | int | str | None]:
        streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
        format_data = payload.get("format") if isinstance(payload.get("format"), dict) else {}
        video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
        audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
        return {
            "duration_seconds": self._parse_optional_float(format_data.get("duration")),
            "width": self._parse_optional_int(video_stream.get("width")),
            "height": self._parse_optional_int(video_stream.get("height")),
            "video_codec": video_stream.get("codec_name") if isinstance(video_stream.get("codec_name"), str) else None,
            "audio_codec": audio_stream.get("codec_name") if isinstance(audio_stream.get("codec_name"), str) else None,
            "bitrate_kbps": self._bitrate_kbps(format_data.get("bit_rate")),
            "container_format": format_data.get("format_name") if isinstance(format_data.get("format_name"), str) else None,
        }

    def _ffprobe_binary(self) -> str:
        ffmpeg_path = Path(self._settings.ffmpeg_binary)
        if ffmpeg_path.name == "ffmpeg":
            return str(ffmpeg_path.with_name("ffprobe"))
        return "ffprobe"

    def _bitrate_kbps(self, value: object) -> int | None:
        bitrate = self._parse_optional_int(value)
        if bitrate is None:
            return None
        return max(1, bitrate // 1000)

    def _parse_optional_float(self, value: object) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _parse_optional_int(self, value: object) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _delegate_error_message(self, lines: list[str]) -> str:
        for line in reversed(lines):
            if line.startswith("ERROR:"):
                return line.removeprefix("ERROR:").strip()

        ignored_prefixes = ("[download]", "[Merger]", "[Metadata]", "[VideoRemuxer]", "[ExtractAudio]")
        for line in reversed(lines):
            if line.startswith(ignored_prefixes) or line.startswith("WARNING:"):
                continue
            return line.strip()
        return ""

    def _terminate_delegate_process(self, process: subprocess.Popen[str]) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()

    def _find_delegate_output(self, job_id: str) -> Path | None:
        matches = sorted(self._settings.artifacts_dir.glob(f"{job_id}.*"))
        for match in matches:
            if match.name.endswith(".part") or not match.is_file():
                continue
            ext = match.suffix.lstrip(".").lower()
            if ext not in _ALLOWED_ARTIFACT_EXTENSIONS:
                self._cleanup_file(match)
                raise DownloadAppError("delegated download produced unsupported file type")
            return match
        return None

    def _delegate_format_args(
        self,
        *,
        source_url: str,
        ext: str,
        audio_only: bool = False,
        selected_quality: str | None = None,
    ) -> list[str]:
        if audio_only:
            return ["-f", "bestaudio/best", "-x", "--audio-format", "mp3"]
        normalized_ext = self._normalize_extension(ext)
        strategy = self._delegate_format_strategy(
            source_url=source_url,
            ext=normalized_ext,
            selected_quality=selected_quality,
        )
        if normalized_ext == "mp4" and strategy == "speed":
            return ["-f", _FAST_MP4_FORMAT_SELECTOR, "--merge-output-format", "mp4"]
        if normalized_ext == "mp4" and (strategy == "quality" or detect_source_platform(source_url) in {"x", "youtube"}):
            return ["-f", _BEST_MP4_FORMAT_SELECTOR, "--merge-output-format", "mp4"]
        return ["--merge-output-format", normalized_ext]

    def _delegate_format_strategy(self, *, source_url: str, ext: str, selected_quality: str | None) -> str:
        selected_strategy = self._selected_quality_strategy(selected_quality)
        if selected_strategy is not None:
            return selected_strategy
        strategy = self._settings.ytdlp_format_strategy
        if strategy == "adaptive" and ext == "mp4" and detect_source_platform(source_url) in {"x", "bilibili", "youtube", "pipixia"}:
            return "speed"
        return strategy

    def _selected_quality_strategy(self, selected_quality: str | None) -> str | None:
        if selected_quality is None:
            return None
        normalized = selected_quality.strip().lower().replace("-", "_")
        if normalized in {"speed", "fast", "quick"}:
            return "speed"
        if normalized in {"quality", "best", "best_quality", "highest"}:
            return "quality"
        return None

    def _safe_artifact_stem(self, title: str | None, *, fallback: str) -> str:
        cleaned = _ARTIFACT_INVALID_CHARS_RE.sub(" ", title or "")
        cleaned = _ARTIFACT_WHITESPACE_RE.sub(" ", cleaned).strip(" .")
        if not cleaned:
            return fallback
        return cleaned[:120].rstrip(" .") or fallback

    def _safe_child_dir(self, *parts: str) -> Path:
        root = self._settings.artifacts_dir.resolve()
        directory = self._settings.artifacts_dir.joinpath(*parts)
        directory.mkdir(parents=True, exist_ok=True)
        resolved = directory.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise DownloadAppError("artifact output directory is unsafe", "文件目录配置异常。") from exc
        return resolved

    def _artifact_output_dir(self, job_type: JobType) -> Path:
        return self._safe_child_dir("Audio" if job_type == JobType.AUDIO_DOWNLOAD else "Videos")

    def _delegate_artifact_output_dir(self, ext: str) -> Path:
        normalized_ext = self._normalize_extension(ext)
        audio_extensions = {"mp3", "m4a", "aac", "flac", "wav"}
        return self._safe_child_dir("Audio" if normalized_ext in audio_extensions else "Videos")

    def _allocate_artifact_path(self, *, stem: str, ext: str, directory: Path | None = None) -> Path:
        normalized_ext = self._normalize_extension(ext)
        output_dir = directory or self._settings.artifacts_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        index = 0
        while True:
            suffix = "" if index == 0 else f" ({index})"
            candidate = output_dir / f"{stem}{suffix}.{normalized_ext}"
            try:
                candidate.touch(exist_ok=False)
                return candidate
            except FileExistsError:
                index += 1

    def _finalize_delegate_artifact(self, path: Path, *, title: str | None, fallback: str) -> Path:
        ext = path.suffix.lstrip(".") or "mp4"
        stem = self._safe_artifact_stem(title, fallback=fallback)
        output_dir = self._delegate_artifact_output_dir(ext)
        if path.parent == output_dir and path.stem == stem and self._normalize_extension(ext) == ext:
            return path
        final_path = self._allocate_artifact_path(stem=stem, ext=ext, directory=output_dir)
        try:
            final_path.unlink()
            shutil.move(str(path), str(final_path))
        except Exception:
            self._cleanup_file(final_path)
            raise
        return final_path

    def _resolve_ffmpeg_location(self) -> str:
        candidate = self._settings.ffmpeg_binary
        candidate_path = Path(candidate)
        if candidate_path.is_dir():
            return str(candidate_path)
        if os.path.isfile(candidate):
            return str(candidate_path.parent)
        resolved = shutil.which(candidate)
        if resolved is not None:
            return str(Path(resolved).parent)
        return candidate

    def _normalize_extension(self, ext: str) -> str:
        cleaned = ext.split("?", maxsplit=1)[0].strip().strip(".").lower()
        if not re.fullmatch(r"[a-z0-9]{1,10}", cleaned) or cleaned not in _ALLOWED_ARTIFACT_EXTENSIONS:
            return "mp4"
        return cleaned

    def _cleanup_file(self, path: Path) -> None:
        artifacts_root = self._settings.artifacts_dir.resolve()
        try:
            resolved_path = path.resolve()
            resolved_path.relative_to(artifacts_root)
        except ValueError:
            return
        if resolved_path.exists() and resolved_path.is_file():
            resolved_path.unlink()

    def _cleanup_delegate_partials(self, job_id: str) -> None:
        for path in self._settings.artifacts_dir.glob(f"{job_id}*.part"):
            self._cleanup_file(path)

    def _cleanup_delegate_outputs(self, job_id: str, *, keep_partials: bool = False) -> None:
        for path in self._settings.artifacts_dir.glob(f"{job_id}.*"):
            if keep_partials and path.name.endswith(".part"):
                continue
            self._cleanup_file(path)
