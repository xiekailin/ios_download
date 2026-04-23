from __future__ import annotations

from pathlib import Path
import signal
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.api.deps import build_container
from app.core.config import Settings
from app.core.errors import AuthorizationError, DownloadAppError, ProviderAppError
from app.domain.models import DeliveryMode, Device, ExtractedMedia, JobStatus, Platform
from app.main import app
from app.services.media_downloader import MediaDownloader
from app.services.repositories import DeviceRepository
from app.services.url_tools import (
    is_supported_source_url,
    normalize_extraction_source_url,
    normalize_source_url,
    resolve_download_url,
    validate_download_url,
)
from app.workers.download_job_worker import DownloadJobWorker


class AppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        import os

        os.environ["XDL_ENV"] = "development"
        os.environ["XDL_DATA_DIR"] = str(base / "data")
        os.environ["XDL_DATABASE_PATH"] = str(base / "data" / "app.db")
        os.environ["XDL_ARTIFACTS_DIR"] = str(base / "data" / "artifacts")
        os.environ["XDL_BOOTSTRAP_CODE"] = "test-bootstrap"
        os.environ["XDL_WORKER_ENABLED"] = "false"
        os.environ["XDL_REGISTER_RATE_LIMIT"] = "5"
        os.environ["XDL_REGISTER_WINDOW_SECONDS"] = "300"

        self.container = build_container()
        app.state.container = self.container
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.container.close()
        self.temp_dir.cleanup()

    def register_device(self) -> str:
        response = self.client.post(
            "/api/v1/devices/register",
            json={
                "device_name": "Test iPhone",
                "platform": "ios",
                "app_version": "0.1.0",
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]["access_token"]

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.register_device()}"}

    def test_register_and_authenticate_device(self) -> None:
        token = self.register_device()
        response = self.client.get("/api/v1/devices/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["name"], "Test iPhone")
        self.assertEqual(data["platform"], "ios")

    def test_reject_invalid_device_token(self) -> None:
        response = self.client.get("/api/v1/devices/me", headers={"Authorization": "Bearer invalid-token"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "authentication_failed")

    def test_reject_invalid_job_url(self) -> None:
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://example.com/not-x"},
            headers=self.auth_headers(),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation_error")

    def test_reject_source_url_with_non_default_port(self) -> None:
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com:444/demo/status/12345"},
            headers=self.auth_headers(),
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation_error")

    def test_reject_invalid_youtube_variants(self) -> None:
        headers = self.auth_headers()
        urls = [
            "https://www.youtube.com/watch",
            "https://youtu.be/",
            "https://youtu.be/shorts/GEFehFHg_os",
            "https://www.youtube.com/watch?v=abc",
            "https://www.youtube.com/watch?v=invalid$id",
            "https://www.youtube.com/shorts/abc",
            "https://youtu.be/invalid$id",
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.post("/api/v1/jobs", json={"url": url}, headers=headers)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "validation_error")

    def test_create_job(self) -> None:
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345?foo=bar#frag"},
            headers=self.auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["status"], "queued")
        self.assertEqual(data["normalized_url"], "https://x.com/demo/status/12345")

    def test_create_job_accepts_supported_platform_urls(self) -> None:
        cases = [
            ("https://www.douyin.com/video/123456?foo=bar", "https://www.douyin.com/video/123456"),
            (
                "https://www.xiaohongshu.com/explore/abcdef?xsec_token=1&xsec_source=app_share",
                "https://www.xiaohongshu.com/explore/abcdef",
            ),
            (
                "https://www.bilibili.com/video/BV1sRoHB5EHC/?spm_id_from=333.1007.tianma.1-2-2.click",
                "https://www.bilibili.com/video/BV1sRoHB5EHC",
            ),
            (
                "https://www.bilibili.com/video/BV1sRoHB5EHC/?p=2&spm_id_from=333.1007.tianma.1-2-2.click",
                "https://www.bilibili.com/video/BV1sRoHB5EHC?p=2",
            ),
            (
                "https://www.youtube.com/watch?v=GEFehFHg_os&list=PL123",
                "https://www.youtube.com/watch?v=GEFehFHg_os",
            ),
            (
                "https://youtu.be/GEFehFHg_os?t=10",
                "https://www.youtube.com/watch?v=GEFehFHg_os",
            ),
            (
                "https://www.youtube.com/shorts/GEFehFHg_os?feature=share",
                "https://www.youtube.com/watch?v=GEFehFHg_os",
            ),
        ]
        headers = self.auth_headers()
        for raw_url, normalized_url in cases:
            with self.subTest(raw_url=raw_url):
                response = self.client.post(
                    "/api/v1/jobs",
                    json={"url": raw_url},
                    headers=headers,
                )
                self.assertEqual(response.status_code, 200)
                data = response.json()["data"]
                self.assertEqual(data["status"], "queued")
                self.assertEqual(data["normalized_url"], normalized_url)

    def test_deduplicate_active_job(self) -> None:
        headers = self.auth_headers()
        first = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        second = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["data"]["id"], second.json()["data"]["id"])

    def test_delete_job_removes_terminal_record_and_artifact(self) -> None:
        headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        artifact_path = self.container.settings.artifacts_dir / "delete-me.mp4"
        artifact_path.write_bytes(b"video")
        artifact = self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="delete-me.mp4",
            mime_type="video/mp4",
            storage_path=str(artifact_path),
            file_size=artifact_path.stat().st_size,
        )
        self.container.job_service._repository.update_status(
            job_id,
            status=JobStatus.COMPLETED,
            progress=100,
            artifact_id=artifact.id,
        )

        delete_response = self.client.request("DELETE", f"/api/v1/jobs/{job_id}", headers=headers)
        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(artifact_path.exists())
        self.assertIsNone(self.container.job_service._repository.get(job_id))
        self.assertIsNone(self.container.artifact_service._artifacts.get(artifact.id))

    def test_delete_job_rejects_active_record(self) -> None:
        headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]

        delete_response = self.client.request("DELETE", f"/api/v1/jobs/{job_id}", headers=headers)
        self.assertEqual(delete_response.status_code, 409)
        self.assertEqual(delete_response.json()["error"]["code"], "conflict")

    def test_artifact_access_is_scoped_to_device(self) -> None:
        repo = DeviceRepository(self.container.database)
        owner = repo.create(name="Owner", platform=Platform.IOS, app_version="1.0", token_hash="owner")
        other = repo.create(name="Other", platform=Platform.IOS, app_version="1.0", token_hash="other")
        job = self.container.job_service.create(
            device=owner,
            source_url="https://x.com/demo/status/999",
            preferred_quality=None,
        )
        artifact_path = self.container.settings.artifacts_dir / "sample.mp4"
        artifact_path.write_bytes(b"video")
        artifact = self.container.artifact_service._artifacts.create(
            job_id=job.id,
            file_name="sample.mp4",
            mime_type="video/mp4",
            storage_path=str(artifact_path),
            file_size=artifact_path.stat().st_size,
        )
        with self.assertRaises(AuthorizationError):
            self.container.artifact_service.get_owned_artifact_path(artifact.id, other)

    def test_extension_is_sanitized(self) -> None:
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
        )
        self.assertEqual(worker._normalize_extension(".mp4"), "mp4")
        self.assertEqual(worker._normalize_extension("../../sh"), "mp4")
        self.assertEqual(worker._normalize_extension("mp4?x=1"), "mp4")

    def test_worker_safe_artifact_stem_sanitizes_title(self) -> None:
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
        )

        self.assertEqual(
            worker._safe_artifact_stem('  Demo:/\\*?"<>|\nTitle .  ', fallback='fallback'),
            "Demo Title",
        )

    def test_worker_safe_artifact_stem_falls_back_when_title_is_blank(self) -> None:
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
        )

        self.assertEqual(worker._safe_artifact_stem('  .:/\\*?"<>|  ', fallback='job-123'), "job-123")

    def test_worker_allocate_artifact_path_appends_suffix_for_collisions(self) -> None:
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
        )
        (self.container.settings.artifacts_dir / "demo.mp4").write_bytes(b"1")
        (self.container.settings.artifacts_dir / "demo (1).mp4").write_bytes(b"2")

        output_path = worker._allocate_artifact_path(stem="demo", ext="mp4")

        self.assertEqual(output_path.name, "demo (2).mp4")

    def test_cancelled_job_stays_cancelled(self) -> None:
        headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/777"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        cancel_response = self.client.post(f"/api/v1/jobs/{job_id}/cancel", headers=headers)
        self.assertEqual(cancel_response.status_code, 200)
        job_response = self.client.get(f"/api/v1/jobs/{job_id}", headers=headers)
        self.assertEqual(job_response.json()["data"]["status"], JobStatus.CANCELED.value)

    def test_register_accepts_legacy_bootstrap_code_field(self) -> None:
        response = self.client.post(
            "/api/v1/devices/register",
            json={
                "device_name": "Legacy",
                "platform": "ios",
                "app_version": "0.1.0",
                "bootstrap_code": "test-bootstrap",
            },
        )
        self.assertEqual(response.status_code, 200)

    def test_register_is_rate_limited(self) -> None:
        self.container.settings.register_rate_limit = 1
        first = self.client.post(
            "/api/v1/devices/register",
            json={
                "device_name": "First",
                "platform": "ios",
                "app_version": "0.1.0",
            },
        )
        second = self.client.post(
            "/api/v1/devices/register",
            json={
                "device_name": "Second",
                "platform": "ios",
                "app_version": "0.1.0",
            },
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)

    def test_settings_default_artifacts_dir_is_downloads(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "XDL_DATA_DIR": self.temp_dir.name,
                "XDL_DATABASE_PATH": str(Path(self.temp_dir.name) / "app.db"),
            },
            clear=True,
        ):
            settings = Settings.from_env()
        self.assertEqual(settings.artifacts_dir, Path.home() / "Downloads" / "XDownloader")

    def test_validate_download_url_blocks_non_global_addresses(self) -> None:
        blocked_addresses = [
            "127.0.0.1",
            "100.64.0.1",
            "169.254.1.1",
            "224.0.0.1",
            "::1",
            "fc00::1",
        ]
        for blocked_address in blocked_addresses:
            fake_infos = [(None, None, None, None, (blocked_address, 443))]
            with self.subTest(blocked_address=blocked_address):
                with patch("app.services.url_tools.socket.getaddrinfo", return_value=fake_infos):
                    with self.assertRaises(ProviderAppError):
                        validate_download_url("https://video.twimg.com/clip.mp4")

    def test_resolve_download_url_uses_webpage_url_as_base(self) -> None:
        fake_infos = [
            (None, None, None, None, ("8.8.8.8", 443)),
        ]
        with patch("app.services.url_tools.socket.getaddrinfo", return_value=fake_infos):
            resolved = resolve_download_url(
                "/media/clip.mp4;stream=1?tag=1",
                base_url="https://video.twimg.com/status/123",
            )
        self.assertEqual(resolved.url, "https://video.twimg.com/media/clip.mp4;stream=1?tag=1")
        self.assertEqual(resolved.host, "video.twimg.com")
        self.assertEqual(resolved.addresses, ("8.8.8.8",))
        self.assertEqual(resolved.path_and_query, "/media/clip.mp4;stream=1?tag=1")

    def test_resolve_download_url_upgrades_allowed_http_host_to_https(self) -> None:
        fake_infos = [
            (None, None, None, None, ("8.8.8.8", 443)),
        ]
        with patch("app.services.url_tools.socket.getaddrinfo", return_value=fake_infos):
            resolved = resolve_download_url(
                "http://sns-bak-v6.xhscdn.com/stream/1/demo.mp4?tag=1",
                source_url="https://www.xiaohongshu.com/explore/abcdef?xsec_token=1&xsec_source=app_share",
            )
        self.assertEqual(resolved.url, "https://sns-bak-v6.xhscdn.com/stream/1/demo.mp4?tag=1")
        self.assertEqual(resolved.host, "sns-bak-v6.xhscdn.com")
        self.assertEqual(resolved.addresses, ("8.8.8.8",))

    def test_resolve_download_url_rejects_http_for_non_xiaohongshu_source(self) -> None:
        with self.assertRaises(ProviderAppError) as context:
            resolve_download_url(
                "http://video.twimg.com/media/clip.mp4?tag=1",
                source_url="https://x.com/demo/status/123",
            )
        self.assertEqual(context.exception.message, "download URL must use HTTPS")

    def test_normalize_extraction_source_url_keeps_only_xiaohongshu_xsec_token(self) -> None:
        normalized = normalize_extraction_source_url(
            "https://www.xiaohongshu.com/explore/abcdef?xsec_token=1&xsec_source=app_share&type=video"
        )
        self.assertEqual(normalized, "https://www.xiaohongshu.com/explore/abcdef?xsec_token=1")

    def test_normalize_extraction_source_url_keeps_twitter_normalized_without_query(self) -> None:
        normalized = normalize_extraction_source_url("https://x.com/demo/status/123?s=20")
        self.assertEqual(normalized, "https://x.com/demo/status/123")

    def test_normalize_source_url_keeps_bilibili_page_parameter(self) -> None:
        normalized = normalize_source_url(
            "https://www.bilibili.com/video/BV1sRoHB5EHC/?p=2&spm_id_from=333.1007.tianma.1-2-2.click"
        )
        self.assertEqual(normalized, "https://www.bilibili.com/video/BV1sRoHB5EHC?p=2")

    def test_normalize_extraction_source_url_keeps_bilibili_page_parameter(self) -> None:
        normalized = normalize_extraction_source_url(
            "https://www.bilibili.com/video/BV1sRoHB5EHC/?p=2&spm_id_from=333.1007.tianma.1-2-2.click"
        )
        self.assertEqual(normalized, "https://www.bilibili.com/video/BV1sRoHB5EHC?p=2")

    def test_normalize_source_url_unifies_youtube_variants(self) -> None:
        self.assertEqual(
            normalize_source_url("https://www.youtube.com/watch?v=GEFehFHg_os&list=PL123"),
            "https://www.youtube.com/watch?v=GEFehFHg_os",
        )
        self.assertEqual(
            normalize_source_url("https://youtu.be/GEFehFHg_os?t=10"),
            "https://www.youtube.com/watch?v=GEFehFHg_os",
        )
        self.assertEqual(
            normalize_source_url("https://www.youtube.com/shorts/GEFehFHg_os?feature=share"),
            "https://www.youtube.com/watch?v=GEFehFHg_os",
        )

    def test_detect_source_platform_rejects_invalid_youtube_video_ids(self) -> None:
        invalid_urls = [
            "https://www.youtube.com/watch",
            "https://www.youtube.com/watch?list=PL123",
            "https://www.youtube.com/watch?v=",
            "https://m.youtube.com/watch?t=30",
            "https://www.youtube.com/watch?v=abc",
            "https://www.youtube.com/watch?v=abcdefghijkl",
            "https://www.youtube.com/watch?v=invalid$id",
            "https://www.youtube.com/shorts/abc",
            "https://youtu.be/invalid$id",
        ]

        for url in invalid_urls:
            with self.subTest(url=url):
                self.assertFalse(is_supported_source_url(url))

    def test_resolve_download_url_rejects_non_default_https_port(self) -> None:
        with self.assertRaises(ProviderAppError):
            resolve_download_url("https://video.twimg.com:8443/media/clip.mp4")

    def test_resolve_download_url_rejects_invalid_port(self) -> None:
        with self.assertRaises(ProviderAppError):
            resolve_download_url("https://video.twimg.com:99999/media/clip.mp4")

    def test_resolve_download_url_rejects_control_characters(self) -> None:
        with self.assertRaises(ProviderAppError):
            resolve_download_url("https://video.twimg.com/media/clip.mp4\r\nX-Test: bad")

    def test_media_downloader_uses_allowed_address_directly(self) -> None:
        calls: list[tuple[str, int]] = []
        expected_body = b"video-bytes"

        class FakeSocket:
            def __init__(self, chunks: list[bytes]) -> None:
                self._chunks = chunks
                self.request = b""

            def sendall(self, data: bytes) -> None:
                self.request = data

            def recv(self, _: int) -> bytes:
                return self._chunks.pop(0)

            def settimeout(self, timeout: int) -> None:
                self.timeout = timeout

            def __enter__(self) -> "FakeSocket":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

        fake_socket = FakeSocket([
            b"HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\nvideo-bytes",
            b"",
        ])

        def fake_create_connection(address: tuple[str, int], timeout: int) -> FakeSocket:
            calls.append(address)
            self.assertEqual(timeout, 5)
            return fake_socket

        def fake_wrap_socket(sock: FakeSocket, server_hostname: str) -> FakeSocket:
            self.assertIs(sock, fake_socket)
            self.assertEqual(server_hostname, "video.twimg.com")
            return sock

        output_path = self.container.settings.artifacts_dir / "download.part"
        downloader = MediaDownloader()
        with patch("app.services.media_downloader.socket.create_connection", side_effect=fake_create_connection):
            with patch.object(ssl.SSLContext, "wrap_socket", side_effect=fake_wrap_socket):
                downloader.download(
                    url="https://video.twimg.com/media/clip.mp4;stream=1?tag=1",
                    allowed_addresses=("8.8.8.8",),
                    output_path=output_path,
                    timeout_seconds=5,
                )

        self.assertEqual(calls, [("8.8.8.8", 443)])
        self.assertEqual(output_path.read_bytes(), expected_body)
        self.assertIn(b"Host: video.twimg.com", fake_socket.request)
        self.assertIn(b"GET /media/clip.mp4;stream=1?tag=1 HTTP/1.1", fake_socket.request)

    def test_media_downloader_rejects_large_content_length(self) -> None:
        class FakeSocket:
            def __init__(self) -> None:
                self._chunks = [
                    b"HTTP/1.1 200 OK\r\nContent-Length: 12\r\n\r\nhello world!",
                    b"",
                ]

            def sendall(self, data: bytes) -> None:
                self.request = data

            def recv(self, _: int) -> bytes:
                return self._chunks.pop(0)

            def settimeout(self, timeout: int) -> None:
                self.timeout = timeout

            def __enter__(self) -> "FakeSocket":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

        output_path = self.container.settings.artifacts_dir / "too-large.part"
        downloader = MediaDownloader(max_bytes=5)
        with patch("app.services.media_downloader.socket.create_connection", return_value=FakeSocket()):
            with patch.object(ssl.SSLContext, "wrap_socket", side_effect=lambda sock, server_hostname: sock):
                with self.assertRaises(DownloadAppError):
                    downloader.download(
                        url="https://video.twimg.com/media/clip.mp4",
                        allowed_addresses=("8.8.8.8",),
                        output_path=output_path,
                        timeout_seconds=5,
                    )
        self.assertFalse(output_path.exists())

    def test_media_downloader_rejects_non_ip_allowed_address(self) -> None:
        downloader = MediaDownloader()
        with self.assertRaises(DownloadAppError):
            downloader.download(
                url="https://video.twimg.com/media/clip.mp4",
                allowed_addresses=("video.twimg.com",),
                output_path=self.container.settings.artifacts_dir / "bad.part",
                timeout_seconds=5,
            )

    def test_media_downloader_rejects_existing_temp_path(self) -> None:
        output_path = self.container.settings.artifacts_dir / "existing.part"
        output_path.write_bytes(b"existing")
        downloader = MediaDownloader()
        with self.assertRaises(DownloadAppError):
            downloader.download(
                url="https://video.twimg.com/media/clip.mp4",
                allowed_addresses=("8.8.8.8",),
                output_path=output_path,
                timeout_seconds=5,
            )

    def test_media_downloader_rejects_oversized_headers(self) -> None:
        class FakeSocket:
            def __init__(self) -> None:
                self._chunks = [b"HTTP/1.1 200 OK\r\nX-Test: " + (b"a" * (20 * 1024))]

            def sendall(self, data: bytes) -> None:
                self.request = data

            def recv(self, _: int) -> bytes:
                return self._chunks.pop(0) if self._chunks else b""

            def settimeout(self, timeout: int) -> None:
                self.timeout = timeout

            def __enter__(self) -> "FakeSocket":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

        downloader = MediaDownloader()
        with patch("app.services.media_downloader.socket.create_connection", return_value=FakeSocket()):
            with patch.object(ssl.SSLContext, "wrap_socket", side_effect=lambda sock, server_hostname: sock):
                with self.assertRaises(DownloadAppError):
                    downloader.download(
                        url="https://video.twimg.com/media/clip.mp4",
                        allowed_addresses=("8.8.8.8",),
                        output_path=self.container.settings.artifacts_dir / "headers.part",
                        timeout_seconds=5,
                    )

    def test_media_downloader_rejects_chunk_larger_than_limit(self) -> None:
        class FakeSocket:
            def __init__(self) -> None:
                self._chunks = [
                    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nA\r\n1234567890\r\n0\r\n\r\n",
                    b"",
                ]

            def sendall(self, data: bytes) -> None:
                self.request = data

            def recv(self, _: int) -> bytes:
                return self._chunks.pop(0)

            def settimeout(self, timeout: int) -> None:
                self.timeout = timeout

            def __enter__(self) -> "FakeSocket":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

        output_path = self.container.settings.artifacts_dir / "chunk-too-large.part"
        downloader = MediaDownloader(max_bytes=5)
        with patch("app.services.media_downloader.socket.create_connection", return_value=FakeSocket()):
            with patch.object(ssl.SSLContext, "wrap_socket", side_effect=lambda sock, server_hostname: sock):
                with self.assertRaises(DownloadAppError):
                    downloader.download(
                        url="https://video.twimg.com/media/clip.mp4",
                        allowed_addresses=("8.8.8.8",),
                        output_path=output_path,
                        timeout_seconds=5,
                    )
        self.assertFalse(output_path.exists())

    def test_media_downloader_reports_progress(self) -> None:
        progress_events: list[tuple[int, int | None, int | None, int | None]] = []

        class FakeSocket:
            def __init__(self) -> None:
                self._chunks = [
                    b"HTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\nhello ",
                    b"world",
                    b"",
                ]

            def sendall(self, data: bytes) -> None:
                self.request = data

            def recv(self, _: int) -> bytes:
                return self._chunks.pop(0)

            def settimeout(self, timeout: int) -> None:
                self.timeout = timeout

            def __enter__(self) -> "FakeSocket":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

        output_path = self.container.settings.artifacts_dir / "progress.part"
        downloader = MediaDownloader()
        with patch("app.services.media_downloader.socket.create_connection", return_value=FakeSocket()):
            with patch.object(ssl.SSLContext, "wrap_socket", side_effect=lambda sock, server_hostname: sock):
                downloader.download(
                    url="https://video.twimg.com/media/clip.mp4",
                    allowed_addresses=("8.8.8.8",),
                    output_path=output_path,
                    timeout_seconds=5,
                    progress_callback=lambda downloaded_bytes, total_bytes, speed_bytes_per_sec, eta_seconds: progress_events.append(
                        (downloaded_bytes, total_bytes, speed_bytes_per_sec, eta_seconds)
                    ),
                )

        self.assertGreaterEqual(len(progress_events), 2)
        self.assertEqual(progress_events[-1][0], 11)
        self.assertEqual(progress_events[-1][1], 11)
        self.assertIsNotNone(progress_events[-1][2])

    def test_worker_uses_extraction_normalized_source_url(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-device",
        )
        source_url = (
            "https://www.xiaohongshu.com/explore/69adbfa5000000002602e9b0"
            "?xsec_token=test-token&xsec_source=app_share"
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url=source_url,
            normalized_url="https://www.xiaohongshu.com/explore/69adbfa5000000002602e9b0",
            selected_quality=None,
        )
        selector = self.container.job_service._runner.worker._selector
        downloader = self.container.job_service._runner.worker._downloader
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=selector,
            downloader=downloader,
        )
        extracted = ExtractedMedia(
            provider="yt-dlp",
            title="title",
            author_handle="author",
            thumbnail_url=None,
            direct_url="https://video.twimg.com/media/clip.mp4",
            direct_url_addresses=("8.8.8.8",),
            webpage_url=source_url,
            file_extension="mp4",
        )

        with patch.object(worker._selector, "extract", return_value=extracted) as extract_mock:
            with patch.object(worker._downloader, "download", side_effect=DownloadAppError("stop download")):
                worker.run(job.id)

        extract_mock.assert_called_once_with(
            "https://www.xiaohongshu.com/explore/69adbfa5000000002602e9b0?xsec_token=test-token"
        )
        self.assertFalse((self.container.settings.artifacts_dir / "title.mp4.part").exists())

    def test_worker_direct_download_updates_progress_fields(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-direct-progress",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://x.com/demo/status/123",
            normalized_url="https://x.com/demo/status/123",
            selected_quality=None,
        )
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )
        extracted = ExtractedMedia(
            provider="yt-dlp",
            title="title",
            author_handle="author",
            thumbnail_url=None,
            direct_url="https://video.twimg.com/media/clip.mp4",
            direct_url_addresses=("8.8.8.8",),
            webpage_url="https://x.com/demo/status/123",
            file_extension="mp4",
        )

        def fake_download(*, output_path: Path, progress_callback, **kwargs) -> None:
            progress_callback(5, 10, 2, 2)
            progress_callback(10, 10, 4, 0)
            output_path.write_bytes(b"0123456789")

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch.object(worker._downloader, "download", side_effect=fake_download):
                worker.run(job.id)

        updated = self.container.job_service._repository.get(job.id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, JobStatus.COMPLETED)
        self.assertEqual(updated.downloaded_bytes, 10)
        self.assertEqual(updated.total_bytes, 10)
        self.assertIsNone(updated.speed_bytes_per_sec)
        self.assertIsNone(updated.eta_seconds)
        self.assertIsNotNone(updated.artifact_id)
        artifact = self.container.artifact_service._artifacts.get(updated.artifact_id)
        self.assertIsNotNone(artifact)
        self.assertEqual(artifact.file_name, "title.mp4")
        self.assertEqual(Path(artifact.storage_path).name, "title.mp4")

    def test_worker_delegates_bilibili_split_stream_download_success(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-device",
        )
        source_url = "https://www.bilibili.com/video/BV1sRoHB5EHC/?spm_id_from=333.1007.tianma.1-2-2.click"
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url=source_url,
            normalized_url="https://www.bilibili.com/video/BV1sRoHB5EHC",
            selected_quality=None,
        )
        selector = self.container.job_service._runner.worker._selector
        downloader = self.container.job_service._runner.worker._downloader
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=selector,
            downloader=downloader,
        )
        extracted = ExtractedMedia(
            provider="yt-dlp",
            title="title",
            author_handle="author",
            thumbnail_url=None,
            direct_url=None,
            webpage_url="https://www.bilibili.com/video/BV1sRoHB5EHC/",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )

        artifacts_dir = self.container.settings.artifacts_dir

        class FakeStderr:
            def __iter__(self):
                return iter(())

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                self.stderr = FakeStderr()

            def wait(self, timeout: int) -> int:
                output_path = artifacts_dir / f"{job.id}.mp4"
                output_path.write_bytes(b"video")
                return 0

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch.object(worker._downloader, "download") as download_mock:
                with patch("app.workers.download_job_worker.subprocess.Popen", return_value=FakeProcess()):
                    worker.run(job.id)

        download_mock.assert_not_called()
        updated = self.container.job_service._repository.get(job.id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, JobStatus.COMPLETED)
        self.assertEqual(updated.provider, "yt-dlp")
        self.assertIsNotNone(updated.artifact_id)
        artifact = self.container.artifact_service._artifacts.get(updated.artifact_id)
        self.assertIsNotNone(artifact)
        self.assertEqual(Path(artifact.storage_path).read_bytes(), b"video")

    def test_worker_marks_failed_when_delegate_ytdlp_download_fails(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-device",
        )
        source_url = "https://www.bilibili.com/video/BV1sRoHB5EHC/?spm_id_from=333.1007.tianma.1-2-2.click"
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url=source_url,
            normalized_url="https://www.bilibili.com/video/BV1sRoHB5EHC",
            selected_quality=None,
        )
        selector = self.container.job_service._runner.worker._selector
        downloader = self.container.job_service._runner.worker._downloader
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=selector,
            downloader=downloader,
        )
        extracted = ExtractedMedia(
            provider="yt-dlp",
            title="title",
            author_handle="author",
            thumbnail_url=None,
            direct_url=None,
            webpage_url="https://www.bilibili.com/video/BV1sRoHB5EHC/",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )

        artifacts_dir = self.container.settings.artifacts_dir

        class FakeStderr:
            def __iter__(self):
                yield "ERROR: merge failed\n"

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                self.stderr = FakeStderr()

            def wait(self, timeout: int) -> int:
                (artifacts_dir / f"{job.id}.part").write_bytes(b"partial")
                (artifacts_dir / f"{job.id}.f137.mp4").write_bytes(b"partial-video")
                return 1

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch.object(worker._downloader, "download") as download_mock:
                with patch("app.workers.download_job_worker.subprocess.Popen", return_value=FakeProcess()):
                    worker.run(job.id)

        download_mock.assert_not_called()
        updated = self.container.job_service._repository.get(job.id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, JobStatus.FAILED)
        self.assertEqual(updated.error_code, "download_error")
        self.assertEqual(updated.error_message, "merge failed")
        self.assertFalse((self.container.settings.artifacts_dir / f"{job.id}.part").exists())
        self.assertFalse((self.container.settings.artifacts_dir / f"{job.id}.f137.mp4").exists())

    def test_worker_prefers_error_line_from_combined_delegate_output(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-combined-output-error",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            normalized_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            selected_quality=None,
        )
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )
        extracted = ExtractedMedia(
            provider="yt-dlp",
            title="title",
            author_handle="author",
            thumbnail_url=None,
            direct_url=None,
            webpage_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )

        class FakeStdout:
            def __iter__(self):
                yield "[youtube] GEFehFHg_os: Downloading webpage\n"
                yield "WARNING: temporary warning\n"
                yield "ERROR: requested format not available\n"

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                self.stdout = FakeStdout()
                self.stderr = None

            def wait(self, timeout: int) -> int:
                return 1

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch("app.workers.download_job_worker.subprocess.Popen", return_value=FakeProcess()):
                worker.run(job.id)

        updated = self.container.job_service._repository.get(job.id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, JobStatus.FAILED)
        self.assertEqual(updated.error_code, "download_error")
        self.assertEqual(updated.error_message, "requested format not available")

    def test_worker_delegate_download_uses_ignore_config_and_size_limit(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-device",
        )
        source_url = "https://www.bilibili.com/video/BV1sRoHB5EHC/?p=2&spm_id_from=333.1007.tianma.1-2-2.click"
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url=source_url,
            normalized_url="https://www.bilibili.com/video/BV1sRoHB5EHC?p=2",
            selected_quality=None,
        )
        selector = self.container.job_service._runner.worker._selector
        downloader = self.container.job_service._runner.worker._downloader
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=selector,
            downloader=downloader,
        )
        extracted = ExtractedMedia(
            provider="yt-dlp",
            title="title",
            author_handle="author",
            thumbnail_url=None,
            direct_url=None,
            webpage_url="https://www.bilibili.com/video/BV1sRoHB5EHC/?p=2",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )

        artifacts_dir = self.container.settings.artifacts_dir

        class FakeStderr:
            def __iter__(self):
                return iter(())

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                self.stderr = FakeStderr()

            def wait(self, timeout: int) -> int:
                output_path = artifacts_dir / f"{job.id}.mp4"
                output_path.write_bytes(b"video")
                return 0

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch("app.workers.download_job_worker.subprocess.Popen", return_value=FakeProcess()) as popen_mock:
                worker.run(job.id)

        command = popen_mock.call_args.args[0]
        self.assertIn("--ignore-config", command)
        self.assertIn("--max-filesize", command)
        self.assertIn("--newline", command)
        self.assertIn(str(self.container.settings.download_max_bytes), command)
        self.assertEqual(command[-1], "https://www.bilibili.com/video/BV1sRoHB5EHC?p=2")

    def test_worker_delegate_download_adds_youtube_runtime_flags(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-youtube-runtime",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            normalized_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            selected_quality=None,
        )
        self.container.settings.yt_dlp_binary = 'yt-dlp'
        self.container.settings.youtube_cookies_from_browser = 'chrome'
        self.container.settings.youtube_js_runtime = 'node'
        self.container.settings.youtube_remote_components = 'ejs:github'
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )
        extracted = ExtractedMedia(
            provider="yt-dlp",
            title="title",
            author_handle="author",
            thumbnail_url=None,
            direct_url=None,
            webpage_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )
        artifacts_dir = self.container.settings.artifacts_dir

        class FakeStderr:
            def __iter__(self):
                return iter(())

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                self.stderr = FakeStderr()

            def wait(self, timeout: int) -> int:
                output_path = artifacts_dir / f"{job.id}.mp4"
                output_path.write_bytes(b"video")
                return 0

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch("app.workers.download_job_worker.subprocess.Popen", return_value=FakeProcess()) as popen_mock:
                worker.run(job.id)

        command = popen_mock.call_args.args[0]
        self.assertEqual(command[0], "yt-dlp")
        self.assertIn("--cookies-from-browser", command)
        self.assertIn("chrome", command)
        self.assertIn("--js-runtimes", command)
        self.assertIn("node", command)
        self.assertIn("--remote-components", command)
        self.assertIn("ejs:github", command)
        self.assertIn("-f", command)
        self.assertEqual(
            command[command.index("-f") + 1],
            "bestvideo*[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo*+bestaudio/best",
        )
        self.assertEqual(command[command.index("--merge-output-format") + 1], "mp4")
        updated = self.container.job_service._repository.get(job.id)
        self.assertIsNotNone(updated)
        self.assertIsNotNone(updated.artifact_id)
        artifact = self.container.artifact_service._artifacts.get(updated.artifact_id)
        self.assertIsNotNone(artifact)
        self.assertEqual(artifact.file_name, "title.mp4")

    def test_worker_delegate_download_keeps_readable_title_when_falling_back_to_webm(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-youtube-webm",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            normalized_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            selected_quality=None,
        )
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )
        extracted = ExtractedMedia(
            provider="yt-dlp",
            title="Readable Title",
            author_handle="author",
            thumbnail_url=None,
            direct_url=None,
            webpage_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )
        artifacts_dir = self.container.settings.artifacts_dir

        class FakeStdout:
            def __iter__(self):
                return iter(())

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                self.stdout = FakeStdout()
                self.stderr = None

            def wait(self, timeout: int) -> int:
                output_path = artifacts_dir / f"{job.id}.webm"
                output_path.write_bytes(b"video")
                return 0

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch("app.workers.download_job_worker.subprocess.Popen", return_value=FakeProcess()):
                worker.run(job.id)

        updated = self.container.job_service._repository.get(job.id)
        self.assertIsNotNone(updated)
        self.assertIsNotNone(updated.artifact_id)
        artifact = self.container.artifact_service._artifacts.get(updated.artifact_id)
        self.assertIsNotNone(artifact)
        self.assertEqual(artifact.file_name, "Readable Title.webm")
        self.assertEqual(Path(artifact.storage_path).name, "Readable Title.webm")

    def test_worker_delegate_download_does_not_add_youtube_runtime_flags_for_bilibili(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-bilibili-runtime",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://www.bilibili.com/video/BV1sRoHB5EHC/",
            normalized_url="https://www.bilibili.com/video/BV1sRoHB5EHC",
            selected_quality=None,
        )
        self.container.settings.yt_dlp_binary = 'yt-dlp'
        self.container.settings.youtube_cookies_from_browser = 'chrome'
        self.container.settings.youtube_js_runtime = 'node'
        self.container.settings.youtube_remote_components = 'ejs:github'
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )
        extracted = ExtractedMedia(
            provider="yt-dlp",
            title="title",
            author_handle="author",
            thumbnail_url=None,
            direct_url=None,
            webpage_url="https://www.bilibili.com/video/BV1sRoHB5EHC/",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )
        artifacts_dir = self.container.settings.artifacts_dir

        class FakeStderr:
            def __iter__(self):
                return iter(())

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                self.stderr = FakeStderr()

            def wait(self, timeout: int) -> int:
                output_path = artifacts_dir / f"{job.id}.mp4"
                output_path.write_bytes(b"video")
                return 0

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch("app.workers.download_job_worker.subprocess.Popen", return_value=FakeProcess()) as popen_mock:
                worker.run(job.id)

        command = popen_mock.call_args.args[0]
        self.assertNotIn("--cookies-from-browser", command)
        self.assertNotIn("--js-runtimes", command)
        self.assertNotIn("--remote-components", command)

    def test_worker_updates_progress_during_delegate_download(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-delegate-progress",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            normalized_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            selected_quality=None,
        )
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )
        extracted = ExtractedMedia(
            provider="yt-dlp",
            title="title",
            author_handle="author",
            thumbnail_url=None,
            direct_url=None,
            webpage_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )
        artifacts_dir = self.container.settings.artifacts_dir

        class FakeStderr:
            def __iter__(self):
                yield "[download]   25.0% of 8.00MiB at 2.00MiB/s ETA 00:03\n"
                current = self_repo.get(job.id)
                self_case.assertIsNotNone(current)
                self_case.assertEqual(current.status, JobStatus.DOWNLOADING)
                self_case.assertEqual(current.downloaded_bytes, 2097152)
                self_case.assertEqual(current.total_bytes, 8388608)
                self_case.assertEqual(current.speed_bytes_per_sec, 2097152)
                self_case.assertEqual(current.eta_seconds, 3)
                yield "[download]   50.0% of 8.00MiB at 4.00MiB/s ETA 00:01\n"

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                self.stderr = FakeStderr()

            def wait(self, timeout: int) -> int:
                output_path = artifacts_dir / f"{job.id}.mp4"
                output_path.write_bytes(b"video")
                return 0

        self_case = self
        self_repo = self.container.job_service._repository
        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch("app.workers.download_job_worker.subprocess.Popen", return_value=FakeProcess()):
                worker.run(job.id)

        updated = self.container.job_service._repository.get(job.id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, JobStatus.COMPLETED)
        self.assertEqual(updated.downloaded_bytes, 5)
        self.assertEqual(updated.total_bytes, 5)

    def test_worker_updates_progress_during_delegate_download_from_stdout(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-delegate-stdout-progress",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            normalized_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            selected_quality=None,
        )
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )
        extracted = ExtractedMedia(
            provider="yt-dlp",
            title="title",
            author_handle="author",
            thumbnail_url=None,
            direct_url=None,
            webpage_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )
        artifacts_dir = self.container.settings.artifacts_dir
        progress_seen = {"value": False}

        class FakeStdout:
            def __iter__(self):
                progress_seen["value"] = True
                yield "[download]   25.0% of 8.00MiB at 2.00MiB/s ETA 00:03\n"
                current = self_repo.get(job.id)
                self_case.assertIsNotNone(current)
                self_case.assertEqual(current.status, JobStatus.DOWNLOADING)
                self_case.assertEqual(current.downloaded_bytes, 2097152)
                self_case.assertEqual(current.total_bytes, 8388608)
                self_case.assertEqual(current.speed_bytes_per_sec, 2097152)
                self_case.assertEqual(current.eta_seconds, 3)
                yield "[download]   50.0% of 8.00MiB at 4.00MiB/s ETA 00:01\n"

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                self.stdout = FakeStdout()
                self.stderr = None

            def wait(self, timeout: int) -> int:
                output_path = artifacts_dir / f"{job.id}.mp4"
                output_path.write_bytes(b"video")
                return 0

        self_case = self
        self_repo = self.container.job_service._repository
        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch("app.workers.download_job_worker.subprocess.Popen", return_value=FakeProcess()):
                worker.run(job.id)

        self.assertTrue(progress_seen["value"])
        updated = self.container.job_service._repository.get(job.id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, JobStatus.COMPLETED)
        self.assertEqual(updated.downloaded_bytes, 5)
        self.assertEqual(updated.total_bytes, 5)

    def test_worker_delegate_timeout_terminates_process_group_and_cleans_outputs(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-delegate-timeout",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            normalized_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            selected_quality=None,
        )
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )
        extracted = ExtractedMedia(
            provider="yt-dlp",
            title="title",
            author_handle="author",
            thumbnail_url=None,
            direct_url=None,
            webpage_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )
        artifacts_dir = self.container.settings.artifacts_dir

        class FakeStderr:
            def __iter__(self):
                return iter(())

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                self.pid = 4321
                self.stderr = FakeStderr()
                self._timed_out = False

            def wait(self, timeout: int | None = None) -> int:
                if not self._timed_out:
                    self._timed_out = True
                    (artifacts_dir / f"{job.id}.part").write_bytes(b"partial")
                    raise subprocess.TimeoutExpired(cmd="yt-dlp", timeout=timeout or 0)
                return 0

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch("app.workers.download_job_worker.subprocess.Popen", return_value=FakeProcess()):
                with patch("app.workers.download_job_worker.os.killpg") as killpg_mock:
                    worker.run(job.id)

        killpg_mock.assert_called_once_with(4321, signal.SIGKILL)
        updated = self.container.job_service._repository.get(job.id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, JobStatus.FAILED)
        self.assertEqual(updated.error_code, "download_error")
        self.assertEqual(updated.error_message, "yt-dlp download timed out")
        self.assertFalse((artifacts_dir / f"{job.id}.part").exists())

    def test_worker_parses_ytdlp_percent_progress_line(self) -> None:
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )

        parsed = worker._parse_ytdlp_progress("[download]   50.0% of 10.00MiB at 2.00MiB/s ETA 00:02")

        self.assertEqual(parsed, (5242880, 10485760, 2097152, 2, 67))

    def test_worker_parses_ytdlp_downloaded_only_progress_line(self) -> None:
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )

        parsed = worker._parse_ytdlp_progress("[download] 1.50MiB at 500.00KiB/s ETA 00:03")

        self.assertEqual(parsed, (1572864, None, 512000, 3, 45))

    def test_worker_ignores_non_progress_ytdlp_lines(self) -> None:
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )

        self.assertIsNone(worker._parse_ytdlp_progress("[download] Destination: file.mp4"))
        self.assertIsNone(worker._parse_ytdlp_progress("[Merger] Merging formats"))

    def test_worker_marks_failed_when_delegate_output_exceeds_size_limit(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-device",
        )
        source_url = "https://www.bilibili.com/video/BV1sRoHB5EHC/"
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url=source_url,
            normalized_url="https://www.bilibili.com/video/BV1sRoHB5EHC",
            selected_quality=None,
        )
        selector = self.container.job_service._runner.worker._selector
        downloader = self.container.job_service._runner.worker._downloader
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=selector,
            downloader=downloader,
        )
        extracted = ExtractedMedia(
            provider="yt-dlp",
            title="title",
            author_handle="author",
            thumbnail_url=None,
            direct_url=None,
            webpage_url="https://www.bilibili.com/video/BV1sRoHB5EHC/",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )
        self.container.settings.download_max_bytes = 4

        artifacts_dir = self.container.settings.artifacts_dir

        class FakeStderr:
            def __iter__(self):
                return iter(())

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                self.stderr = FakeStderr()

            def wait(self, timeout: int) -> int:
                (artifacts_dir / f"{job.id}.mp4").write_bytes(b"video")
                return 0

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch("app.workers.download_job_worker.subprocess.Popen", return_value=FakeProcess()):
                worker.run(job.id)

        updated = self.container.job_service._repository.get(job.id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, JobStatus.FAILED)
        self.assertEqual(updated.error_code, "download_error")
        self.assertEqual(updated.error_message, "delegated download exceeds size limit")
        self.assertFalse((self.container.settings.artifacts_dir / f"{job.id}.mp4").exists())

    def test_worker_delegate_download_uses_ffmpeg_directory_when_binary_path_is_file(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-device",
        )
        source_url = "https://www.bilibili.com/video/BV1sRoHB5EHC/"
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url=source_url,
            normalized_url="https://www.bilibili.com/video/BV1sRoHB5EHC",
            selected_quality=None,
        )
        selector = self.container.job_service._runner.worker._selector
        downloader = self.container.job_service._runner.worker._downloader
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=selector,
            downloader=downloader,
        )
        extracted = ExtractedMedia(
            provider="yt-dlp",
            title="title",
            author_handle="author",
            thumbnail_url=None,
            direct_url=None,
            webpage_url="https://www.bilibili.com/video/BV1sRoHB5EHC/",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )
        self.container.settings.ffmpeg_binary = "/opt/homebrew/bin/ffmpeg"

        artifacts_dir = self.container.settings.artifacts_dir

        class FakeStderr:
            def __iter__(self):
                return iter(())

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                self.stderr = FakeStderr()

            def wait(self, timeout: int) -> int:
                output_path = artifacts_dir / f"{job.id}.mp4"
                output_path.write_bytes(b"video")
                return 0

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch("app.workers.download_job_worker.subprocess.Popen", return_value=FakeProcess()) as popen_mock:
                worker.run(job.id)

        command = popen_mock.call_args.args[0]
        ffmpeg_location = command[command.index("--ffmpeg-location") + 1]
        self.assertEqual(ffmpeg_location, "/opt/homebrew/bin")

    def test_worker_delegate_download_resolves_ffmpeg_command_name_to_directory(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-device",
        )
        source_url = "https://www.bilibili.com/video/BV1sRoHB5EHC/"
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url=source_url,
            normalized_url="https://www.bilibili.com/video/BV1sRoHB5EHC",
            selected_quality=None,
        )
        selector = self.container.job_service._runner.worker._selector
        downloader = self.container.job_service._runner.worker._downloader
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=selector,
            downloader=downloader,
        )
        extracted = ExtractedMedia(
            provider="yt-dlp",
            title="title",
            author_handle="author",
            thumbnail_url=None,
            direct_url=None,
            webpage_url="https://www.bilibili.com/video/BV1sRoHB5EHC/",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )
        self.container.settings.ffmpeg_binary = "ffmpeg"

        artifacts_dir = self.container.settings.artifacts_dir

        class FakeStderr:
            def __iter__(self):
                return iter(())

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                self.stderr = FakeStderr()

            def wait(self, timeout: int) -> int:
                output_path = artifacts_dir / f"{job.id}.mp4"
                output_path.write_bytes(b"video")
                return 0

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch("app.workers.download_job_worker.shutil.which", return_value="/opt/homebrew/bin/ffmpeg"):
                with patch("app.workers.download_job_worker.subprocess.Popen", return_value=FakeProcess()) as popen_mock:
                    worker.run(job.id)

        command = popen_mock.call_args.args[0]
        ffmpeg_location = command[command.index("--ffmpeg-location") + 1]
        self.assertEqual(ffmpeg_location, "/opt/homebrew/bin")

    def test_worker_logs_unexpected_exception(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-device",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://x.com/demo/status/999",
            normalized_url="https://x.com/demo/status/999",
            selected_quality=None,
        )
        selector = self.container.job_service._runner.worker._selector
        downloader = self.container.job_service._runner.worker._downloader
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=selector,
            downloader=downloader,
        )

        extracted = ExtractedMedia(
            provider="test",
            title="title",
            author_handle="author",
            thumbnail_url=None,
            direct_url="https://video.twimg.com/media/clip.mp4",
            direct_url_addresses=("8.8.8.8",),
            webpage_url="https://x.com/demo/status/999",
            file_extension="mp4",
        )

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch.object(worker._downloader, "download", side_effect=RuntimeError("boom")):
                with self.assertLogs("app.workers.download_job_worker", level="ERROR") as logs:
                    worker.run(job.id)

        updated = self.container.job_service._repository.get(job.id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, JobStatus.FAILED)
        self.assertEqual(updated.error_code, "internal_error")
        self.assertEqual(updated.error_message, "unexpected worker exception")
        self.assertTrue(any(job.id in message for message in logs.output))


if __name__ == "__main__":
    unittest.main()
