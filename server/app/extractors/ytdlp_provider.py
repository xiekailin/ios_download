from __future__ import annotations

import json
import subprocess
from typing import Any

from app.core.config import Settings
from app.core.errors import ProviderAppError, ProviderUnavailableError
from app.core.ytdlp_errors import extract_ytdlp_error_text, is_ytdlp_login_required, ytdlp_safe_error_text, ytdlp_user_message
from app.domain.models import DeliveryMode, ExtractedMedia
from app.services.url_tools import detect_source_platform, is_supported_source_url, resolve_download_url

_ALLOWED_DIRECT_EXTENSIONS = frozenset({"mp4", "mov", "webm", "m4a", "jpg", "jpeg", "png", "webp", "gif"})


class YtDlpProvider:
    name = "yt-dlp"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def can_handle(self, url: str) -> bool:
        return is_supported_source_url(url)

    def extract(self, url: str) -> ExtractedMedia:
        command = self._metadata_command(url)
        result = self._run_metadata_command(command)
        if result.returncode != 0:
            error_text = extract_ytdlp_error_text(result.stdout, result.stderr)
            retry_command = self._settings.youtube_cookie_retry_command(command, url)
            retried_with_cookie = False
            if retry_command and is_ytdlp_login_required(error_text):
                retried_with_cookie = True
                result = self._run_metadata_command(retry_command)
                if result.returncode == 0:
                    error_text = ""
                else:
                    error_text = extract_ytdlp_error_text(result.stdout, result.stderr)
            if error_text:
                raise ProviderAppError(ytdlp_safe_error_text(error_text), ytdlp_user_message(error_text, retried_with_cookie=retried_with_cookie))

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ProviderAppError("invalid yt-dlp JSON output") from exc
        if not isinstance(payload, dict):
            raise ProviderAppError("invalid yt-dlp JSON output")

        title = payload.get("title")
        author = payload.get("uploader_id") or payload.get("uploader")
        thumbnail = payload.get("thumbnail")
        webpage_url = payload.get("webpage_url") or url
        source_platform = detect_source_platform(url)

        if source_platform in {"bilibili", "youtube", "pipixia"}:
            self._validate_delegated_webpage_url(webpage_url, source_platform=source_platform)
            return self._delegate_download(payload, title=title, author=author, thumbnail=thumbnail, webpage_url=webpage_url)

        if source_platform == "x":
            self._validate_delegated_webpage_url(webpage_url, source_platform=source_platform)
            if self._has_x_video_candidate(payload):
                return self._delegate_download(payload, title=title, author=author, thumbnail=thumbnail, webpage_url=webpage_url)
            selected_media = self._select_media_candidate(payload)
            if selected_media is None and not self._has_download_url_candidate(payload):
                return self._delegate_download(payload, title=title, author=author, thumbnail=thumbnail, webpage_url=webpage_url)
        else:
            selected_media = self._select_media_candidate(payload)
        if selected_media is None:
            raise ProviderAppError("no downloadable media found", "该帖子没有可直接下载的媒体文件。")

        resolved_direct_url = resolve_download_url(selected_media["url"], base_url=webpage_url, source_url=url)
        return ExtractedMedia(
            provider=self.name,
            title=title,
            author_handle=author,
            thumbnail_url=thumbnail,
            direct_url=resolved_direct_url.url,
            direct_url_addresses=resolved_direct_url.addresses,
            webpage_url=webpage_url,
            file_extension=selected_media["ext"],
        )

    def _validate_delegated_webpage_url(self, webpage_url: Any, *, source_platform: str) -> None:
        if not isinstance(webpage_url, str) or detect_source_platform(webpage_url) != source_platform:
            raise ProviderAppError("unexpected delegated webpage URL", "该分享链接无法识别，请确认链接来自受支持平台。")

    def _delegate_download(
        self,
        payload: dict[str, Any],
        *,
        title: Any,
        author: Any,
        thumbnail: Any,
        webpage_url: str,
    ) -> ExtractedMedia:
        return ExtractedMedia(
            provider=self.name,
            title=title if isinstance(title, str) else None,
            author_handle=author if isinstance(author, str) else None,
            thumbnail_url=thumbnail if isinstance(thumbnail, str) else None,
            direct_url=None,
            webpage_url=webpage_url,
            file_extension=self._payload_extension(payload),
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )

    def _metadata_command(self, url: str) -> list[str]:
        return [
            *self._settings.yt_dlp_command,
            "--ignore-config",
            "--dump-single-json",
            "--no-warnings",
            "--skip-download",
            *self._settings.youtube_runtime_args(url),
            "--",
            url,
        ]

    def _run_metadata_command(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self._settings.provider_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ProviderUnavailableError("yt-dlp binary not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderAppError("yt-dlp timed out") from exc

    def _has_x_video_candidate(self, payload: dict[str, Any]) -> bool:
        for candidate in self._iter_media_candidates(payload):
            ext = self._candidate_extension(candidate, payload).lower()
            if ext != "mp4":
                continue
            if self._is_x_tweet_video_gif_candidate(candidate):
                continue
            vcodec = candidate.get("vcodec")
            if vcodec == "none":
                continue
            direct_url = candidate.get("url")
            if isinstance(direct_url, str) and direct_url.strip():
                return True
        return False

    def _has_download_url_candidate(self, payload: dict[str, Any]) -> bool:
        return any(isinstance(candidate.get("url"), str) and candidate["url"].strip() for candidate in self._iter_media_candidates(payload))

    def _is_x_tweet_video_gif_candidate(self, candidate: dict[str, Any]) -> bool:
        direct_url = candidate.get("url")
        if isinstance(direct_url, str) and "/tweet_video/" in direct_url:
            return True
        format_id = candidate.get("format_id")
        return isinstance(format_id, str) and "gif" in format_id.lower()

    def _select_media_candidate(self, payload: dict[str, Any]) -> dict[str, str] | None:
        fallback_candidate: dict[str, str] | None = None
        for candidate in self._iter_media_candidates(payload):
            direct_url = candidate.get("url")
            if not isinstance(direct_url, str) or not direct_url.strip():
                continue
            if self._is_manifest_candidate(candidate):
                continue
            ext = self._candidate_extension(candidate, payload).lower()
            if ext not in _ALLOWED_DIRECT_EXTENSIONS:
                continue
            normalized_candidate = {
                "url": direct_url,
                "ext": ext,
            }
            if normalized_candidate["ext"] == "mp4":
                return normalized_candidate
            if fallback_candidate is None:
                fallback_candidate = normalized_candidate
        return fallback_candidate

    def _iter_media_candidates(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        candidates.append(payload)
        self._append_nested_media_candidates(payload, candidates)

        entries = payload.get("entries")
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                candidates.append(entry)
                self._append_nested_media_candidates(entry, candidates)
        return candidates

    def _append_nested_media_candidates(self, payload: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
        requested_downloads = payload.get("requested_downloads")
        if isinstance(requested_downloads, list):
            for requested_download in requested_downloads:
                if not isinstance(requested_download, dict):
                    continue
                candidates.append(requested_download)
                requested_formats = requested_download.get("requested_formats")
                if isinstance(requested_formats, list):
                    candidates.extend(format_item for format_item in requested_formats if isinstance(format_item, dict))

        formats = payload.get("formats")
        if isinstance(formats, list):
            candidates.extend(format_item for format_item in formats if isinstance(format_item, dict))

    def _candidate_extension(self, candidate: dict[str, Any], payload: dict[str, Any]) -> str:
        ext = candidate.get("ext")
        if isinstance(ext, str) and ext:
            return ext
        return self._payload_extension(payload)

    def _payload_extension(self, payload: dict[str, Any]) -> str:
        payload_ext = payload.get("ext")
        if isinstance(payload_ext, str) and payload_ext:
            return payload_ext
        return "mp4"

    def _is_manifest_candidate(self, candidate: dict[str, Any]) -> bool:
        protocol = candidate.get("protocol")
        if isinstance(protocol, str) and protocol.startswith("m3u8"):
            return True

        direct_url = candidate.get("url")
        if not isinstance(direct_url, str):
            return True
        return ".m3u8" in direct_url.lower()
