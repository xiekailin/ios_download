from __future__ import annotations

from dataclasses import dataclass, field
import os
from ipaddress import ip_address
from pathlib import Path
import random
import socket
import ssl
import time
from typing import BinaryIO, Callable
from urllib.parse import urlparse, urlunparse

from app.core.errors import DownloadAppError

_CHUNK_SIZE = 1024 * 1024
_MAX_HEADER_BYTES = 16 * 1024
_MAX_LINE_BYTES = 4 * 1024

ProgressCallback = Callable[[int, int | None, int | None, int | None], None]


@dataclass(slots=True)
class _ProgressTracker:
    callback: ProgressCallback | None
    total_bytes: int | None
    downloaded_bytes: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def advance(self, chunk_size: int) -> None:
        self.downloaded_bytes += chunk_size
        if self.callback is None:
            return
        elapsed = max(time.monotonic() - self.started_at, 0.001)
        speed_bytes_per_sec = max(1, int(self.downloaded_bytes / elapsed))
        eta_seconds: int | None = None
        if self.total_bytes is not None and speed_bytes_per_sec > 0:
            remaining_bytes = max(0, self.total_bytes - self.downloaded_bytes)
            eta_seconds = int(remaining_bytes / speed_bytes_per_sec)
        self.callback(
            self.downloaded_bytes,
            self.total_bytes,
            speed_bytes_per_sec,
            eta_seconds,
        )


class MediaDownloader:
    def __init__(self, *, max_bytes: int = 512 * 1024 * 1024) -> None:
        self._max_bytes = max_bytes

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
        with socket.create_connection((address, port), timeout=timeout_seconds) as tcp_socket:
            with ssl_context.wrap_socket(tcp_socket, server_hostname=host) as tls_socket:
                request = (
                    f"GET {path_and_query} HTTP/1.1\r\n"
                    f"Host: {host}\r\n"
                    "User-Agent: XDownloader/0.1\r\n"
                    "Connection: close\r\n"
                    "Accept: */*\r\n\r\n"
                )
                tls_socket.sendall(request.encode("ascii"))
                tls_socket.settimeout(timeout_seconds)
                with self._open_output_file(output_path) as output_file:
                    self._read_response_body(tls_socket, output_file, progress_callback)

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
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError as exc:
                raise DownloadAppError("invalid content length") from exc
            if declared_length > self._max_bytes:
                raise DownloadAppError("media file is too large")
        tracker = _ProgressTracker(callback=progress_callback, total_bytes=declared_length if content_length is not None else None)
        if "chunked" in transfer_encoding:
            self._read_chunked_body(tls_socket, output_file, remaining, tracker)
            return
        if content_length is not None:
            self._read_fixed_body(tls_socket, output_file, remaining, declared_length, tracker)
            return
        self._read_until_close(tls_socket, output_file, remaining, tracker)

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
