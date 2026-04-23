from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import logging
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import threading
import time

from app.core.config import Settings
from app.core.errors import DownloadAppError, ProviderAppError
from app.domain.models import DeliveryMode, JobStatus
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
_YOUTUBE_MP4_FORMAT_SELECTOR = "bestvideo*[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo*+bestaudio/best"
_PROGRESS_UPDATE_INTERVAL_SECONDS = 0.5
_MAX_DELEGATE_STDERR_LINES = 200


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
        artifact_id: str | None = None
        delegate_output_prefix: str | None = None
        try:
            if not self._jobs.transition_status(
                job_id,
                from_statuses={JobStatus.QUEUED},
                to_status=JobStatus.RESOLVING,
                progress=5,
            ):
                return
            job = self._jobs.get(job_id)
            if job is None:
                return
            extracted = self._selector.extract(normalize_extraction_source_url(job.source_url))
            if not self._jobs.transition_status(
                job_id,
                from_statuses={JobStatus.RESOLVING},
                to_status=JobStatus.RESOLVED,
                progress=20,
                provider=extracted.provider,
                media_title=extracted.title,
                author_handle=extracted.author_handle,
                thumbnail_url=extracted.thumbnail_url,
            ):
                return
            if not self._jobs.transition_status(
                job_id,
                from_statuses={JobStatus.RESOLVED},
                to_status=JobStatus.DOWNLOADING,
                progress=45,
                provider=extracted.provider,
                downloaded_bytes=None,
                total_bytes=None,
                speed_bytes_per_sec=None,
                eta_seconds=None,
            ):
                return
            if extracted.delivery_mode == DeliveryMode.DELEGATE_YTDLP:
                delegate_output_prefix = job_id
                artifact_path = self._download_via_ytdlp(
                    job_id=job_id,
                    title=extracted.title,
                    source_url=normalize_extraction_source_url(job.source_url),
                    ext=extracted.file_extension,
                )
            else:
                artifact_path = self._download_media(
                    job_id=job_id,
                    title=extracted.title,
                    url=extracted.direct_url,
                    allowed_addresses=extracted.direct_url_addresses,
                    ext=extracted.file_extension,
                )
            current = self._jobs.get(job_id)
            if not self._jobs.transition_status(
                job_id,
                from_statuses={JobStatus.DOWNLOADING},
                to_status=JobStatus.STORING,
                progress=90,
                provider=extracted.provider,
                downloaded_bytes=current.downloaded_bytes if current else None,
                total_bytes=current.total_bytes if current else None,
                speed_bytes_per_sec=current.speed_bytes_per_sec if current else None,
                eta_seconds=current.eta_seconds if current else None,
            ):
                self._cleanup_file(artifact_path)
                return
            artifact = self._artifacts.create(
                job_id=job_id,
                file_name=artifact_path.name,
                mime_type=mimetypes.guess_type(artifact_path.name)[0] or "application/octet-stream",
                storage_path=str(artifact_path),
                file_size=artifact_path.stat().st_size,
            )
            artifact_id = artifact.id
            completed_size = artifact.file_size
            if not self._jobs.transition_status(
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
            ):
                self._artifacts.delete(artifact.id)
                self._cleanup_file(artifact_path)
        except (ProviderAppError, DownloadAppError) as exc:
            self._mark_failed(job_id, error_code=exc.code, error_message=exc.message, user_message=exc.user_message)
            if artifact_id is not None:
                self._artifacts.delete(artifact_id)
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
            if artifact_path is not None:
                self._cleanup_file(artifact_path)
            if delegate_output_prefix is not None:
                self._cleanup_delegate_outputs(delegate_output_prefix)

    def _mark_failed(self, job_id: str, *, error_code: str, error_message: str, user_message: str) -> None:
        current = self._jobs.get(job_id)
        if current is None or current.status == JobStatus.CANCELED:
            return
        self._jobs.update_status(
            job_id,
            status=JobStatus.FAILED,
            progress=100,
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

    def _download_via_ytdlp(self, *, job_id: str, title: str | None, source_url: str, ext: str) -> Path:
        output_template = self._settings.artifacts_dir / f"{job_id}.%(ext)s"
        ffmpeg_location = self._resolve_ffmpeg_location()
        command = [
            *self._settings.yt_dlp_command,
            "--ignore-config",
            "--no-warnings",
            "--newline",
            "--no-playlist",
            *self._settings.youtube_runtime_args(source_url),
            "--max-filesize",
            str(self._settings.download_max_bytes),
            *self._delegate_format_args(source_url=source_url, ext=ext),
            "--ffmpeg-location",
            ffmpeg_location,
            "-o",
            str(output_template),
            "--",
            source_url,
        ]
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
        last_reported_at = 0.0
        last_reported_progress: int | None = None
        last_reported_bytes: int | None = None
        reader_error: Exception | None = None

        def consume_progress() -> None:
            nonlocal last_reported_at, last_reported_progress, last_reported_bytes, reader_error
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
                    output_lines.append(line)
                    if len(output_lines) > _MAX_DELEGATE_STDERR_LINES:
                        del output_lines[:-_MAX_DELEGATE_STDERR_LINES]
                    parsed = self._parse_ytdlp_progress(line)
                    if parsed is None:
                        continue
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
                reader_error = exc

        reader = threading.Thread(target=consume_progress, name=f"yt-dlp-progress-{job_id}", daemon=True)
        reader.start()
        try:
            return_code = process.wait(timeout=self._settings.provider_timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            self._terminate_delegate_process(process)
            reader.join(timeout=1)
            raise DownloadAppError("yt-dlp download timed out") from exc
        finally:
            reader.join(timeout=1)
            stdout_stream = getattr(process, "stdout", None)
            if stdout_stream is not None:
                stdout_stream.close()
            stderr_stream = getattr(process, "stderr", None)
            if stderr_stream is not None:
                stderr_stream.close()

        if reader_error is not None:
            raise DownloadAppError("yt-dlp progress parsing failed") from reader_error
        if return_code != 0:
            raise DownloadAppError(self._delegate_error_message(output_lines) or "yt-dlp delegated download failed")

        artifact_path = self._find_delegate_output(job_id)
        if artifact_path is None:
            raise DownloadAppError("yt-dlp delegated download produced no artifact")
        if artifact_path.stat().st_size > self._settings.download_max_bytes:
            self._cleanup_file(artifact_path)
            raise DownloadAppError("delegated download exceeds size limit")
        return self._finalize_delegate_artifact(artifact_path, title=title, fallback=job_id)

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
        self._jobs.transition_status(
            job_id,
            from_statuses={JobStatus.DOWNLOADING},
            to_status=JobStatus.DOWNLOADING,
            progress=progress,
            downloaded_bytes=normalized_downloaded,
            total_bytes=normalized_total,
            speed_bytes_per_sec=speed_bytes_per_sec,
            eta_seconds=eta_seconds,
        )

    def _progress_from_bytes(self, downloaded_bytes: int, total_bytes: int | None) -> int:
        if total_bytes is None or total_bytes <= 0:
            return 45
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
            45,
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
            return match
        return None

    def _delegate_format_args(self, *, source_url: str, ext: str) -> list[str]:
        if detect_source_platform(source_url) == "youtube":
            return ["-f", _YOUTUBE_MP4_FORMAT_SELECTOR, "--merge-output-format", "mp4"]
        return ["--merge-output-format", self._normalize_extension(ext)]

    def _safe_artifact_stem(self, title: str | None, *, fallback: str) -> str:
        cleaned = _ARTIFACT_INVALID_CHARS_RE.sub(" ", title or "")
        cleaned = _ARTIFACT_WHITESPACE_RE.sub(" ", cleaned).strip(" .")
        if not cleaned:
            return fallback
        return cleaned[:120].rstrip(" .") or fallback

    def _allocate_artifact_path(self, *, stem: str, ext: str) -> Path:
        normalized_ext = self._normalize_extension(ext)
        index = 0
        while True:
            suffix = "" if index == 0 else f" ({index})"
            candidate = self._settings.artifacts_dir / f"{stem}{suffix}.{normalized_ext}"
            try:
                candidate.touch(exist_ok=False)
                return candidate
            except FileExistsError:
                index += 1

    def _finalize_delegate_artifact(self, path: Path, *, title: str | None, fallback: str) -> Path:
        ext = path.suffix.lstrip(".") or "mp4"
        stem = self._safe_artifact_stem(title, fallback=fallback)
        if path.parent == self._settings.artifacts_dir and path.stem == stem and self._normalize_extension(ext) == ext:
            return path
        final_path = self._allocate_artifact_path(stem=stem, ext=ext)
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
        if not re.fullmatch(r"[a-z0-9]{1,10}", cleaned):
            return "mp4"
        return cleaned

    def _cleanup_file(self, path: Path) -> None:
        if path.exists():
            path.unlink()

    def _cleanup_delegate_outputs(self, job_id: str) -> None:
        for path in self._settings.artifacts_dir.glob(f"{job_id}.*"):
            if path.is_file():
                path.unlink()
