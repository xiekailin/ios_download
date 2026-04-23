from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address
import re
import socket
from urllib.parse import ParseResult, parse_qsl, urlencode, urljoin, urlparse, urlunparse

from app.core.errors import ProviderAppError, ValidationAppError

IPAddress = IPv4Address | IPv6Address
_YOUTUBE_VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")


@dataclass(frozen=True, slots=True)
class ResolvedDownloadURL:
    url: str
    host: str
    port: int
    path_and_query: str
    addresses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourcePlatformConfig:
    source_hosts: frozenset[str]
    download_hosts: frozenset[str]
    path_matches: Callable[[str, str], bool]


def _matches_x_path(_: str, path: str) -> bool:
    return "/status/" in path


def _matches_douyin_path(host: str, path: str) -> bool:
    normalized_path = path.rstrip("/")
    if host == "www.douyin.com":
        return normalized_path.startswith("/video/")
    return normalized_path not in {"", "/"}


def _matches_xiaohongshu_path(host: str, path: str) -> bool:
    normalized_path = path.rstrip("/")
    if host == "www.xiaohongshu.com":
        return normalized_path.startswith("/explore/") or normalized_path.startswith("/discovery/item/")
    return normalized_path not in {"", "/"}


def _matches_bilibili_path(host: str, path: str) -> bool:
    normalized_path = path.rstrip("/")
    return host == "www.bilibili.com" and normalized_path.startswith("/video/BV")


def _matches_youtube_path(host: str, path: str) -> bool:
    normalized_path = path.rstrip("/")
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        return normalized_path == "/watch" or normalized_path.startswith("/shorts/")
    if host != "youtu.be":
        return False
    short_id = path.strip("/")
    return bool(short_id) and "/" not in short_id


def _youtube_video_id(parsed: ParseResult) -> str:
    host = (parsed.hostname or "").lower()
    candidate = ""
    if host == "youtu.be":
        short_id = parsed.path.strip("/")
        if short_id and "/" not in short_id:
            candidate = short_id
    elif parsed.path.rstrip("/") == "/watch":
        candidate = dict(parse_qsl(parsed.query, keep_blank_values=True)).get("v", "")
    elif parsed.path.startswith("/shorts/"):
        candidate = parsed.path.removeprefix("/shorts/").split("/", maxsplit=1)[0]
    if not _YOUTUBE_VIDEO_ID_RE.fullmatch(candidate):
        return ""
    return candidate


_SOURCE_PLATFORMS: dict[str, SourcePlatformConfig] = {
    "x": SourcePlatformConfig(
        source_hosts=frozenset({"x.com", "www.x.com", "twitter.com", "www.twitter.com"}),
        download_hosts=frozenset({"video.twimg.com", "video-cf.twimg.com", "pbs.twimg.com"}),
        path_matches=_matches_x_path,
    ),
    "douyin": SourcePlatformConfig(
        source_hosts=frozenset({"www.douyin.com", "v.douyin.com"}),
        download_hosts=frozenset({"api-play-hl.amemv.com"}),
        path_matches=_matches_douyin_path,
    ),
    "xiaohongshu": SourcePlatformConfig(
        source_hosts=frozenset({"www.xiaohongshu.com", "xhslink.com", "www.xhslink.com"}),
        download_hosts=frozenset(
            {
                "sns-video-ak.xhscdn.com",
                "sns-video-hw.xhscdn.com",
                "sns-bak-v1.xhscdn.com",
                "sns-bak-v6.xhscdn.com",
            }
        ),
        path_matches=_matches_xiaohongshu_path,
    ),
    "bilibili": SourcePlatformConfig(
        source_hosts=frozenset({"www.bilibili.com"}),
        download_hosts=frozenset(),
        path_matches=_matches_bilibili_path,
    ),
    "youtube": SourcePlatformConfig(
        source_hosts=frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}),
        download_hosts=frozenset(),
        path_matches=_matches_youtube_path,
    ),
}
_SUPPORTED_SOURCE_HOSTS = frozenset(host for config in _SOURCE_PLATFORMS.values() for host in config.source_hosts)
_ALL_ALLOWED_DOWNLOAD_HOSTS = frozenset(host for config in _SOURCE_PLATFORMS.values() for host in config.download_hosts)


def detect_source_platform(raw_url: str) -> str | None:
    parsed = urlparse(raw_url.strip())
    if parsed.scheme not in {"http", "https"}:
        return None
    return _detect_source_platform_from_parsed(parsed)


def is_supported_source_url(raw_url: str) -> bool:
    return detect_source_platform(raw_url) is not None


def normalize_source_url(raw_url: str) -> str:
    return _normalize_source_url(raw_url, for_extraction=False)


def normalize_extraction_source_url(raw_url: str) -> str:
    return _normalize_source_url(raw_url, for_extraction=True)


def _normalize_source_url(raw_url: str, *, for_extraction: bool) -> str:
    parsed = urlparse(raw_url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValidationAppError("invalid URL scheme", "链接格式不正确。")
    if parsed.username or parsed.password:
        raise ValidationAppError("unsupported URL credentials", "链接格式不正确。")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValidationAppError("invalid URL port", "链接格式不正确。") from exc
    if port not in {None, 80, 443}:
        raise ValidationAppError("unsupported URL port", "链接格式不正确。")

    host = parsed.hostname
    if not host:
        raise ValidationAppError("missing host", "链接格式不正确。")
    normalized_host = host.lower()
    platform = _detect_source_platform_from_parsed(parsed)
    if platform is None:
        if normalized_host not in _SUPPORTED_SOURCE_HOSTS:
            raise ValidationAppError("unsupported host", "目前只支持 X、抖音、小红书、Bilibili 和 YouTube 公开链接。")
        raise ValidationAppError("unsupported source URL", "请提供公开分享链接。")

    normalized_query = _normalize_source_query(parsed, platform=platform, for_extraction=for_extraction)
    normalized_path = parsed.path
    normalized_netloc = normalized_host
    if platform == "bilibili" and normalized_path != "/":
        normalized_path = normalized_path.rstrip("/")
    elif platform == "youtube":
        normalized_netloc = "www.youtube.com"
        normalized_path, normalized_query = _normalize_youtube_target(parsed)
    normalized = parsed._replace(
        scheme="https",
        netloc=normalized_netloc,
        path=normalized_path,
        query=normalized_query,
        fragment="",
    )
    return urlunparse(normalized)


def validate_download_url(raw_url: str, *, source_url: str | None = None) -> str:
    return resolve_download_url(raw_url, source_url=source_url).url


def _normalize_source_query(parsed: ParseResult, *, platform: str, for_extraction: bool) -> str:
    if platform == "xiaohongshu":
        if not for_extraction:
            return ""
        kept_keys = {"xsec_token"}
    elif platform == "bilibili":
        kept_keys = {"p"}
    elif platform == "youtube":
        return ""
    else:
        return ""
    kept_items = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key in kept_keys]
    return urlencode(kept_items, doseq=True)


def _normalize_youtube_target(parsed: ParseResult) -> tuple[str, str]:
    video_id = _youtube_video_id(parsed)
    if not video_id:
        raise ValidationAppError("unsupported source URL", "请提供公开分享链接。")
    return "/watch", urlencode({"v": video_id})


def allowed_download_hosts_for_source_url(raw_url: str) -> frozenset[str]:
    platform = detect_source_platform(raw_url)
    if platform is None:
        raise ProviderAppError("unsupported source URL")
    return _SOURCE_PLATFORMS[platform].download_hosts


def resolve_download_url(
    raw_url: str,
    *,
    base_url: str | None = None,
    source_url: str | None = None,
    allowed_hosts: frozenset[str] | set[str] | None = None,
) -> ResolvedDownloadURL:
    candidate_url = urljoin(base_url, raw_url.strip()) if base_url else raw_url.strip()
    _ensure_no_control_chars(candidate_url)
    parsed = urlparse(candidate_url)
    host = parsed.hostname
    if not host:
        raise ProviderAppError("download URL missing host")
    if parsed.username or parsed.password:
        raise ProviderAppError("download URL must not include credentials")
    _ensure_no_control_chars(host)
    _ensure_no_control_chars(parsed.path)
    _ensure_no_control_chars(parsed.params)
    _ensure_no_control_chars(parsed.query)

    normalized_host = host.lower()
    if normalized_host in {"localhost", "localhost.localdomain"}:
        raise ProviderAppError("download URL points to localhost")
    source_platform = _resolve_source_platform(source_url=source_url, base_url=base_url)
    active_allowed_hosts = _resolve_allowed_download_hosts(
        source_url=source_url,
        base_url=base_url,
        allowed_hosts=allowed_hosts,
    )
    if normalized_host not in active_allowed_hosts:
        raise ProviderAppError("download URL host is not allowed")
    if parsed.scheme == "http":
        if source_platform != "xiaohongshu":
            raise ProviderAppError("download URL must use HTTPS")
        parsed = parsed._replace(scheme="https")
    elif parsed.scheme != "https":
        raise ProviderAppError("download URL must use HTTPS")

    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise ProviderAppError("download URL port is invalid") from exc
    if port != 443:
        raise ProviderAppError("download URL port is not allowed")

    addresses = _resolve_addresses(normalized_host, port)
    for address in addresses:
        if address.is_multicast or not address.is_global:
            raise ProviderAppError("download URL points to a blocked address")

    normalized = parsed._replace(fragment="")
    path_and_query = urlunparse(("", "", parsed.path or "/", parsed.params, parsed.query, ""))
    return ResolvedDownloadURL(
        url=urlunparse(normalized),
        host=normalized_host,
        port=port,
        path_and_query=path_and_query,
        addresses=tuple(str(address) for address in addresses),
    )


def _detect_source_platform_from_parsed(parsed: ParseResult) -> str | None:
    host = parsed.hostname
    if not host:
        return None
    normalized_host = host.lower()
    path = parsed.path or "/"
    for name, config in _SOURCE_PLATFORMS.items():
        if normalized_host not in config.source_hosts or not config.path_matches(normalized_host, path):
            continue
        if name == "youtube" and not _youtube_video_id(parsed):
            return None
        return name
    return None


def _resolve_source_platform(*, source_url: str | None, base_url: str | None) -> str | None:
    if source_url is not None:
        return detect_source_platform(source_url)
    if base_url is not None:
        return detect_source_platform(base_url)
    return None


def _resolve_allowed_download_hosts(
    *,
    source_url: str | None,
    base_url: str | None,
    allowed_hosts: frozenset[str] | set[str] | None,
) -> frozenset[str] | set[str]:
    if allowed_hosts is not None:
        return allowed_hosts
    if source_url is not None:
        return allowed_download_hosts_for_source_url(source_url)
    if base_url is not None and is_supported_source_url(base_url):
        return allowed_download_hosts_for_source_url(base_url)
    return _ALL_ALLOWED_DOWNLOAD_HOSTS


def _ensure_no_control_chars(value: str) -> None:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ProviderAppError("download URL contains control characters")


def _resolve_addresses(host: str, port: int) -> tuple[IPAddress, ...]:
    try:
        return (ip_address(host),)
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ProviderAppError("download URL host could not be resolved") from exc

    addresses: list[IPAddress] = []
    seen: set[IPAddress] = set()
    for info in infos:
        address = ip_address(info[4][0])
        if address in seen:
            continue
        seen.add(address)
        addresses.append(address)
    if not addresses:
        raise ProviderAppError("download URL host has no addresses")
    return tuple(addresses)
