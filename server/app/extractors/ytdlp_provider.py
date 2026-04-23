from __future__ import annotations

import json
import subprocess
from typing import Any

from app.core.config import Settings
from app.core.errors import ProviderAppError, ProviderUnavailableError
from app.domain.models import DeliveryMode, ExtractedMedia
from app.services.url_tools import detect_source_platform, is_supported_source_url, resolve_download_url


class YtDlpProvider:
    name = "yt-dlp"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def can_handle(self, url: str) -> bool:
        return is_supported_source_url(url)

    def extract(self, url: str) -> ExtractedMedia:
        command = [
            *self._settings.yt_dlp_command,
            "--ignore-config",
            "--dump-single-json",
            "--no-warnings",
            "--skip-download",
            *self._settings.youtube_runtime_args(url),
            "--",
            url,
        ]
        try:
            result = subprocess.run(
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

        if result.returncode != 0:
            raise ProviderAppError("yt-dlp metadata extraction failed")

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

        if source_platform in {"bilibili", "youtube"}:
            return ExtractedMedia(
                provider=self.name,
                title=title,
                author_handle=author,
                thumbnail_url=thumbnail,
                direct_url=None,
                webpage_url=webpage_url,
                file_extension=self._payload_extension(payload),
                delivery_mode=DeliveryMode.DELEGATE_YTDLP,
            )

        selected_media = self._select_media_candidate(payload)
        if selected_media is None:
            raise ProviderAppError("no downloadable media found", "该帖子没有可直接下载的视频。")

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

    def _select_media_candidate(self, payload: dict[str, Any]) -> dict[str, str] | None:
        fallback_candidate: dict[str, str] | None = None
        for candidate in self._iter_media_candidates(payload):
            direct_url = candidate.get("url")
            if not isinstance(direct_url, str) or not direct_url.strip():
                continue
            if self._is_manifest_candidate(candidate):
                continue
            normalized_candidate = {
                "url": direct_url,
                "ext": self._candidate_extension(candidate, payload),
            }
            if normalized_candidate["ext"].lower() == "mp4":
                return normalized_candidate
            if fallback_candidate is None:
                fallback_candidate = normalized_candidate
        return fallback_candidate

    def _iter_media_candidates(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        candidates.append(payload)

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
        return candidates

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
