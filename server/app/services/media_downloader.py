from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import json
import math
import os
from ipaddress import ip_address
from pathlib import Path
import random
import socket
import ssl
import threading
import time
from typing import BinaryIO, Callable
from urllib.parse import urlparse, urlunparse

from app.core.errors import DownloadAppError

_CHUNK_SIZE = 1024 * 1024
_MAX_HEADER_BYTES = 16 * 1024
_MAX_LINE_BYTES = 4 * 1024
_SEGMENT_MANIFEST_VERSION = 1

ProgressCallback = Callable[[int, int | None, int | None, int | None], None]


@dataclass(frozen=True, slots=True)
class _Segment:
    index: int
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


@dataclass(frozen=True, slots=True)
class _RangeProbe:
    total_bytes: int
    etag: str | None = None
    last_modified: str | None = None


@dataclass(slots=True)
class _ProgressTracker:
    callback: ProgressCallback | None
    total_bytes: int | None
    downloaded_bytes: int = 0
    started_at: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def advance(self, chunk_size: int) -> None:
        with self.lock:
            self.downloaded_bytes += chunk_size
            downloaded_bytes = self.downloaded_bytes
            elapsed = max(time.monotonic() - self.started_at, 0.001)
            speed_bytes_per_sec = max(1, int(downloaded_bytes / elapsed))
            eta_seconds: int | None = None
            if self.total_bytes is not None and speed_bytes_per_sec > 0:
                remaining_bytes = max(0, self.total_bytes - downloaded_bytes)
                eta_seconds = int(remaining_bytes / speed_bytes_per_sec)
        if self.callback is None:
            return
        self.callback(
            downloaded_bytes,
            self.total_bytes,
            speed_bytes_per_sec,
            eta_seconds,
        )


class MediaDownloader:
    def __init__(
        self,
        *,
        max_bytes: int = 512 * 1024 * 1024,
        max_connections: int = 1,
        segment_min_bytes: int = 8 * 1024 * 1024,
        segment_size_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        self._max_bytes = max_bytes
        self._max_connections = max(1, max_connections)
        self._segment_min_bytes = max(1, segment_min_bytes)
        self._segment_size_bytes = max(1, segment_size_bytes)

    def download(
        self,
        *,
        url: str,
        allowed_addresses: tuple[str, ...],
        output_path: Path,
        timeout_seconds: int,
        progress_callback: ProgressCallback | None = None,
    ) -> None:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            raise DownloadAppError("download URL missing host")
        self._ensure_safe_http_component(host)
        self._ensure_safe_http_component(parsed.path)
        self._ensure_safe_http_component(parsed.params)
        self._ensure_safe_http_component(parsed.query)

        port = parsed.port or 443
        path_and_query = urlunparse(("", "", parsed.path or "/", parsed.params, parsed.query, ""))

        context = ssl.create_default_context()
        address_order = [self._validate_allowed_address(address) for address in allowed_addresses]
        random.shuffle(address_order)
        last_error: OSError | ssl.SSLError | None = None

        for address in address_order:
            try:
                self._download_from_address(
                    host=host,
                    port=port,
                    path_and_query=path_and_query,
                    address=address,
                    output_path=output_path,
                    timeout_seconds=timeout_seconds,
                    ssl_context=context,
                    progress_callback=progress_callback,
                )
                return
            except DownloadAppError:
                if output_path.exists():
                    output_path.unlink()
                raise
            except (OSError, ssl.SSLError) as exc:
                last_error = exc
                if output_path.exists():
                    output_path.unlink()

        raise DownloadAppError("failed to download media") from last_error

    def _download_from_address(
        self,
        *,
        host: str,
        port: int,
        path_and_query: str,
        address: str,
        output_path: Path,
        timeout_seconds: int,
        ssl_context: ssl.SSLContext,
        progress_callback: ProgressCallback | None,
    ) -> None:
        if self._max_connections > 1:
            probe = self._probe_range_support(
                host=host,
                port=port,
                path_and_query=path_and_query,
                address=address,
                timeout_seconds=timeout_seconds,
                ssl_context=ssl_context,
            )
            if probe is not None:
                try:
                    self._download_segmented_from_address(
                        host=host,
                        port=port,
                        path_and_query=path_and_query,
                        address=address,
                        output_path=output_path,
                        timeout_seconds=timeout_seconds,
                        ssl_context=ssl_context,
                        total_bytes=probe.total_bytes,
                        etag=probe.etag,
                        last_modified=probe.last_modified,
                        progress_callback=progress_callback,
                    )
                    return
                except DownloadAppError as exc:
                    if not self._should_fallback_to_single_download(exc):
                        raise
                    self._download_single_from_address(
                        host=host,
                        port=port,
                        path_and_query=path_and_query,
                        address=address,
                        output_path=output_path,
                        timeout_seconds=timeout_seconds,
                        ssl_context=ssl_context,
                        progress_callback=progress_callback,
                    )
                    part_paths = [self._segment_part_path(output_path, segment) for segment in self._build_segments(probe.total_bytes)]
                    self._cleanup_segment_artifacts(part_paths, self._segment_manifest_path(output_path))
                    return

        self._download_single_from_address(
            host=host,
            port=port,
            path_and_query=path_and_query,
            address=address,
            output_path=output_path,
            timeout_seconds=timeout_seconds,
            ssl_context=ssl_context,
            progress_callback=progress_callback,
        )

    def _download_single_from_address(
        self,
        *,
        host: str,
        port: int,
        path_and_query: str,
        address: str,
        output_path: Path,
        timeout_seconds: int,
        ssl_context: ssl.SSLContext,
        progress_callback: ProgressCallback | None,
    ) -> None:
        with socket.create_connection((address, port), timeout=timeout_seconds) as tcp_socket:
            with ssl_context.wrap_socket(tcp_socket, server_hostname=host) as tls_socket:
                self._send_request(tls_socket, method="GET", host=host, path_and_query=path_and_query)
                tls_socket.settimeout(timeout_seconds)
                with self._open_output_file(output_path) as output_file:
                    self._read_response_body(tls_socket, output_file, progress_callback)

    def _should_fallback_to_single_download(self, exc: DownloadAppError) -> bool:
        return exc.message not in {
            "temporary download path already exists",
            "failed to create temporary download file",
            "media file is too large",
        }

    def _probe_range_support(
        self,
        *,
        host: str,
        port: int,
        path_and_query: str,
        address: str,
        timeout_seconds: int,
        ssl_context: ssl.SSLContext,
    ) -> _RangeProbe | None:
        try:
            with socket.create_connection((address, port), timeout=timeout_seconds) as tcp_socket:
                with ssl_context.wrap_socket(tcp_socket, server_hostname=host) as tls_socket:
                    self._send_request(tls_socket, method="HEAD", host=host, path_and_query=path_and_query)
                    tls_socket.settimeout(timeout_seconds)
                    status_code, headers, _ = self._read_headers(tls_socket)
        except DownloadAppError:
            return None

        if status_code != 200:
            return None
        try:
            content_length = self._parse_content_length(headers)
        except DownloadAppError:
            return None
        if content_length is None:
            return None
        if content_length > self._max_bytes:
            raise DownloadAppError("media file is too large")
        if content_length < self._segment_min_bytes or content_length <= self._segment_size_bytes:
            return None
        if "bytes" not in headers.get("accept-ranges", "").lower():
            return None
        if headers.get("content-encoding"):
            return None
        return _RangeProbe(
            total_bytes=content_length,
            etag=headers.get("etag"),
            last_modified=headers.get("last-modified"),
        )

    def _download_segmented_from_address(
        self,
        *,
        host: str,
        port: int,
        path_and_query: str,
        address: str,
        output_path: Path,
        timeout_seconds: int,
        ssl_context: ssl.SSLContext,
        total_bytes: int,
        etag: str | None,
        last_modified: str | None,
        progress_callback: ProgressCallback | None,
    ) -> None:
        if output_path.exists():
            raise DownloadAppError("temporary download path already exists")

        segments = self._build_segments(total_bytes)
        part_paths = [self._segment_part_path(output_path, segment) for segment in segments]
        manifest_path = self._prepare_segment_manifest(
            output_path=output_path,
            resource=f"{host}{path_and_query}",
            total_bytes=total_bytes,
            etag=etag,
            last_modified=last_modified,
            segments=segments,
            part_paths=part_paths,
        )
        complete_segments = [
            segment
            for segment, part_path in zip(segments, part_paths, strict=True)
            if self._is_complete_segment_file(part_path, segment)
        ]
        complete_indexes = {segment.index for segment in complete_segments}
        pending_segments = [
            (segment, part_path)
            for segment, part_path in zip(segments, part_paths, strict=True)
            if segment.index not in complete_indexes
        ]
        resumed_bytes = sum(segment.length for segment in complete_segments)

        tracker = _ProgressTracker(
            callback=progress_callback,
            total_bytes=total_bytes,
            downloaded_bytes=resumed_bytes,
        )
        try:
            if pending_segments:
                with ThreadPoolExecutor(max_workers=min(self._max_connections, len(pending_segments))) as executor:
                    futures = [
                        executor.submit(
                            self._download_segment,
                            host=host,
                            port=port,
                            path_and_query=path_and_query,
                            address=address,
                            output_path=part_path,
                            timeout_seconds=timeout_seconds,
                            ssl_context=ssl_context,
                            segment=segment,
                            tracker=tracker,
                        )
                        for segment, part_path in pending_segments
                    ]
                    for future in as_completed(futures):
                        future.result()
            self._combine_segments(part_paths, output_path)
            self._cleanup_segment_artifacts(part_paths, manifest_path)
        except DownloadAppError:
            self._cleanup_segmented_output(output_path)
            raise
        except Exception as exc:
            self._cleanup_segmented_output(output_path)
            raise DownloadAppError("segmented download failed") from exc

    def _download_segment(
        self,
        *,
        host: str,
        port: int,
        path_and_query: str,
        address: str,
        output_path: Path,
        timeout_seconds: int,
        ssl_context: ssl.SSLContext,
        segment: _Segment,
        tracker: _ProgressTracker,
    ) -> None:
        with socket.create_connection((address, port), timeout=timeout_seconds) as tcp_socket:
            with ssl_context.wrap_socket(tcp_socket, server_hostname=host) as tls_socket:
                self._send_request(
                    tls_socket,
                    method="GET",
                    host=host,
                    path_and_query=path_and_query,
                    extra_headers=(f"Range: bytes={segment.start}-{segment.end}",),
                )
                tls_socket.settimeout(timeout_seconds)
                status_code, headers, remaining = self._read_headers(tls_socket)
                if status_code != 206:
                    raise DownloadAppError(f"unexpected ranged download status {status_code}")
                content_length = self._parse_content_length(headers)
                if content_length is not None and content_length != segment.length:
                    raise DownloadAppError("ranged download length mismatch")
                content_range = headers.get("content-range")
                if content_range and not content_range.lower().startswith(f"bytes {segment.start}-{segment.end}/"):
                    raise DownloadAppError("ranged download content range mismatch")
                with self._open_output_file(output_path) as output_file:
                    if content_length is None:
                        self._read_until_close(tls_socket, output_file, remaining, tracker)
                    else:
                        self._read_fixed_body(tls_socket, output_file, remaining, content_length, tracker)
        if output_path.stat().st_size != segment.length:
            raise DownloadAppError("ranged download length mismatch")

    def _send_request(
        self,
        tls_socket: ssl.SSLSocket,
        *,
        method: str,
        host: str,
        path_and_query: str,
        extra_headers: tuple[str, ...] = (),
    ) -> None:
        header_lines = [
            f"{method} {path_and_query} HTTP/1.1",
            f"Host: {host}",
            "User-Agent: XDownloader/0.1",
            "Connection: close",
            "Accept: */*",
            *extra_headers,
        ]
        request = "\r\n".join(header_lines) + "\r\n\r\n"
        tls_socket.sendall(request.encode("ascii"))

    def _read_response_body(
        self,
        tls_socket: ssl.SSLSocket,
        output_file: BinaryIO,
        progress_callback: ProgressCallback | None,
    ) -> None:
        status_code, headers, remaining = self._read_headers(tls_socket)
        if status_code != 200:
            raise DownloadAppError(f"unexpected download status {status_code}")

        transfer_encoding = headers.get("transfer-encoding", "").lower()
        declared_length = self._parse_content_length(headers)
        if declared_length is not None:
            if declared_length > self._max_bytes:
                raise DownloadAppError("media file is too large")
        tracker = _ProgressTracker(callback=progress_callback, total_bytes=declared_length)
        if "chunked" in transfer_encoding:
            self._read_chunked_body(tls_socket, output_file, remaining, tracker)
            return
        if declared_length is not None:
            self._read_fixed_body(tls_socket, output_file, remaining, declared_length, tracker)
            return
        self._read_until_close(tls_socket, output_file, remaining, tracker)

    def _parse_content_length(self, headers: dict[str, str]) -> int | None:
        content_length = headers.get("content-length")
        if content_length is None:
            return None
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise DownloadAppError("invalid content length") from exc
        if declared_length < 0:
            raise DownloadAppError("invalid content length")
        return declared_length

    def _build_segments(self, total_bytes: int) -> list[_Segment]:
        segment_count = min(self._max_connections, math.ceil(total_bytes / self._segment_size_bytes))
        segments: list[_Segment] = []
        segment_size = math.ceil(total_bytes / segment_count)
        start = 0
        for index in range(segment_count):
            end = min(total_bytes - 1, start + segment_size - 1)
            segments.append(_Segment(index=index, start=start, end=end))
            start = end + 1
        return segments

    def _combine_segments(self, part_paths: list[Path], output_path: Path) -> None:
        merge_path = output_path.with_name(f"{output_path.name}.merge")
        self._unlink_if_exists(merge_path)
        try:
            with self._open_output_file(merge_path) as output_file:
                for part_path in part_paths:
                    with part_path.open("rb") as part_file:
                        while True:
                            chunk = part_file.read(_CHUNK_SIZE)
                            if not chunk:
                                break
                            output_file.write(chunk)
            merge_path.replace(output_path)
        except Exception:
            self._unlink_if_exists(merge_path)
            raise

    def _segment_part_path(self, output_path: Path, segment: _Segment) -> Path:
        return output_path.with_name(f"{output_path.name}.segment-{segment.index}")

    def _segment_manifest_path(self, output_path: Path) -> Path:
        return output_path.with_name(f"{output_path.name}.segments.json")

    def _prepare_segment_manifest(
        self,
        *,
        output_path: Path,
        resource: str,
        total_bytes: int,
        etag: str | None,
        last_modified: str | None,
        segments: list[_Segment],
        part_paths: list[Path],
    ) -> Path:
        manifest_path = self._segment_manifest_path(output_path)
        expected_manifest = self._segment_manifest_data(
            resource=resource,
            total_bytes=total_bytes,
            etag=etag,
            last_modified=last_modified,
            segments=segments,
        )
        if manifest_path.exists():
            try:
                existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing_manifest = None
            if self._segment_manifest_matches(existing_manifest, expected_manifest):
                return manifest_path
            self._cleanup_segment_artifacts(part_paths, manifest_path)
        else:
            self._cleanup_segment_artifacts(part_paths, manifest_path)
        self._write_segment_manifest(manifest_path, expected_manifest)
        return manifest_path

    def _segment_manifest_data(
        self,
        *,
        resource: str,
        total_bytes: int,
        etag: str | None,
        last_modified: str | None,
        segments: list[_Segment],
    ) -> dict[str, object]:
        now = time.time()
        return {
            "version": _SEGMENT_MANIFEST_VERSION,
            "resource": resource,
            "total_bytes": total_bytes,
            "etag": etag,
            "last_modified": last_modified,
            "segments": [
                {
                    "index": segment.index,
                    "start": segment.start,
                    "end": segment.end,
                    "length": segment.length,
                }
                for segment in segments
            ],
            "created_at": now,
            "updated_at": now,
        }

    def _segment_manifest_matches(self, existing_manifest: object, expected_manifest: dict[str, object]) -> bool:
        if not isinstance(existing_manifest, dict):
            return False
        return (
            existing_manifest.get("version") == expected_manifest["version"]
            and existing_manifest.get("resource") == expected_manifest["resource"]
            and existing_manifest.get("total_bytes") == expected_manifest["total_bytes"]
            and existing_manifest.get("etag") == expected_manifest["etag"]
            and existing_manifest.get("last_modified") == expected_manifest["last_modified"]
            and existing_manifest.get("segments") == expected_manifest["segments"]
        )

    def _write_segment_manifest(self, manifest_path: Path, manifest: dict[str, object]) -> None:
        temp_path = manifest_path.with_name(f"{manifest_path.name}.tmp")
        self._unlink_if_exists(temp_path)
        try:
            with self._open_output_file(temp_path) as manifest_file:
                manifest_file.write(json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode("utf-8"))
            temp_path.replace(manifest_path)
        except Exception:
            self._unlink_if_exists(temp_path)
            raise

    def _is_complete_segment_file(self, part_path: Path, segment: _Segment) -> bool:
        try:
            size = part_path.stat().st_size
        except FileNotFoundError:
            return False
        if size == segment.length:
            return True
        self._unlink_if_exists(part_path)
        return False

    def _cleanup_segment_artifacts(self, part_paths: list[Path], manifest_path: Path) -> None:
        for part_path in part_paths:
            self._unlink_if_exists(part_path)
        self._unlink_if_exists(manifest_path)
        self._unlink_if_exists(manifest_path.with_name(f"{manifest_path.name}.tmp"))

    def _cleanup_segmented_output(self, output_path: Path) -> None:
        self._unlink_if_exists(output_path)
        self._unlink_if_exists(output_path.with_name(f"{output_path.name}.merge"))

    def _unlink_if_exists(self, path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            return

    def _read_headers(self, tls_socket: ssl.SSLSocket) -> tuple[int, dict[str, str], bytes]:
        buffer = bytearray()
        while b"\r\n\r\n" not in buffer:
            chunk = tls_socket.recv(_CHUNK_SIZE)
            if not chunk:
                raise DownloadAppError("download response ended before headers")
            buffer.extend(chunk)
            if len(buffer) > _MAX_HEADER_BYTES:
                raise DownloadAppError("download response headers too large")

        header_bytes, remaining = bytes(buffer).split(b"\r\n\r\n", maxsplit=1)
        lines = header_bytes.decode("iso-8859-1").split("\r\n")
        status_line = lines[0].split(" ", maxsplit=2)
        if len(status_line) < 2:
            raise DownloadAppError("invalid download response status")

        try:
            status_code = int(status_line[1])
        except ValueError as exc:
            raise DownloadAppError("invalid download response status") from exc

        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line or ":" not in line:
                continue
            key, value = line.split(":", maxsplit=1)
            headers[key.strip().lower()] = value.strip()
        return status_code, headers, remaining

    def _read_fixed_body(
        self,
        tls_socket: ssl.SSLSocket,
        output_file: BinaryIO,
        initial_body: bytes,
        content_length: int,
        tracker: _ProgressTracker,
    ) -> None:
        remaining_length = content_length
        total_written = 0
        if initial_body:
            chunk = initial_body[:remaining_length]
            total_written = self._write_chunk(output_file, chunk, total_written, tracker)
            remaining_length -= len(chunk)

        while remaining_length > 0:
            chunk = tls_socket.recv(min(_CHUNK_SIZE, remaining_length))
            if not chunk:
                raise DownloadAppError("download response ended early")
            total_written = self._write_chunk(output_file, chunk, total_written, tracker)
            remaining_length -= len(chunk)

    def _read_until_close(
        self,
        tls_socket: ssl.SSLSocket,
        output_file: BinaryIO,
        initial_body: bytes,
        tracker: _ProgressTracker,
    ) -> None:
        total_written = 0
        if initial_body:
            total_written = self._write_chunk(output_file, initial_body, total_written, tracker)
        while True:
            chunk = tls_socket.recv(_CHUNK_SIZE)
            if not chunk:
                return
            total_written = self._write_chunk(output_file, chunk, total_written, tracker)

    def _read_chunked_body(
        self,
        tls_socket: ssl.SSLSocket,
        output_file: BinaryIO,
        initial_body: bytes,
        tracker: _ProgressTracker,
    ) -> None:
        buffer = bytearray(initial_body)
        total_written = 0
        while True:
            size = self._read_chunk_size(tls_socket, buffer)
            if size == 0:
                self._consume_chunk_trailer(tls_socket, buffer)
                return
            total_written = self._read_chunk_payload(tls_socket, buffer, output_file, size, total_written, tracker)
            self._read_exact(tls_socket, buffer, 2)

    def _read_chunk_size(self, tls_socket: ssl.SSLSocket, buffer: bytearray) -> int:
        line = self._read_line(tls_socket, buffer)
        size_text = line.split(b";", maxsplit=1)[0].strip()
        try:
            return int(size_text, 16)
        except ValueError as exc:
            raise DownloadAppError("invalid chunk size") from exc

    def _consume_chunk_trailer(self, tls_socket: ssl.SSLSocket, buffer: bytearray) -> None:
        while True:
            line = self._read_line(tls_socket, buffer)
            if line == b"":
                return

    def _read_line(self, tls_socket: ssl.SSLSocket, buffer: bytearray) -> bytes:
        while b"\r\n" not in buffer:
            chunk = tls_socket.recv(_CHUNK_SIZE)
            if not chunk:
                raise DownloadAppError("download response ended early")
            buffer.extend(chunk)
            if len(buffer) > _MAX_LINE_BYTES:
                raise DownloadAppError("download response line too large")
        line, remainder = bytes(buffer).split(b"\r\n", maxsplit=1)
        buffer.clear()
        buffer.extend(remainder)
        return line

    def _read_exact(self, tls_socket: ssl.SSLSocket, buffer: bytearray, size: int) -> bytes:
        while len(buffer) < size:
            chunk = tls_socket.recv(_CHUNK_SIZE)
            if not chunk:
                raise DownloadAppError("download response ended early")
            buffer.extend(chunk)
        data = bytes(buffer[:size])
        del buffer[:size]
        return data

    def _write_chunk(
        self,
        output_file: BinaryIO,
        chunk: bytes,
        total_written: int,
        tracker: _ProgressTracker,
    ) -> int:
        next_total = total_written + len(chunk)
        if next_total > self._max_bytes:
            raise DownloadAppError("media file is too large")
        output_file.write(chunk)
        tracker.advance(len(chunk))
        return next_total

    def _read_chunk_payload(
        self,
        tls_socket: ssl.SSLSocket,
        buffer: bytearray,
        output_file: BinaryIO,
        size: int,
        total_written: int,
        tracker: _ProgressTracker,
    ) -> int:
        remaining = size
        current_total = total_written
        while remaining > 0:
            if buffer:
                chunk = bytes(buffer[:remaining])
                del buffer[: len(chunk)]
            else:
                chunk = tls_socket.recv(min(_CHUNK_SIZE, remaining))
                if not chunk:
                    raise DownloadAppError("download response ended early")
            current_total = self._write_chunk(output_file, chunk, current_total, tracker)
            remaining -= len(chunk)
        return current_total

    def _validate_allowed_address(self, address: str) -> str:
        try:
            parsed = ip_address(address)
        except ValueError as exc:
            raise DownloadAppError("allowed download address must be an IP literal") from exc
        if parsed.is_multicast or not parsed.is_global:
            raise DownloadAppError("allowed download address is not public")
        return address

    def _ensure_safe_http_component(self, value: str) -> None:
        if any(char in {"\r", "\n"} for char in value):
            raise DownloadAppError("download URL contains invalid control characters")

    def _open_output_file(self, output_path: Path) -> BinaryIO:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(output_path, flags, 0o600)
        except FileExistsError as exc:
            raise DownloadAppError("temporary download path already exists") from exc
        except OSError as exc:
            raise DownloadAppError("failed to create temporary download file") from exc
        return os.fdopen(fd, "wb")
