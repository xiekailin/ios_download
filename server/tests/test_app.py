from __future__ import annotations

from pathlib import Path
import os
import signal
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.api.deps import build_container
from app.core.config import Settings
from app.core.errors import AuthorizationError, DownloadAppError, ProviderAppError
from app.domain.models import ArtifactRole, DeliveryMode, Device, ExtractedMedia, JobStatus, JobType, Platform
from app.main import app
from app.services.jobs import BackgroundJobRunner
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

        os.environ["XDL_ENV"] = "development"
        os.environ["XDL_DATA_DIR"] = str(base / "data")
        os.environ["XDL_DATABASE_PATH"] = str(base / "data" / "app.db")
        os.environ["XDL_ARTIFACTS_DIR"] = str(base / "data" / "artifacts")
        os.environ["XDL_BOOTSTRAP_CODE"] = "test-bootstrap"
        os.environ["XDL_LOCAL_SECRET"] = "test-local-secret"
        os.environ["XDL_WORKER_ENABLED"] = "false"
        os.environ["XDL_REGISTER_RATE_LIMIT"] = "5"
        os.environ["XDL_REGISTER_WINDOW_SECONDS"] = "300"

        self.container = build_container()
        app.state.container = self.container
        self.client = TestClient(app)
        self.local_headers = {"X-XDownloader-Local-Secret": "test-local-secret"}

    def tearDown(self) -> None:
        os.environ.pop("XDL_AUDIO_SEPARATION_COMMAND", None)
        os.environ.pop("XDL_PERFORMANCE_MODE", None)
        os.environ.pop("XDL_DOWNLOAD_WORKER_MAX_JOBS", None)
        os.environ.pop("XDL_AUDIO_SEPARATION_WORKER_MAX_JOBS", None)
        os.environ.pop("XDL_YTDLP_CONCURRENT_FRAGMENTS", None)
        os.environ.pop("XDL_YTDLP_FORMAT_STRATEGY", None)
        os.environ.pop("XDL_FFMPEG_THREADS", None)
        os.environ.pop("XDL_DOWNLOAD_RATE_LIMIT", None)
        os.environ.pop("XDL_YTDLP_EXTERNAL_DOWNLOADER", None)
        os.environ.pop("XDL_YTDLP_EXTERNAL_DOWNLOADER_ARGS", None)
        self.container.close()
        self.temp_dir.cleanup()

    def register_device(self, platform: str = "ios") -> str:
        response = self.client.post(
            "/api/v1/devices/register",
            headers=self.local_headers,
            json={
                "device_name": "Test iPhone",
                "platform": platform,
                "app_version": "0.1.0",
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["data"]["access_token"]

    def auth_headers(self, platform: str = "ios") -> dict[str, str]:
        return {**self.local_headers, "Authorization": f"Bearer {self.register_device(platform)}"}

    def mac_auth_headers(self) -> dict[str, str]:
        return self.auth_headers(platform="macos")

    def test_health_returns_local_proof_for_nonce(self) -> None:
        response = self.client.get("/api/v1/health?nonce=abc")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["data"]["local_proof"],
            "727e536b61b913c00f128e5a00319a68ec7a4ffdad3218f9b9006f871583b05e",
        )

    def test_health_returns_youtube_cookie_runtime_state(self) -> None:
        self.container.settings.youtube_cookies_from_browser = "chrome"
        self.container.settings.youtube_cookies_disabled = True

        response = self.client.get("/api/v1/health?nonce=abc")

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["youtube_cookies_from_browser"], "chrome")
        self.assertTrue(data["youtube_cookies_disabled"])

    def test_default_download_size_limit_allows_large_videos(self) -> None:
        self.assertGreaterEqual(Settings().download_max_bytes, 3 * 1024 * 1024 * 1024)

    def test_performance_mode_sets_download_and_heavy_task_limits(self) -> None:
        os.environ["XDL_PERFORMANCE_MODE"] = "performance"
        os.environ.pop("XDL_WORKER_MAX_JOBS", None)
        os.environ.pop("XDL_DOWNLOAD_WORKER_MAX_JOBS", None)
        os.environ.pop("XDL_AUDIO_SEPARATION_WORKER_MAX_JOBS", None)

        settings = Settings.from_env()

        self.assertEqual(settings.performance_mode, "performance")
        self.assertEqual(settings.download_worker_max_jobs, 4)
        self.assertEqual(settings.audio_separation_worker_max_jobs, 1)

    def test_low_power_mode_keeps_downloads_serial(self) -> None:
        os.environ["XDL_PERFORMANCE_MODE"] = "low_power"
        os.environ.pop("XDL_WORKER_MAX_JOBS", None)
        os.environ.pop("XDL_DOWNLOAD_WORKER_MAX_JOBS", None)
        os.environ.pop("XDL_AUDIO_SEPARATION_WORKER_MAX_JOBS", None)

        settings = Settings.from_env()

        self.assertEqual(settings.download_worker_max_jobs, 1)
        self.assertEqual(settings.audio_separation_worker_max_jobs, 1)

    def test_explicit_worker_limits_override_performance_mode_defaults(self) -> None:
        os.environ["XDL_PERFORMANCE_MODE"] = "low_power"
        os.environ["XDL_DOWNLOAD_WORKER_MAX_JOBS"] = "3"
        os.environ["XDL_AUDIO_SEPARATION_WORKER_MAX_JOBS"] = "2"

        settings = Settings.from_env()

        self.assertEqual(settings.download_worker_max_jobs, 3)
        self.assertEqual(settings.audio_separation_worker_max_jobs, 2)

    def test_performance_mode_sets_fragment_download_parallelism(self) -> None:
        os.environ["XDL_PERFORMANCE_MODE"] = "performance"
        os.environ.pop("XDL_YTDLP_CONCURRENT_FRAGMENTS", None)

        settings = Settings.from_env()

        self.assertEqual(settings.ytdlp_concurrent_fragments, 8)

    def test_explicit_fragment_parallelism_overrides_performance_mode(self) -> None:
        os.environ["XDL_PERFORMANCE_MODE"] = "low_power"
        os.environ["XDL_YTDLP_CONCURRENT_FRAGMENTS"] = "6"

        settings = Settings.from_env()

        self.assertEqual(settings.ytdlp_concurrent_fragments, 6)

    def test_download_engine_accepts_rate_limit_and_external_downloader(self) -> None:
        os.environ["XDL_DOWNLOAD_RATE_LIMIT"] = "5M"
        os.environ["XDL_YTDLP_EXTERNAL_DOWNLOADER"] = "aria2c"
        os.environ["XDL_YTDLP_EXTERNAL_DOWNLOADER_ARGS"] = "aria2c:-x 8 -s 8 -k 1M"

        settings = Settings.from_env()

        self.assertEqual(settings.download_rate_limit, "5M")
        self.assertEqual(settings.ytdlp_external_downloader, "aria2c")
        self.assertEqual(settings.ytdlp_external_downloader_args, "aria2c:-x 8 -s 8 -k 1M")

    def test_download_engine_accepts_speed_format_strategy_and_ffmpeg_threads(self) -> None:
        os.environ["XDL_YTDLP_FORMAT_STRATEGY"] = "speed"
        os.environ["XDL_FFMPEG_THREADS"] = "0"

        settings = Settings.from_env()

        self.assertEqual(settings.ytdlp_format_strategy, "speed")
        self.assertEqual(settings.ffmpeg_threads, 0)

    def test_download_engine_accepts_adaptive_format_strategy_alias(self) -> None:
        os.environ["XDL_YTDLP_FORMAT_STRATEGY"] = "auto"

        settings = Settings.from_env()

        self.assertEqual(settings.ytdlp_format_strategy, "adaptive")

    def test_background_runner_keeps_audio_separation_single_flight(self) -> None:
        started: list[str] = []
        first_started = threading.Event()
        second_started = threading.Event()
        release_first = threading.Event()

        class BlockingWorker:
            def run(self, job_id: str) -> None:
                started.append(job_id)
                if job_id == "audio-1":
                    first_started.set()
                    release_first.wait(timeout=2)
                if job_id == "audio-2":
                    second_started.set()

        runner = BackgroundJobRunner(
            BlockingWorker(),
            max_jobs=4,
            download_max_jobs=4,
            audio_separation_max_jobs=1,
        )
        try:
            self.assertTrue(runner.dispatch("audio-1", job_type=JobType.AUDIO_SEPARATION))
            self.assertTrue(runner.dispatch("audio-2", job_type=JobType.AUDIO_SEPARATION))

            self.assertTrue(first_started.wait(timeout=1))
            self.assertFalse(second_started.wait(timeout=0.1))
            self.assertEqual(started, ["audio-1"])

            release_first.set()
            self.assertTrue(second_started.wait(timeout=1))
            self.assertEqual(started, ["audio-1", "audio-2"])
        finally:
            release_first.set()
            runner.close(wait=True)

    def test_background_runner_runs_download_while_heavy_task_is_busy(self) -> None:
        started: list[str] = []
        audio_started = threading.Event()
        download_started = threading.Event()
        release_audio = threading.Event()

        class BlockingWorker:
            def run(self, job_id: str) -> None:
                started.append(job_id)
                if job_id == "audio":
                    audio_started.set()
                    release_audio.wait(timeout=2)
                if job_id == "download":
                    download_started.set()

        runner = BackgroundJobRunner(
            BlockingWorker(),
            max_jobs=1,
            download_max_jobs=1,
            audio_separation_max_jobs=1,
        )
        try:
            self.assertTrue(runner.dispatch("audio", job_type=JobType.AUDIO_SEPARATION))
            self.assertTrue(audio_started.wait(timeout=1))
            self.assertTrue(runner.dispatch("download", job_type=JobType.DOWNLOAD))

            self.assertTrue(download_started.wait(timeout=1))
            self.assertEqual(started[:2], ["audio", "download"])
        finally:
            release_audio.set()
            runner.close(wait=True)

    def test_youtube_cookie_status_requires_device_authentication(self) -> None:
        response = self.client.get("/api/v1/youtube/cookies/status", headers=self.local_headers)

        self.assertEqual(response.status_code, 401)

    def test_upload_youtube_cookies_persists_file_with_private_permissions(self) -> None:
        content = b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\ttest-cookie\n"

        response = self.client.post(
            "/api/v1/youtube/cookies",
            headers=self.mac_auth_headers(),
            files={"file": ("cookies.txt", content, "text/plain")},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertTrue(data["is_configured"])
        self.assertEqual(data["file_size"], len(content))
        self.assertNotIn("test-cookie", response.text)
        cookie_path = self.container.settings.youtube_cookies_file
        self.assertEqual(cookie_path.read_bytes(), content)
        self.assertEqual(cookie_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(cookie_path.parent.stat().st_mode & 0o777, 0o700)

    def test_upload_youtube_cookies_accepts_httponly_cookie_lines(self) -> None:
        content = b"#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\ttest-cookie\n"

        response = self.client.post(
            "/api/v1/youtube/cookies",
            headers=self.mac_auth_headers(),
            files={"file": ("cookies.txt", content, "text/plain")},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["data"]["is_configured"])

    def test_upload_youtube_cookies_requires_macos_device(self) -> None:
        content = b".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\ttest-cookie\n"

        response = self.client.post(
            "/api/v1/youtube/cookies",
            headers=self.auth_headers(),
            files={"file": ("cookies.txt", content, "text/plain")},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "access_denied")

    def test_delete_youtube_cookies_requires_macos_device(self) -> None:
        cookie_path = self.container.settings.youtube_cookies_file
        cookie_path.parent.mkdir(parents=True, exist_ok=True)
        cookie_path.write_bytes(b".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tsecret-value\n")

        response = self.client.request("DELETE", "/api/v1/youtube/cookies", headers=self.auth_headers())

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "access_denied")
        self.assertTrue(cookie_path.exists())

    def test_youtube_cookie_status_does_not_expose_cookie_contents(self) -> None:
        cookie_path = self.container.settings.youtube_cookies_file
        cookie_path.parent.mkdir(parents=True, exist_ok=True)
        cookie_path.write_bytes(b".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tsecret-value\n")

        response = self.client.get("/api/v1/youtube/cookies/status", headers=self.auth_headers())

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertTrue(data["is_configured"])
        self.assertGreater(data["file_size"], 0)
        self.assertNotIn("secret-value", response.text)

    def test_upload_youtube_cookies_rejects_invalid_file(self) -> None:
        response = self.client.post(
            "/api/v1/youtube/cookies",
            headers=self.mac_auth_headers(),
            files={"file": ("cookies.txt", b"not a cookie file", "text/plain")},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation_error")

    def test_upload_youtube_cookies_rejects_lookalike_domain(self) -> None:
        content = b".notyoutube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tfake-cookie\n"

        response = self.client.post(
            "/api/v1/youtube/cookies",
            headers=self.mac_auth_headers(),
            files={"file": ("cookies.txt", content, "text/plain")},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation_error")
        self.assertFalse(self.container.settings.youtube_cookies_file.exists())

    def test_upload_youtube_cookies_rejects_oversized_file(self) -> None:
        self.container.settings.youtube_cookies_max_bytes = 4

        response = self.client.post(
            "/api/v1/youtube/cookies",
            headers=self.mac_auth_headers(),
            files={"file": ("cookies.txt", b"12345", "text/plain")},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation_error")

    def test_delete_youtube_cookies_removes_file(self) -> None:
        cookie_path = self.container.settings.youtube_cookies_file
        cookie_path.parent.mkdir(parents=True, exist_ok=True)
        cookie_path.write_bytes(b".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tsecret-value\n")

        response = self.client.request("DELETE", "/api/v1/youtube/cookies", headers=self.mac_auth_headers())

        self.assertEqual(response.status_code, 200)
        self.assertFalse(cookie_path.exists())
        self.assertFalse(response.json()["data"]["is_configured"])

    def test_health_shutdown_requires_matching_local_proof(self) -> None:
        with patch("app.api.v1.health.os.kill") as kill_mock:
            response = self.client.post("/api/v1/health/shutdown?nonce=abc", headers={**self.local_headers, "X-XDownloader-Local-Proof": "bad"})

        self.assertEqual(response.status_code, 403)
        kill_mock.assert_not_called()

    def test_health_shutdown_accepts_matching_local_proof(self) -> None:
        proof = "727e536b61b913c00f128e5a00319a68ec7a4ffdad3218f9b9006f871583b05e"
        with patch("app.api.v1.health.os.kill") as kill_mock:
            response = self.client.post("/api/v1/health/shutdown?nonce=abc", headers={**self.local_headers, "X-XDownloader-Local-Proof": proof})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], "shutting_down")
        kill_mock.assert_called_once()

    def test_reject_missing_local_secret(self) -> None:
        response = self.client.post(
            "/api/v1/devices/register",
            json={"device_name": "Test iPhone", "platform": "ios", "app_version": "0.1.0"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "local_service_untrusted")

    def test_reject_api_when_local_secret_is_not_configured(self) -> None:
        self.container.settings.local_secret = ""
        response = self.client.post(
            "/api/v1/devices/register",
            headers={"X-XDownloader-Local-Secret": "test-local-secret"},
            json={"device_name": "Test iPhone", "platform": "ios", "app_version": "0.1.0"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "local_service_untrusted")

    def test_register_and_authenticate_device(self) -> None:
        token = self.register_device()
        response = self.client.get("/api/v1/devices/me", headers={**self.local_headers, "Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["name"], "Test iPhone")
        self.assertEqual(data["platform"], "ios")

    def test_cloud_mode_register_accepts_valid_bootstrap_without_local_secret(self) -> None:
        self.container.settings.cloud_mode = True
        response = self.client.post(
            "/api/v1/devices/register",
            json={
                "device_name": "Cloud iPhone",
                "platform": "ios",
                "app_version": "0.1.0",
                "bootstrap_code": "test-bootstrap",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["data"]["access_token"])

    def test_cloud_mode_register_rejects_missing_bootstrap(self) -> None:
        self.container.settings.cloud_mode = True
        response = self.client.post(
            "/api/v1/devices/register",
            json={"device_name": "Cloud iPhone", "platform": "ios", "app_version": "0.1.0"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "access_denied")

    def test_cloud_mode_register_rejects_invalid_bootstrap(self) -> None:
        self.container.settings.cloud_mode = True
        response = self.client.post(
            "/api/v1/devices/register",
            json={
                "device_name": "Cloud iPhone",
                "platform": "ios",
                "app_version": "0.1.0",
                "bootstrap_code": "wrong",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "access_denied")

    def test_cloud_mode_register_rejects_unconfigured_bootstrap(self) -> None:
        self.container.settings.cloud_mode = True
        self.container.settings.bootstrap_code = ""
        response = self.client.post(
            "/api/v1/devices/register",
            json={
                "device_name": "Cloud iPhone",
                "platform": "ios",
                "app_version": "0.1.0",
                "bootstrap_code": "test-bootstrap",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "access_denied")

    def test_reject_invalid_device_token(self) -> None:
        response = self.client.get("/api/v1/devices/me", headers={**self.local_headers, "Authorization": "Bearer invalid-token"})
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

    def test_database_migrates_legacy_dedupe_keys(self) -> None:
        from app.services.database import Database

        self.container.close()
        database_path = Path(os.environ["XDL_DATABASE_PATH"])
        if database_path.exists():
            database_path.unlink()
        conn = sqlite3.connect(database_path)
        conn.executescript(
            """
            CREATE TABLE devices (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                platform TEXT NOT NULL,
                app_version TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                is_active INTEGER NOT NULL
            );
            CREATE TABLE register_attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, client_key TEXT NOT NULL, attempted_at TEXT NOT NULL);
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                source_url TEXT NOT NULL,
                normalized_url TEXT NOT NULL,
                dedupe_key TEXT NOT NULL,
                is_active INTEGER NOT NULL,
                provider TEXT,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL,
                error_code TEXT,
                error_message TEXT,
                user_message TEXT,
                media_title TEXT,
                author_handle TEXT,
                thumbnail_url TEXT,
                artifact_id TEXT,
                selected_quality TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT
            );
            CREATE TABLE artifacts (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                file_name TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO jobs (
                id, device_id, source_url, normalized_url, dedupe_key, is_active, provider, status, progress,
                error_code, error_message, user_message, media_title, author_handle, thumbnail_url, artifact_id,
                selected_quality, created_at, updated_at, finished_at
            ) VALUES (
                'job-1', 'device-1', 'https://x.com/demo/status/123', 'https://x.com/demo/status/123',
                'device-1:https://x.com/demo/status/123', 1, NULL, 'queued', 0,
                NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                '2026-04-27T00:00:00+00:00', '2026-04-27T00:00:00+00:00', NULL
            );
            """
        )
        conn.commit()
        conn.close()

        Database(self.container.settings).initialize()
        conn = sqlite3.connect(database_path)
        dedupe_key = conn.execute("SELECT dedupe_key FROM jobs WHERE id = 'job-1'").fetchone()[0]
        conn.close()

        self.assertEqual(dedupe_key, "device-1:download:https://x.com/demo/status/123")
        self.container = build_container()
        app.state.container = self.container
        self.client = TestClient(app)

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
            ("https://m.douyin.com/share/video/123456/?foo=bar", "https://m.douyin.com/share/video/123456/"),
            ("https://www.iesdouyin.com/share/video/123456/?foo=bar", "https://www.iesdouyin.com/share/video/123456/"),
            ("https://h5.pipix.com/s/abc123/?foo=bar", "https://h5.pipix.com/s/abc123/"),
            ("https://www.pipix.com/item/123456?foo=bar", "https://www.pipix.com/item/123456"),
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

    def test_retry_terminal_job_returns_existing_active_duplicate(self) -> None:
        headers = self.auth_headers()
        first = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        first_job_id = first.json()["data"]["id"]
        self.container.job_service._repository.update_status(
            first_job_id,
            status=JobStatus.FAILED,
            progress=45,
            error_code="download_error",
            error_message="failed",
            user_message="下载失败。",
        )
        second = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        retry = self.client.post(f"/api/v1/jobs/{first_job_id}/retry", headers=headers)

        self.assertEqual(second.status_code, 200)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["data"]["id"], second.json()["data"]["id"])
        self.assertEqual(retry.json()["data"]["status"], "queued")

    def test_preview_job_returns_metadata_without_creating_job(self) -> None:
        headers = self.auth_headers()
        extracted = ExtractedMedia(
            provider="yt-dlp",
            title="Preview title",
            author_handle="author",
            thumbnail_url="https://example.com/thumb.jpg",
            direct_url=None,
            webpage_url="https://x.com/demo/status/12345",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )

        with patch.object(self.container.job_service._selector, "extract", return_value=extracted) as extract_mock:
            response = self.client.post(
                "/api/v1/jobs/preview",
                json={"url": "https://x.com/demo/status/12345?foo=bar"},
                headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["source_url"], "https://x.com/demo/status/12345?foo=bar")
        self.assertEqual(data["normalized_url"], "https://x.com/demo/status/12345")
        self.assertEqual(data["provider"], "yt-dlp")
        self.assertEqual(data["title"], "Preview title")
        self.assertEqual(data["author_handle"], "author")
        self.assertEqual(data["thumbnail_url"], "https://example.com/thumb.jpg")
        self.assertEqual(data["file_extension"], "mp4")
        self.assertEqual(data["recommended_job_type"], "download")
        self.assertFalse(data["can_reuse_existing"])
        self.assertIsNone(data["existing_job_id"])
        self.assertEqual(self.container.job_service._repository.list_for_device(self.container.device_service.authenticate(headers["Authorization"].split(" ", maxsplit=1)[1]).id), [])
        extract_mock.assert_called_once_with("https://x.com/demo/status/12345")

    def test_preview_job_rejects_unsupported_url(self) -> None:
        response = self.client.post(
            "/api/v1/jobs/preview",
            json={"url": "https://example.com/video/123"},
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation_error")

    def test_preview_job_returns_existing_completed_artifact(self) -> None:
        headers = self.mac_auth_headers()
        device = self.container.device_service.authenticate(headers["Authorization"].split(" ", maxsplit=1)[1])
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://x.com/demo/status/12345",
            normalized_url="https://x.com/demo/status/12345",
            selected_quality=None,
            job_type=JobType.DOWNLOAD,
            media_title="Existing title",
        )
        artifact_path = self.container.settings.artifacts_dir / "existing.mp4"
        artifact_path.write_bytes(b"video")
        artifact = self.container.artifact_service._artifacts.create(
            job_id=job.id,
            file_name="existing.mp4",
            mime_type="video/mp4",
            storage_path=str(artifact_path),
            file_size=artifact_path.stat().st_size,
        )
        self.container.job_service._repository.update_status(
            job.id,
            status=JobStatus.COMPLETED,
            progress=100,
            provider="yt-dlp",
            media_title="Existing title",
            author_handle="author",
            thumbnail_url="https://example.com/thumb.jpg",
            artifact_id=artifact.id,
        )
        extracted = ExtractedMedia(
            provider="yt-dlp",
            title="Existing title",
            author_handle="author",
            thumbnail_url="https://example.com/thumb.jpg",
            direct_url=None,
            webpage_url="https://x.com/demo/status/12345",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )

        with patch.object(self.container.job_service._selector, "extract", return_value=extracted):
            response = self.client.post(
                "/api/v1/jobs/preview",
                json={"url": "https://x.com/demo/status/12345"},
                headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertTrue(data["can_reuse_existing"])
        self.assertEqual(data["existing_job_id"], job.id)
        self.assertEqual(data["existing_artifact_id"], artifact.id)
        self.assertEqual(data["existing_file_name"], "existing.mp4")
        self.assertEqual(data["existing_local_path"], str(artifact_path.resolve()))

    def test_preview_job_hides_existing_local_path_for_non_macos_device(self) -> None:
        headers = self.auth_headers(platform="ios")
        device = self.container.device_service.authenticate(headers["Authorization"].split(" ", maxsplit=1)[1])
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://x.com/demo/status/12345",
            normalized_url="https://x.com/demo/status/12345",
            selected_quality=None,
            job_type=JobType.DOWNLOAD,
        )
        artifact_path = self.container.settings.artifacts_dir / "ios-hidden.mp4"
        artifact_path.write_bytes(b"video")
        artifact = self.container.artifact_service._artifacts.create(
            job_id=job.id,
            file_name="ios-hidden.mp4",
            mime_type="video/mp4",
            storage_path=str(artifact_path),
            file_size=artifact_path.stat().st_size,
        )
        self.container.job_service._repository.update_status(
            job.id,
            status=JobStatus.COMPLETED,
            progress=100,
            artifact_id=artifact.id,
        )
        extracted = ExtractedMedia(
            provider="yt-dlp",
            title="iOS hidden",
            author_handle=None,
            thumbnail_url=None,
            direct_url=None,
            webpage_url="https://x.com/demo/status/12345",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )

        with patch.object(self.container.job_service._selector, "extract", return_value=extracted):
            response = self.client.post(
                "/api/v1/jobs/preview",
                json={"url": "https://x.com/demo/status/12345"},
                headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertIsNone(data["existing_local_path"])
        self.assertFalse(data["can_reuse_existing"])

    def test_preview_job_hides_existing_local_path_in_cloud_mode(self) -> None:
        headers = self.mac_auth_headers()
        device = self.container.device_service.authenticate(headers["Authorization"].split(" ", maxsplit=1)[1])
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://x.com/demo/status/12345",
            normalized_url="https://x.com/demo/status/12345",
            selected_quality=None,
            job_type=JobType.DOWNLOAD,
        )
        artifact_path = self.container.settings.artifacts_dir / "cloud-hidden.mp4"
        artifact_path.write_bytes(b"video")
        artifact = self.container.artifact_service._artifacts.create(
            job_id=job.id,
            file_name="cloud-hidden.mp4",
            mime_type="video/mp4",
            storage_path=str(artifact_path),
            file_size=artifact_path.stat().st_size,
        )
        self.container.job_service._repository.update_status(
            job.id,
            status=JobStatus.COMPLETED,
            progress=100,
            artifact_id=artifact.id,
        )
        self.container.settings.cloud_mode = True
        extracted = ExtractedMedia(
            provider="yt-dlp",
            title="Cloud hidden",
            author_handle=None,
            thumbnail_url=None,
            direct_url=None,
            webpage_url="https://x.com/demo/status/12345",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )

        with patch.object(self.container.job_service._selector, "extract", return_value=extracted):
            response = self.client.post(
                "/api/v1/jobs/preview",
                json={"url": "https://x.com/demo/status/12345"},
                headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertIsNone(data["existing_local_path"])
        self.assertFalse(data["can_reuse_existing"])

    def test_preview_job_does_not_reuse_missing_existing_file(self) -> None:
        headers = self.mac_auth_headers()
        device = self.container.device_service.authenticate(headers["Authorization"].split(" ", maxsplit=1)[1])
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://x.com/demo/status/12345",
            normalized_url="https://x.com/demo/status/12345",
            selected_quality=None,
            job_type=JobType.DOWNLOAD,
        )
        missing_path = self.container.settings.artifacts_dir / "missing-preview.mp4"
        artifact = self.container.artifact_service._artifacts.create(
            job_id=job.id,
            file_name="missing-preview.mp4",
            mime_type="video/mp4",
            storage_path=str(missing_path),
            file_size=123,
        )
        self.container.job_service._repository.update_status(
            job.id,
            status=JobStatus.COMPLETED,
            progress=100,
            artifact_id=artifact.id,
        )
        extracted = ExtractedMedia(
            provider="yt-dlp",
            title="Missing file",
            author_handle=None,
            thumbnail_url=None,
            direct_url=None,
            webpage_url="https://x.com/demo/status/12345",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )

        with patch.object(self.container.job_service._selector, "extract", return_value=extracted):
            response = self.client.post(
                "/api/v1/jobs/preview",
                json={"url": "https://x.com/demo/status/12345"},
                headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["existing_job_id"], job.id)
        self.assertEqual(data["existing_artifact_id"], artifact.id)
        self.assertIsNone(data["existing_local_path"])
        self.assertFalse(data["can_reuse_existing"])

    def test_create_audio_download_job(self) -> None:
        response = self.client.post(
            "/api/v1/jobs/audio-download",
            json={"url": "https://x.com/demo/status/12345?foo=bar#frag"},
            headers=self.auth_headers(),
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["job_type"], "audio_download")
        self.assertEqual(data["status"], "queued")
        self.assertEqual(data["normalized_url"], "https://x.com/demo/status/12345")

    def test_create_job_rejects_incomplete_new_platform_urls(self) -> None:
        urls = [
            "https://www.douyin.com/video/",
            "https://m.douyin.com/share/video/",
            "https://m.douyin.com/share/video/123456/extra",
            "https://m.douyin.com/share/video/%2F123456",
            "https://h5.pipix.com/s/",
            "https://h5.pipix.com/s/abc123/extra",
            "https://h5.pipix.com/s/%2Fabc123",
            "https://www.pipix.com/item/",
            "https://www.pipix.com/item/123456/extra",
            "https://www.pipix.com/item/%2F123456",
        ]
        headers = self.auth_headers()
        for url in urls:
            with self.subTest(url=url):
                response = self.client.post(
                    "/api/v1/jobs",
                    json={"url": url},
                    headers=headers,
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["error"]["code"], "validation_error")

    def test_create_audio_download_job_accepts_new_platform_urls(self) -> None:
        cases = [
            ("https://m.douyin.com/share/video/123456/?foo=bar", "https://m.douyin.com/share/video/123456/"),
            ("https://h5.pipix.com/s/abc123/?foo=bar", "https://h5.pipix.com/s/abc123/"),
        ]
        headers = self.auth_headers()
        for raw_url, normalized_url in cases:
            with self.subTest(raw_url=raw_url):
                response = self.client.post(
                    "/api/v1/jobs/audio-download",
                    json={"url": raw_url},
                    headers=headers,
                )
                self.assertEqual(response.status_code, 200)
                data = response.json()["data"]
                self.assertEqual(data["job_type"], "audio_download")
                self.assertEqual(data["normalized_url"], normalized_url)

    def test_audio_download_and_video_download_do_not_dedupe_each_other(self) -> None:
        headers = self.auth_headers()
        video = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        audio = self.client.post(
            "/api/v1/jobs/audio-download",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )

        self.assertEqual(video.status_code, 200)
        self.assertEqual(audio.status_code, 200)
        self.assertNotEqual(video.json()["data"]["id"], audio.json()["data"]["id"])
        self.assertEqual(video.json()["data"]["job_type"], "download")
        self.assertEqual(audio.json()["data"]["job_type"], "audio_download")

    def test_deduplicate_active_audio_download_job(self) -> None:
        headers = self.auth_headers()
        first = self.client.post(
            "/api/v1/jobs/audio-download",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        second = self.client.post(
            "/api/v1/jobs/audio-download",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["data"]["id"], second.json()["data"]["id"])
        self.assertEqual(second.json()["data"]["job_type"], "audio_download")

    def test_delete_job_removes_terminal_record_and_artifact(self) -> None:
        headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        artifact_path = self.container.settings.artifacts_dir / "delete-me.mp4"
        thumbnail_path = self.container.settings.artifacts_dir / "delete-me.thumbnail.jpg"
        artifact_path.write_bytes(b"video")
        thumbnail_path.write_bytes(b"thumbnail")
        artifact = self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="delete-me.mp4",
            mime_type="video/mp4",
            storage_path=str(artifact_path),
            file_size=artifact_path.stat().st_size,
            thumbnail_path=str(thumbnail_path),
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
        self.assertFalse(thumbnail_path.exists())
        self.assertIsNone(self.container.job_service._repository.get(job_id))
        self.assertIsNone(self.container.artifact_service._artifacts.get(artifact.id))

    def test_list_job_logs_returns_events_for_owned_job(self) -> None:
        headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        self.container.job_service.record_event(job_id, level="info", event_type="resolving", message="开始解析链接")
        self.container.job_service.record_event(job_id, level="info", event_type="downloading", message="开始下载素材")

        logs_response = self.client.get(f"/api/v1/jobs/{job_id}/logs", headers=headers)

        self.assertEqual(logs_response.status_code, 200)
        data = logs_response.json()["data"]
        self.assertEqual(data["job_id"], job_id)
        self.assertEqual([item["message"] for item in data["items"]], ["任务已加入队列", "开始解析链接", "开始下载素材"])
        self.assertEqual(data["items"][1]["level"], "info")
        self.assertEqual(data["items"][1]["event_type"], "resolving")
        self.assertIsInstance(data["items"][0]["id"], int)
        self.assertIsNotNone(data["items"][0]["created_at"])

    def test_list_job_logs_is_scoped_to_owner(self) -> None:
        owner_headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=owner_headers,
        )
        job_id = response.json()["data"]["id"]
        self.container.job_service.record_event(job_id, level="info", event_type="queued", message="任务已加入队列")

        other_headers = self.auth_headers()
        logs_response = self.client.get(f"/api/v1/jobs/{job_id}/logs", headers=other_headers)

        self.assertEqual(logs_response.status_code, 404)

    def test_job_logs_redact_sensitive_message_parts(self) -> None:
        headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        event = self.container.job_service.record_event(
            job_id,
            level="error",
            event_type="failed",
            message="token=abc https://example.com/video?sig=secret /Users/test/private/file.mp4",
        )

        self.assertNotIn("abc", event.message)
        self.assertNotIn("sig=secret", event.message)
        self.assertNotIn("/Users/test", event.message)

    def test_list_job_logs_supports_limit_and_after_id(self) -> None:
        headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        first = self.container.job_service.record_event(job_id, level="info", event_type="queued", message="任务已加入队列")
        self.container.job_service.record_event(job_id, level="info", event_type="resolving", message="开始解析链接")
        self.container.job_service.record_event(job_id, level="info", event_type="downloading", message="开始下载素材")

        logs_response = self.client.get(f"/api/v1/jobs/{job_id}/logs?limit=1&after_id={first.id}", headers=headers)

        self.assertEqual(logs_response.status_code, 200)
        items = logs_response.json()["data"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["message"], "开始解析链接")

    def test_delete_job_removes_related_logs(self) -> None:
        headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        self.container.job_service._repository.update_status(job_id, status=JobStatus.FAILED, progress=100)
        self.container.job_service.record_event(job_id, level="error", event_type="failed", message="下载失败")

        delete_response = self.client.request("DELETE", f"/api/v1/jobs/{job_id}", headers=headers)

        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(self.container.job_service.list_events(job_id), [])

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

    def test_delete_artifact_removes_file_and_keeps_job_record(self) -> None:
        headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        artifact_path = self.container.settings.artifacts_dir / "source.mp4"
        thumbnail_path = self.container.settings.artifacts_dir / "source.thumbnail.jpg"
        artifact_path.write_bytes(b"video")
        thumbnail_path.write_bytes(b"thumbnail")
        artifact = self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="source.mp4",
            mime_type="video/mp4",
            storage_path=str(artifact_path),
            file_size=artifact_path.stat().st_size,
            thumbnail_path=str(thumbnail_path),
        )
        self.container.job_service._repository.update_status(
            job_id,
            status=JobStatus.COMPLETED,
            progress=100,
            artifact_id=artifact.id,
        )

        delete_response = self.client.request("DELETE", f"/api/v1/artifacts/{artifact.id}", headers=headers)

        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.json()["data"]["deleted"], True)
        self.assertFalse(artifact_path.exists())
        self.assertFalse(thumbnail_path.exists())
        job = self.container.job_service._repository.get(job_id)
        self.assertIsNotNone(job)
        self.assertIsNone(job.artifact_id)
        self.assertIsNone(self.container.artifact_service._artifacts.get(artifact.id))

    def test_delete_artifact_rejects_symlink_artifact_path(self) -> None:
        headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        outside_path = Path(self.temp_dir.name) / "outside.mp4"
        outside_path.write_bytes(b"outside")
        symlink_path = self.container.settings.artifacts_dir / "source.mp4"
        symlink_path.symlink_to(outside_path)
        artifact = self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="source.mp4",
            mime_type="video/mp4",
            storage_path=str(symlink_path),
            file_size=outside_path.stat().st_size,
        )
        self.container.job_service._repository.update_status(job_id, status=JobStatus.COMPLETED, progress=100, artifact_id=artifact.id)

        delete_response = self.client.request("DELETE", f"/api/v1/artifacts/{artifact.id}", headers=headers)

        self.assertEqual(delete_response.status_code, 409)
        self.assertTrue(outside_path.exists())
        self.assertTrue(symlink_path.is_symlink())

    def test_delete_artifact_rejects_missing_thumbnail_outside_allowed_roots(self) -> None:
        headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        artifact_path = self.container.settings.artifacts_dir / "source.mp4"
        artifact_path.write_bytes(b"video")
        artifact = self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="source.mp4",
            mime_type="video/mp4",
            storage_path=str(artifact_path),
            file_size=artifact_path.stat().st_size,
            thumbnail_path=str(Path(self.temp_dir.name) / "missing-outside-thumbnail.jpg"),
        )
        self.container.job_service._repository.update_status(job_id, status=JobStatus.COMPLETED, progress=100, artifact_id=artifact.id)

        delete_response = self.client.request("DELETE", f"/api/v1/artifacts/{artifact.id}", headers=headers)

        self.assertEqual(delete_response.status_code, 409)
        self.assertTrue(artifact_path.exists())
        self.assertIsNotNone(self.container.artifact_service._artifacts.get(artifact.id))

    def test_delete_artifact_succeeds_when_file_is_missing(self) -> None:
        headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        artifact_path = self.container.settings.artifacts_dir / "missing.mp4"
        artifact = self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="missing.mp4",
            mime_type="video/mp4",
            storage_path=str(artifact_path),
            file_size=0,
        )
        self.container.job_service._repository.update_status(job_id, status=JobStatus.COMPLETED, progress=100, artifact_id=artifact.id)

        delete_response = self.client.request("DELETE", f"/api/v1/artifacts/{artifact.id}", headers=headers)

        self.assertEqual(delete_response.status_code, 200)
        self.assertIsNone(self.container.artifact_service._artifacts.get(artifact.id))

    def test_delete_artifact_rejects_other_device_artifact(self) -> None:
        owner_headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=owner_headers,
        )
        job_id = response.json()["data"]["id"]
        artifact_path = self.container.settings.artifacts_dir / "owned.mp4"
        artifact_path.write_bytes(b"video")
        artifact = self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="owned.mp4",
            mime_type="video/mp4",
            storage_path=str(artifact_path),
            file_size=artifact_path.stat().st_size,
        )
        self.container.job_service._repository.update_status(job_id, status=JobStatus.COMPLETED, progress=100, artifact_id=artifact.id)

        delete_response = self.client.request("DELETE", f"/api/v1/artifacts/{artifact.id}", headers=self.auth_headers())

        self.assertEqual(delete_response.status_code, 404)
        self.assertTrue(artifact_path.exists())
        self.assertIsNotNone(self.container.artifact_service._artifacts.get(artifact.id))

    def test_delete_artifact_rejects_active_job_artifact(self) -> None:
        headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        artifact_path = self.container.settings.artifacts_dir / "active.mp4"
        artifact_path.write_bytes(b"video")
        artifact = self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="active.mp4",
            mime_type="video/mp4",
            storage_path=str(artifact_path),
            file_size=artifact_path.stat().st_size,
        )

        delete_response = self.client.request("DELETE", f"/api/v1/artifacts/{artifact.id}", headers=headers)

        self.assertEqual(delete_response.status_code, 409)
        self.assertTrue(artifact_path.exists())
        self.assertIsNotNone(self.container.artifact_service._artifacts.get(artifact.id))

    def test_delete_artifact_does_not_unlink_file_outside_allowed_roots(self) -> None:
        headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        outside_path = Path(self.temp_dir.name) / "outside.mp4"
        outside_path.write_bytes(b"outside")
        artifact = self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="outside.mp4",
            mime_type="video/mp4",
            storage_path=str(outside_path),
            file_size=outside_path.stat().st_size,
        )
        self.container.job_service._repository.update_status(job_id, status=JobStatus.COMPLETED, progress=100, artifact_id=artifact.id)

        delete_response = self.client.request("DELETE", f"/api/v1/artifacts/{artifact.id}", headers=headers)

        self.assertEqual(delete_response.status_code, 409)
        self.assertTrue(outside_path.exists())
        self.assertIsNotNone(self.container.artifact_service._artifacts.get(artifact.id))

    def test_delete_job_rejects_outside_allowed_root_without_partial_delete(self) -> None:
        headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        allowed_path = self.container.settings.artifacts_dir / "allowed.mp4"
        outside_path = Path(self.temp_dir.name) / "outside-job.mp4"
        allowed_path.write_bytes(b"allowed")
        outside_path.write_bytes(b"outside")
        allowed_artifact = self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="allowed.mp4",
            mime_type="video/mp4",
            storage_path=str(allowed_path),
            file_size=allowed_path.stat().st_size,
        )
        outside_artifact = self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="outside-job.mp4",
            mime_type="video/mp4",
            storage_path=str(outside_path),
            file_size=outside_path.stat().st_size,
        )
        self.container.job_service._repository.update_status(job_id, status=JobStatus.COMPLETED, progress=100, artifact_id=allowed_artifact.id)

        delete_response = self.client.request("DELETE", f"/api/v1/jobs/{job_id}", headers=headers)

        self.assertEqual(delete_response.status_code, 409)
        self.assertTrue(allowed_path.exists())
        self.assertTrue(outside_path.exists())
        self.assertIsNotNone(self.container.artifact_service._artifacts.get(allowed_artifact.id))
        self.assertIsNotNone(self.container.artifact_service._artifacts.get(outside_artifact.id))
        self.assertIsNotNone(self.container.job_service._repository.get(job_id))

    def test_delete_artifact_removes_legacy_application_support_file(self) -> None:
        headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        legacy_dir = self.container.settings.database_path.parent / "Artifacts"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = legacy_dir / "legacy.mp4"
        artifact_path.write_bytes(b"legacy")
        artifact = self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="legacy.mp4",
            mime_type="video/mp4",
            storage_path=str(artifact_path),
            file_size=artifact_path.stat().st_size,
        )
        self.container.job_service._repository.update_status(job_id, status=JobStatus.COMPLETED, progress=100, artifact_id=artifact.id)

        delete_response = self.client.request("DELETE", f"/api/v1/artifacts/{artifact.id}", headers=headers)

        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(artifact_path.exists())

    def test_delete_job_history_removes_terminal_records_and_keeps_active_jobs(self) -> None:
        headers = self.auth_headers()
        completed = self.client.post("/api/v1/jobs", json={"url": "https://x.com/demo/status/11111"}, headers=headers).json()["data"]["id"]
        failed = self.client.post("/api/v1/jobs", json={"url": "https://x.com/demo/status/22222"}, headers=headers).json()["data"]["id"]
        active = self.client.post("/api/v1/jobs", json={"url": "https://x.com/demo/status/33333"}, headers=headers).json()["data"]["id"]
        completed_path = self.container.settings.artifacts_dir / "completed.mp4"
        completed_path.write_bytes(b"completed")
        legacy_dir = self.container.settings.database_path.parent / "Artifacts"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        failed_path = legacy_dir / "failed.mp4"
        failed_path.write_bytes(b"failed")
        completed_artifact = self.container.artifact_service._artifacts.create(
            job_id=completed,
            file_name="completed.mp4",
            mime_type="video/mp4",
            storage_path=str(completed_path),
            file_size=completed_path.stat().st_size,
        )
        failed_artifact = self.container.artifact_service._artifacts.create(
            job_id=failed,
            file_name="failed.mp4",
            mime_type="video/mp4",
            storage_path=str(failed_path),
            file_size=failed_path.stat().st_size,
        )
        self.container.job_service._repository.update_status(completed, status=JobStatus.COMPLETED, progress=100, artifact_id=completed_artifact.id)
        self.container.job_service._repository.update_status(failed, status=JobStatus.FAILED, progress=0, artifact_id=failed_artifact.id)

        delete_response = self.client.request("DELETE", "/api/v1/jobs/history", headers=headers)

        self.assertEqual(delete_response.status_code, 200)
        data = delete_response.json()["data"]
        self.assertEqual(data["deleted_count"], 2)
        self.assertEqual(data["skipped_active_count"], 1)
        self.assertCountEqual(data["deleted_job_ids"], [completed, failed])
        self.assertIsNone(self.container.job_service._repository.get(completed))
        self.assertIsNone(self.container.job_service._repository.get(failed))
        self.assertIsNotNone(self.container.job_service._repository.get(active))
        self.assertFalse(completed_path.exists())
        self.assertFalse(failed_path.exists())

    def test_create_audio_separation_job_uploads_audio_file(self) -> None:
        response = self.client.post(
            "/api/v1/jobs/audio-separation",
            headers=self.mac_auth_headers(),
            files={"file": ("song.mp3", b"audio-bytes", "audio/mpeg")},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["job_type"], "audio_separation")
        self.assertEqual(data["status"], "queued")
        self.assertEqual(data["source_url"], "upload:song.mp3")
        self.assertEqual(data["media_title"], "song")

    def test_audio_separation_same_file_name_creates_unique_inputs(self) -> None:
        repo = DeviceRepository(self.container.database)
        device = repo.create(name="Owner", platform=Platform.MACOS, app_version="1.0", token_hash="same-file-owner")

        first = self.container.job_service.create_audio_separation(device=device, file_name="song.mp3", content=b"first")
        second = self.container.job_service.create_audio_separation(device=device, file_name="song.mp3", content=b"second")

        self.assertNotEqual(first.id, second.id)
        self.assertNotEqual(first.normalized_url, second.normalized_url)
        self.assertEqual(Path(first.normalized_url.removeprefix("file:")).read_bytes(), b"first")
        self.assertEqual(Path(second.normalized_url.removeprefix("file:")).read_bytes(), b"second")

    def test_audio_separation_input_path_does_not_include_uploaded_file_stem(self) -> None:
        repo = DeviceRepository(self.container.database)
        device = repo.create(name="Owner", platform=Platform.MACOS, app_version="1.0", token_hash="safe-input-owner")

        job = self.container.job_service.create_audio_separation(device=device, file_name="song name -danger.mp3", content=b"audio")

        input_path = Path(job.normalized_url.removeprefix("file:"))
        self.assertTrue(input_path.name.endswith(".input.mp3"))
        self.assertNotIn("song", input_path.name)
        self.assertNotIn("danger", input_path.name)

    def test_audio_separation_rejects_unsupported_file(self) -> None:
        response = self.client.post(
            "/api/v1/jobs/audio-separation",
            headers=self.mac_auth_headers(),
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation_error")

    def test_audio_separation_rejects_oversized_file_before_service_create(self) -> None:
        self.container.settings.audio_upload_max_bytes = 4
        response = self.client.post(
            "/api/v1/jobs/audio-separation",
            headers=self.mac_auth_headers(),
            files={"file": ("song.mp3", b"12345", "audio/mpeg")},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["user_message"], "音频文件太大。")

    def test_job_artifacts_lists_owned_outputs(self) -> None:
        headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs/audio-separation",
            headers=headers,
            files={"file": ("song.wav", b"audio", "audio/wav")},
        )
        job_id = response.json()["data"]["id"]
        vocals_path = self.container.settings.artifacts_dir / "song.vocals.wav"
        accompaniment_path = self.container.settings.artifacts_dir / "song.accompaniment.wav"
        vocals_path.write_bytes(b"vocals")
        accompaniment_path.write_bytes(b"accompaniment")
        self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="song.vocals.wav",
            mime_type="audio/wav",
            storage_path=str(vocals_path),
            file_size=vocals_path.stat().st_size,
            role=ArtifactRole.VOCALS,
        )
        self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="song.accompaniment.wav",
            mime_type="audio/wav",
            storage_path=str(accompaniment_path),
            file_size=accompaniment_path.stat().st_size,
            role=ArtifactRole.ACCOMPANIMENT,
        )

        artifacts_response = self.client.get(f"/api/v1/jobs/{job_id}/artifacts", headers=headers)

        self.assertEqual(artifacts_response.status_code, 200)
        items = artifacts_response.json()["data"]["items"]
        self.assertEqual([item["role"] for item in items], ["vocals", "accompaniment"])

    def test_artifact_media_details_are_persisted_and_returned(self) -> None:
        headers = self.mac_auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        artifact_path = self.container.settings.artifacts_dir / "sample.mp4"
        artifact_path.write_bytes(b"video")
        self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="sample.mp4",
            mime_type="video/mp4",
            storage_path=str(artifact_path),
            file_size=artifact_path.stat().st_size,
            duration_seconds=12.5,
            width=1920,
            height=1080,
            video_codec="h264",
            audio_codec="aac",
            bitrate_kbps=4500,
            container_format="mov,mp4,m4a,3gp,3g2,mj2",
        )

        artifacts_response = self.client.get(f"/api/v1/jobs/{job_id}/artifacts", headers=headers)

        self.assertEqual(artifacts_response.status_code, 200)
        item = artifacts_response.json()["data"]["items"][0]
        self.assertEqual(item["local_path"], str(artifact_path.resolve()))
        self.assertEqual(item["duration_seconds"], 12.5)
        self.assertEqual(item["width"], 1920)
        self.assertEqual(item["height"], 1080)
        self.assertEqual(item["video_codec"], "h264")
        self.assertEqual(item["audio_codec"], "aac")
        self.assertEqual(item["bitrate_kbps"], 4500)
        self.assertEqual(item["container_format"], "mov,mp4,m4a,3gp,3g2,mj2")

    def test_artifact_summary_hides_local_path_for_non_macos_device(self) -> None:
        headers = self.auth_headers(platform="ios")
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        artifact_path = self.container.settings.artifacts_dir / "ios-sample.mp4"
        artifact_path.write_bytes(b"video")
        self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="ios-sample.mp4",
            mime_type="video/mp4",
            storage_path=str(artifact_path),
            file_size=artifact_path.stat().st_size,
        )

        artifacts_response = self.client.get(f"/api/v1/jobs/{job_id}/artifacts", headers=headers)

        self.assertEqual(artifacts_response.status_code, 200)
        self.assertIsNone(artifacts_response.json()["data"]["items"][0]["local_path"])

    def test_artifact_summary_returns_safe_thumbnail_local_path(self) -> None:
        headers = self.mac_auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        artifact_path = self.container.settings.artifacts_dir / "sample.mp4"
        thumbnail_path = self.container.settings.artifacts_dir / "sample.thumbnail.jpg"
        artifact_path.write_bytes(b"video")
        thumbnail_path.write_bytes(b"thumbnail")
        self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="sample.mp4",
            mime_type="video/mp4",
            storage_path=str(artifact_path),
            file_size=artifact_path.stat().st_size,
            thumbnail_path=str(thumbnail_path),
        )

        artifacts_response = self.client.get(f"/api/v1/jobs/{job_id}/artifacts", headers=headers)

        self.assertEqual(artifacts_response.status_code, 200)
        self.assertEqual(artifacts_response.json()["data"]["items"][0]["thumbnail_local_path"], str(thumbnail_path.resolve()))

    def test_artifact_summary_hides_local_paths_in_cloud_mode(self) -> None:
        headers = self.auth_headers()
        self.container.settings.cloud_mode = True
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        artifact_path = self.container.settings.artifacts_dir / "sample.mp4"
        thumbnail_path = self.container.settings.artifacts_dir / "sample.thumbnail.jpg"
        artifact_path.write_bytes(b"video")
        thumbnail_path.write_bytes(b"thumbnail")
        self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="sample.mp4",
            mime_type="video/mp4",
            storage_path=str(artifact_path),
            file_size=artifact_path.stat().st_size,
            thumbnail_path=str(thumbnail_path),
        )

        artifacts_response = self.client.get(f"/api/v1/jobs/{job_id}/artifacts", headers=headers)

        self.assertEqual(artifacts_response.status_code, 200)
        item = artifacts_response.json()["data"]["items"][0]
        self.assertIsNone(item["local_path"])
        self.assertIsNone(item["thumbnail_local_path"])

    def test_artifact_summary_hides_untrusted_thumbnail_local_path(self) -> None:
        headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        artifact_path = self.container.settings.artifacts_dir / "sample.mp4"
        outside_thumbnail_path = Path(self.temp_dir.name) / "outside-thumbnail.jpg"
        artifact_path.write_bytes(b"video")
        outside_thumbnail_path.write_bytes(b"thumbnail")
        self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="sample.mp4",
            mime_type="video/mp4",
            storage_path=str(artifact_path),
            file_size=artifact_path.stat().st_size,
            thumbnail_path=str(outside_thumbnail_path),
        )

        artifacts_response = self.client.get(f"/api/v1/jobs/{job_id}/artifacts", headers=headers)

        self.assertEqual(artifacts_response.status_code, 200)
        self.assertIsNone(artifacts_response.json()["data"]["items"][0]["thumbnail_local_path"])

    def test_artifact_summary_hides_untrusted_local_path(self) -> None:
        headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        outside_path = Path(self.temp_dir.name) / "outside-summary.mp4"
        outside_path.write_bytes(b"outside")
        artifact = self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="outside-summary.mp4",
            mime_type="video/mp4",
            storage_path=str(outside_path),
            file_size=outside_path.stat().st_size,
        )
        self.container.job_service._repository.update_status(job_id, status=JobStatus.COMPLETED, progress=100, artifact_id=artifact.id)

        artifacts_response = self.client.get(f"/api/v1/jobs/{job_id}/artifacts", headers=headers)

        self.assertEqual(artifacts_response.status_code, 200)
        self.assertIsNone(artifacts_response.json()["data"]["items"][0]["local_path"])

    def test_download_artifact_allows_legacy_application_support_file(self) -> None:
        headers = self.auth_headers()
        response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = response.json()["data"]["id"]
        legacy_dir = self.container.settings.database_path.parent / "Artifacts"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = legacy_dir / "legacy-download.mp4"
        artifact_path.write_bytes(b"legacy")
        artifact = self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="legacy-download.mp4",
            mime_type="video/mp4",
            storage_path=str(artifact_path),
            file_size=artifact_path.stat().st_size,
        )
        self.container.job_service._repository.update_status(job_id, status=JobStatus.COMPLETED, progress=100, artifact_id=artifact.id)

        download_response = self.client.get(f"/api/v1/artifacts/{artifact.id}/download", headers=headers)

        self.assertEqual(download_response.status_code, 200)
        self.assertEqual(download_response.content, b"legacy")

    def test_download_worker_records_ffprobe_media_details(self) -> None:
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
        )
        artifact_path = self.container.settings.artifacts_dir / "sample.mp4"
        artifact_path.write_bytes(b"video")
        ffprobe_output = b"""{
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "12.5", "bit_rate": "4500000"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                {"codec_type": "audio", "codec_name": "aac"}
            ]
        }"""

        with patch("app.workers.download_job_worker.subprocess_run") as run_mock:
            run_mock.return_value = subprocess.CompletedProcess(args=["ffprobe"], returncode=0, stdout=ffprobe_output, stderr=b"")
            details = worker._probe_media_details(artifact_path)

        self.assertEqual(details["duration_seconds"], 12.5)
        self.assertEqual(details["width"], 1920)
        self.assertEqual(details["height"], 1080)
        self.assertEqual(details["video_codec"], "h264")
        self.assertEqual(details["audio_codec"], "aac")
        self.assertEqual(details["bitrate_kbps"], 4500)
        self.assertEqual(details["container_format"], "mov,mp4,m4a,3gp,3g2,mj2")

    def test_download_worker_generates_thumbnail_only_for_video_download_with_video_stream(self) -> None:
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
        )

        self.assertTrue(worker._should_generate_thumbnail(JobType.DOWNLOAD, "video/mp4", {"video_codec": "h264"}))
        self.assertFalse(worker._should_generate_thumbnail(JobType.DOWNLOAD, "video/mp4", {}))
        self.assertFalse(worker._should_generate_thumbnail(JobType.AUDIO_DOWNLOAD, "video/mp4", {"video_codec": "h264"}))
        self.assertFalse(worker._should_generate_thumbnail(JobType.AUDIO_SEPARATION, "video/mp4", {"video_codec": "h264"}))

    def test_download_worker_generates_thumbnail_for_video_download(self) -> None:
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
        )
        artifact_path = self.container.settings.artifacts_dir / "sample.mp4"
        artifact_path.write_bytes(b"video")

        def write_thumbnail(command, **kwargs):
            Path(command[-1]).write_bytes(b"thumbnail")
            return subprocess.CompletedProcess(args=["ffmpeg"], returncode=0, stdout=b"", stderr=b"")

        with patch("app.workers.download_job_worker.subprocess.run", side_effect=write_thumbnail) as run_mock:
            thumbnail_path = worker._generate_video_thumbnail("job-1", artifact_path, 12.5)

        self.assertIsNotNone(thumbnail_path)
        self.assertEqual(thumbnail_path, (self.container.settings.artifacts_dir / "Thumbnails" / "job-1.thumbnail.jpg").resolve())
        run_mock.assert_called_once()
        command = run_mock.call_args.args[0]
        self.assertIn("-frames:v", command)
        self.assertEqual(command[-1], str(thumbnail_path))

    def test_download_worker_ignores_thumbnail_generation_failure(self) -> None:
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
        )
        artifact_path = self.container.settings.artifacts_dir / "sample.mp4"
        artifact_path.write_bytes(b"video")

        with patch("app.workers.download_job_worker.subprocess.run", side_effect=subprocess.CalledProcessError(1, ["ffmpeg"])):
            thumbnail_path = worker._generate_video_thumbnail("job-1", artifact_path, 12.5)

        self.assertIsNone(thumbnail_path)

    def test_download_worker_ignores_ffprobe_failure(self) -> None:
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
        )
        artifact_path = self.container.settings.artifacts_dir / "sample.mp4"
        artifact_path.write_bytes(b"video")

        with patch("app.workers.download_job_worker.subprocess_run", side_effect=subprocess.CalledProcessError(1, ["ffprobe"])):
            details = worker._probe_media_details(artifact_path)

        self.assertEqual(details, {})

    def test_audio_separation_delete_removes_uploaded_input(self) -> None:
        repo = DeviceRepository(self.container.database)
        device = repo.create(name="Owner", platform=Platform.MACOS, app_version="1.0", token_hash="input-delete-owner")
        job = self.container.job_service.create_audio_separation(device=device, file_name="song.wav", content=b"audio")
        input_path = Path(job.normalized_url.removeprefix("file:"))
        self.container.job_service._repository.update_status(job.id, status=JobStatus.FAILED, progress=100)

        self.container.job_service.delete(job.id, device)

        self.assertFalse(input_path.exists())

    def test_audio_separation_worker_keeps_uploaded_input_on_failure_for_retry(self) -> None:
        job = self.container.job_service.create_audio_separation(
            device=DeviceRepository(self.container.database).create(
                name="Owner",
                platform=Platform.MACOS,
                app_version="1.0",
                token_hash="owner-worker-input-cleanup",
            ),
            file_name="song.wav",
            content=b"audio",
        )
        input_path = Path(job.normalized_url.removeprefix("file:"))

        self.container.job_service._runner.worker.run(job.id)

        self.assertTrue(input_path.exists())

    def test_audio_separation_worker_fails_when_command_is_missing(self) -> None:
        job = self.container.job_service.create_audio_separation(
            device=DeviceRepository(self.container.database).create(
                name="Owner",
                platform=Platform.MACOS,
                app_version="1.0",
                token_hash="owner-worker",
            ),
            file_name="song.wav",
            content=b"audio",
        )

        self.container.job_service._runner.worker.run(job.id)
        stored = self.container.job_service._repository.get(job.id)
        artifacts = self.container.artifact_service._artifacts.list_for_job(job.id)

        self.assertEqual(stored.status, JobStatus.FAILED)
        self.assertEqual(stored.user_message, "未配置音频分离工具。")
        self.assertEqual(artifacts, [])

    def test_audio_separation_engine_quotes_placeholder_paths(self) -> None:
        from app.workers.audio_separation_job_worker import AudioSeparationEngine

        settings = self.container.settings
        settings.audio_separation_command = "python {input:q} {output_dir:q}"
        input_path = Path(self.temp_dir.name) / "input file's song.wav"
        output_dir = Path(self.temp_dir.name) / "output dir"

        args = AudioSeparationEngine(settings)._build_args(input_path=input_path, output_dir=output_dir)

        self.assertEqual(args, ["python", str(input_path), str(output_dir)])

    def test_audio_separation_engine_accepts_demucs_no_vocals_output(self) -> None:
        from app.workers.audio_separation_job_worker import AudioSeparationEngine

        output_dir = Path(self.temp_dir.name) / "demucs output"
        output_dir.mkdir()
        vocals = output_dir / "vocals.wav"
        no_vocals = output_dir / "no_vocals.wav"
        vocals.write_bytes(b"vocals")
        no_vocals.write_bytes(b"accompaniment")

        self.assertEqual(AudioSeparationEngine(self.container.settings)._find_output(output_dir, "accompaniment", "no_vocals"), no_vocals)

    def test_audio_separation_worker_creates_vocals_and_accompaniment_artifacts(self) -> None:
        script_path = Path(self.temp_dir.name) / "fake_separator.py"
        script_path.write_text(
            "from pathlib import Path\n"
            "import sys\n"
            "out = Path(sys.argv[2])\n"
            "out.mkdir(parents=True, exist_ok=True)\n"
            "(out / 'vocals.wav').write_bytes(b'vocals')\n"
            "(out / 'accompaniment.wav').write_bytes(b'accompaniment')\n",
            encoding="utf-8",
        )
        os.environ["XDL_AUDIO_SEPARATION_COMMAND"] = f"{sys.executable} {script_path} {{input}} {{output_dir}}"
        self.container.close()
        self.container = build_container()
        app.state.container = self.container
        self.client = TestClient(app)
        job = self.container.job_service.create_audio_separation(
            device=DeviceRepository(self.container.database).create(
                name="Owner",
                platform=Platform.MACOS,
                app_version="1.0",
                token_hash="owner-worker-success",
            ),
            file_name="song.wav",
            content=b"audio",
        )

        self.container.job_service._runner.worker.run(job.id)
        stored = self.container.job_service._repository.get(job.id)
        artifacts = self.container.artifact_service._artifacts.list_for_job(job.id)

        self.assertEqual(stored.status, JobStatus.COMPLETED)
        self.assertEqual([artifact.role for artifact in artifacts], [ArtifactRole.VOCALS, ArtifactRole.ACCOMPANIMENT])
        self.assertEqual([artifact.file_name for artifact in artifacts], ["song.vocals.wav", "song.accompaniment.wav"])
        self.assertEqual(Path(artifacts[0].storage_path).parent.relative_to(self.container.settings.artifacts_dir.resolve()), Path("Separated/Vocals"))
        self.assertEqual(Path(artifacts[1].storage_path).parent.relative_to(self.container.settings.artifacts_dir.resolve()), Path("Separated/Accompaniment"))
        self.assertFalse(Path(job.normalized_url.removeprefix("file:")).exists())

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

    def test_download_artifact_returns_image_content_type_and_filename(self) -> None:
        headers = self.auth_headers()
        job_response = self.client.post(
            "/api/v1/jobs",
            json={"url": "https://x.com/demo/status/12345"},
            headers=headers,
        )
        job_id = job_response.json()["data"]["id"]
        artifact_path = self.container.settings.artifacts_dir / "sample.jpg"
        artifact_path.write_bytes(b"image-bytes")
        artifact = self.container.artifact_service._artifacts.create(
            job_id=job_id,
            file_name="sample.jpg",
            mime_type="image/jpeg",
            storage_path=str(artifact_path),
            file_size=artifact_path.stat().st_size,
        )
        self.container.job_service._repository.update_status(
            job_id,
            status=JobStatus.COMPLETED,
            progress=100,
            artifact_id=artifact.id,
        )

        response = self.client.get(f"/api/v1/artifacts/{artifact.id}/download", headers=headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        self.assertIn("sample.jpg", response.headers["content-disposition"])
        self.assertEqual(response.content, b"image-bytes")

    def test_delegate_format_args_prefers_best_mp4_for_x_video(self) -> None:
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
        )

        expected = [
            "-f",
            "bestvideo*[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo*+bestaudio/best",
            "--merge-output-format",
            "mp4",
        ]

        self.assertEqual(worker._delegate_format_args(source_url="https://x.com/demo/status/12345", ext="mp4"), expected)
        self.assertEqual(worker._delegate_format_args(source_url="https://twitter.com/demo/status/12345", ext="mp4"), expected)

    def test_delegate_format_args_prefers_direct_mp4_when_speed_strategy_is_enabled(self) -> None:
        self.container.settings.ytdlp_format_strategy = "speed"
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
        )

        expected = [
            "-f",
            "best[ext=mp4]/bestvideo*[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format",
            "mp4",
        ]

        self.assertEqual(worker._delegate_format_args(source_url="https://www.youtube.com/watch?v=demo", ext="mp4"), expected)

    def test_delegate_format_args_can_keep_quality_first_split_streams(self) -> None:
        self.container.settings.ytdlp_format_strategy = "quality"
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
        )

        expected = [
            "-f",
            "bestvideo*[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo*+bestaudio/best",
            "--merge-output-format",
            "mp4",
        ]

        self.assertEqual(worker._delegate_format_args(source_url="https://www.bilibili.com/video/BV1sRoHB5EHC", ext="mp4"), expected)

    def test_delegate_format_args_adaptive_prefers_fast_mp4_for_delegate_video(self) -> None:
        self.container.settings.ytdlp_format_strategy = "adaptive"
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
        )

        expected = [
            "-f",
            "best[ext=mp4]/bestvideo*[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format",
            "mp4",
        ]

        self.assertEqual(worker._delegate_format_args(source_url="https://www.bilibili.com/video/BV1sRoHB5EHC", ext="mp4"), expected)

    def test_delegate_format_args_selected_quality_overrides_adaptive_speed(self) -> None:
        self.container.settings.ytdlp_format_strategy = "adaptive"
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
        )

        expected = [
            "-f",
            "bestvideo*[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo*+bestaudio/best",
            "--merge-output-format",
            "mp4",
        ]

        self.assertEqual(
            worker._delegate_format_args(
                source_url="https://www.bilibili.com/video/BV1sRoHB5EHC",
                ext="mp4",
                selected_quality="quality",
            ),
            expected,
        )

    def test_delegate_download_retries_timeout_errors(self) -> None:
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
        )
        calls = 0

        def fake_run_once(*, job_id: str, command: list[str], source_url: str) -> None:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise DownloadAppError("yt-dlp download timed out", "下载长时间没有进展，请稍后重试。")

        with patch.object(worker, "_raise_if_job_canceled", return_value=None):
            with patch.object(worker, "_run_delegate_download_once", side_effect=fake_run_once):
                worker._run_delegate_download_with_retries(
                    job_id="job-1",
                    command=["yt-dlp", "https://x.com/demo/status/12345"],
                    source_url="https://x.com/demo/status/12345",
                )

        self.assertEqual(calls, 3)

    def test_delegate_download_retries_keep_partial_files_for_resume(self) -> None:
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
        )
        part_path = self.container.settings.artifacts_dir / "job-1.mp4.part"
        part_path.write_bytes(b"partial")
        calls = 0

        def fake_run_once(*, job_id: str, command: list[str], source_url: str) -> None:
            nonlocal calls
            calls += 1
            self.assertTrue(part_path.exists())
            if calls == 1:
                raise DownloadAppError("yt-dlp download timed out", "下载长时间没有进展，请稍后重试。")

        with patch.object(worker, "_raise_if_job_canceled", return_value=None):
            with patch.object(worker, "_run_delegate_download_once", side_effect=fake_run_once):
                worker._run_delegate_download_with_retries(
                    job_id="job-1",
                    command=["yt-dlp", "https://x.com/demo/status/12345"],
                    source_url="https://x.com/demo/status/12345",
                )

        self.assertEqual(calls, 2)
        self.assertTrue(part_path.exists())

    def test_delegate_download_fails_after_timeout_retries_are_exhausted(self) -> None:
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
        )

        with patch.object(worker, "_raise_if_job_canceled", return_value=None):
            with patch.object(worker, "_run_delegate_download_once", side_effect=DownloadAppError("yt-dlp download timed out", "下载长时间没有进展，请稍后重试。")) as run_once:
                with self.assertRaises(DownloadAppError):
                    worker._run_delegate_download_with_retries(
                        job_id="job-1",
                        command=["yt-dlp", "https://x.com/demo/status/12345"],
                        source_url="https://x.com/demo/status/12345",
                    )

        self.assertEqual(run_once.call_count, 4)

    def test_worker_rejects_unsupported_delegate_output_extension(self) -> None:
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
        )
        output_path = self.container.settings.artifacts_dir / "job-1.html"
        output_path.write_text("<html></html>")

        with self.assertRaises(DownloadAppError) as context:
            worker._find_delegate_output("job-1")

        self.assertEqual(context.exception.message, "delegated download produced unsupported file type")
        self.assertFalse(output_path.exists())

    def test_extension_is_sanitized(self) -> None:
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
        )
        self.assertEqual(worker._normalize_extension(".mp4"), "mp4")
        self.assertEqual(worker._normalize_extension("mov"), "mov")
        self.assertEqual(worker._normalize_extension("webm"), "webm")
        self.assertEqual(worker._normalize_extension("m4a"), "m4a")
        self.assertEqual(worker._normalize_extension(".jpg"), "jpg")
        self.assertEqual(worker._normalize_extension("jpeg"), "jpeg")
        self.assertEqual(worker._normalize_extension("png"), "png")
        self.assertEqual(worker._normalize_extension("webp"), "webp")
        self.assertEqual(worker._normalize_extension("gif"), "gif")
        self.assertEqual(worker._normalize_extension("html"), "mp4")
        self.assertEqual(worker._normalize_extension("svg"), "mp4")
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
            headers=self.local_headers,
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
            headers=self.local_headers,
            json={
                "device_name": "First",
                "platform": "ios",
                "app_version": "0.1.0",
            },
        )
        second = self.client.post(
            "/api/v1/devices/register",
            headers=self.local_headers,
            json={
                "device_name": "Second",
                "platform": "ios",
                "app_version": "0.1.0",
            },
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)

    def test_cloud_mode_requires_bootstrap_code_configuration(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "XDL_CLOUD_MODE": "true",
                "XDL_DATA_DIR": self.temp_dir.name,
                "XDL_DATABASE_PATH": str(Path(self.temp_dir.name) / "app.db"),
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "XDL_BOOTSTRAP_CODE"):
                Settings.from_env()

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
                        validate_download_url("https://video.twimg.com/clip.mp4", source_url="https://x.com/demo/status/123")

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

    def test_resolve_download_url_allows_douyin_cdn_subdomains(self) -> None:
        fake_infos = [(None, None, None, None, ("8.8.8.8", 443))]
        with patch("app.services.url_tools.socket.getaddrinfo", return_value=fake_infos):
            resolved = resolve_download_url(
                "https://v99-default.douyinvod.com/obj/tos-cn-ve-15/demo.mp4",
                source_url="https://v.douyin.com/yjaQ3bMm4us/",
            )

        self.assertEqual(resolved.host, "v99-default.douyinvod.com")

    def test_resolve_download_url_rejects_douyin_cdn_lookalike_hosts(self) -> None:
        blocked_urls = [
            "https://evil-douyinvod.com/obj/demo.mp4",
            "https://douyinvod.com.evil.com/obj/demo.mp4",
            "https://v99-default.douyinvod.com.evil.com/obj/demo.mp4",
        ]
        for blocked_url in blocked_urls:
            with self.subTest(blocked_url=blocked_url):
                with self.assertRaises(ProviderAppError) as context:
                    resolve_download_url(blocked_url, source_url="https://v.douyin.com/yjaQ3bMm4us/")

                self.assertEqual(context.exception.message, "download URL host is not allowed")

    def test_resolve_download_url_rejects_douyin_cdn_without_douyin_source_context(self) -> None:
        for source_url in [None, "https://x.com/demo/status/123"]:
            with self.subTest(source_url=source_url):
                with self.assertRaises(ProviderAppError) as context:
                    resolve_download_url(
                        "https://v99-default.douyinvod.com/obj/tos-cn-ve-15/demo.mp4",
                        source_url=source_url,
                    )

                self.assertEqual(context.exception.message, "download URL host is not allowed")

    def test_resolve_download_url_rejects_douyin_cdn_when_explicit_hosts_do_not_include_suffix(self) -> None:
        with self.assertRaises(ProviderAppError) as context:
            resolve_download_url(
                "https://v99-default.douyinvod.com/obj/tos-cn-ve-15/demo.mp4",
                source_url="https://v.douyin.com/yjaQ3bMm4us/",
                allowed_hosts={"api-play-hl.amemv.com"},
            )

        self.assertEqual(context.exception.message, "download URL host is not allowed")

    def test_resolve_download_url_rejects_douyin_cdn_apex_host(self) -> None:
        with self.assertRaises(ProviderAppError) as context:
            resolve_download_url(
                "https://douyinvod.com/obj/tos-cn-ve-15/demo.mp4",
                source_url="https://v.douyin.com/yjaQ3bMm4us/",
            )

        self.assertEqual(context.exception.message, "download URL host is not allowed")

    def test_resolve_download_url_rejects_exact_download_hosts_without_context(self) -> None:
        with self.assertRaises(ProviderAppError) as context:
            resolve_download_url("https://video.twimg.com/media/clip.mp4")

        self.assertEqual(context.exception.message, "download URL host is not allowed")

    def test_resolve_download_url_uses_allowed_download_base_host_for_relative_url(self) -> None:
        fake_infos = [(None, None, None, None, ("8.8.8.8", 443))]
        with patch("app.services.url_tools.socket.getaddrinfo", return_value=fake_infos):
            resolved = resolve_download_url("/media/clip.mp4", base_url="https://video.twimg.com/status/123")

        self.assertEqual(resolved.url, "https://video.twimg.com/media/clip.mp4")
        self.assertEqual(resolved.host, "video.twimg.com")

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
        with self.assertRaises(ProviderAppError) as context:
            resolve_download_url("https://video.twimg.com:8443/media/clip.mp4", source_url="https://x.com/demo/status/123")

        self.assertEqual(context.exception.message, "download URL port is not allowed")

    def test_resolve_download_url_rejects_invalid_port(self) -> None:
        with self.assertRaises(ProviderAppError) as context:
            resolve_download_url("https://video.twimg.com:99999/media/clip.mp4", source_url="https://x.com/demo/status/123")

        self.assertEqual(context.exception.message, "download URL port is invalid")

    def test_resolve_download_url_rejects_control_characters(self) -> None:
        with self.assertRaises(ProviderAppError) as context:
            resolve_download_url("https://video.twimg.com/media/clip.mp4\r\nX-Test: bad", source_url="https://x.com/demo/status/123")

        self.assertEqual(context.exception.message, "download URL contains control characters")

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

    def test_worker_stores_direct_video_in_videos_directory(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-video-directory",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://x.com/demo/status/123",
            normalized_url="https://x.com/demo/status/123",
            selected_quality=None,
            job_type=JobType.DOWNLOAD,
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
            title="video title",
            author_handle=None,
            thumbnail_url=None,
            direct_url="https://video.twimg.com/media/clip.mp4",
            direct_url_addresses=("8.8.8.8",),
            webpage_url="https://x.com/demo/status/123",
            file_extension="mp4",
        )

        def fake_download(*, output_path: Path, **kwargs) -> None:
            output_path.write_bytes(b"video")

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch.object(worker._downloader, "download", side_effect=fake_download):
                worker.run(job.id)

        updated = self.container.job_service._repository.get(job.id)
        artifact = self.container.artifact_service._artifacts.get(updated.artifact_id)
        self.assertEqual(Path(artifact.storage_path).parent.name, "Videos")
        self.assertTrue(Path(artifact.storage_path).exists())

    def test_worker_stores_audio_download_in_audio_directory(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-audio-directory",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://x.com/demo/status/123",
            normalized_url="https://x.com/demo/status/123",
            selected_quality=None,
            job_type=JobType.AUDIO_DOWNLOAD,
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
            title="audio title",
            author_handle=None,
            thumbnail_url=None,
            direct_url="https://video.twimg.com/media/clip.mp4",
            direct_url_addresses=("8.8.8.8",),
            webpage_url="https://x.com/demo/status/123",
            file_extension="mp4",
        )

        def fake_download(*, output_path: Path, **kwargs) -> None:
            output_path.write_bytes(b"video")

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch.object(worker._downloader, "download", side_effect=fake_download):
                with patch("app.workers.download_job_worker.subprocess.run") as run_mock:
                    def fake_ffmpeg(command: list[str], **kwargs):
                        Path(command[-1]).write_bytes(b"mp3")
                        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")
                    run_mock.side_effect = fake_ffmpeg
                    worker.run(job.id)

        updated = self.container.job_service._repository.get(job.id)
        artifact = self.container.artifact_service._artifacts.get(updated.artifact_id)
        self.assertEqual(Path(artifact.storage_path).parent.name, "Audio")
        self.assertTrue(Path(artifact.storage_path).exists())

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

        ffprobe_output = b"""{
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "12.5", "bit_rate": "4500000"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
                {"codec_type": "audio", "codec_name": "aac"}
            ]
        }"""
        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch.object(worker._downloader, "download", side_effect=fake_download):
                with patch("app.workers.download_job_worker.subprocess_run") as run_mock:
                    run_mock.return_value = subprocess.CompletedProcess(args=["ffprobe"], returncode=0, stdout=ffprobe_output, stderr=b"")
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
        self.assertEqual(artifact.duration_seconds, 12.5)
        self.assertEqual(artifact.width, 1920)
        self.assertEqual(artifact.height, 1080)
        self.assertEqual(artifact.video_codec, "h264")
        self.assertEqual(artifact.audio_codec, "aac")
        self.assertEqual(artifact.bitrate_kbps, 4500)
        self.assertEqual(artifact.container_format, "mov,mp4,m4a,3gp,3g2,mj2")

    def test_worker_direct_download_preserves_image_extension_and_mime_type(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-image",
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
            title="image title",
            author_handle="author",
            thumbnail_url=None,
            direct_url="https://pbs.twimg.com/media/demo.jpg?format=jpg&name=large",
            direct_url_addresses=("8.8.8.8",),
            webpage_url="https://x.com/demo/status/123",
            file_extension="jpg",
        )

        def fake_download(*, output_path: Path, **kwargs) -> None:
            output_path.write_bytes(b"image-bytes")

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch.object(worker._downloader, "download", side_effect=fake_download):
                worker.run(job.id)

        updated = self.container.job_service._repository.get(job.id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, JobStatus.COMPLETED)
        self.assertIsNotNone(updated.artifact_id)
        artifact = self.container.artifact_service._artifacts.get(updated.artifact_id)
        self.assertIsNotNone(artifact)
        self.assertEqual(artifact.file_name, "image title.jpg")
        self.assertEqual(Path(artifact.storage_path).name, "image title.jpg")
        self.assertEqual(artifact.mime_type, "image/jpeg")

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

    def test_audio_download_worker_rejects_image_candidate(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-audio-image",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://x.com/demo/status/123",
            normalized_url="https://x.com/demo/status/123",
            selected_quality=None,
            job_type=JobType.AUDIO_DOWNLOAD,
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
            title="image title",
            author_handle="author",
            thumbnail_url=None,
            direct_url="https://pbs.twimg.com/media/demo.jpg",
            direct_url_addresses=("8.8.8.8",),
            webpage_url="https://x.com/demo/status/123",
            file_extension="jpg",
        )

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch("app.workers.download_job_worker.subprocess.Popen") as popen_mock:
                worker.run(job.id)

        popen_mock.assert_not_called()
        updated = self.container.job_service._repository.get(job.id)
        self.assertEqual(updated.status, JobStatus.FAILED)
        self.assertEqual(updated.user_message, "该链接没有可提取的音频。")

    def test_audio_download_worker_extracts_mp3_with_ytdlp(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-audio-download",
        )
        source_url = "https://www.youtube.com/watch?v=GEFehFHg_os"
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url=source_url,
            normalized_url=source_url,
            selected_quality=None,
            job_type=JobType.AUDIO_DOWNLOAD,
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
            title="music title",
            author_handle="author",
            thumbnail_url=None,
            direct_url=None,
            webpage_url=source_url,
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )
        artifacts_dir = self.container.settings.artifacts_dir
        captured_commands: list[list[str]] = []

        class FakeStdout:
            def __iter__(self):
                return iter(())

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self, command: list[str]) -> None:
                self.stdout = FakeStdout()
                self.stderr = None
                captured_commands.append(command)

            def wait(self, timeout: int) -> int:
                output_path = artifacts_dir / f"{job.id}.mp3"
                output_path.write_bytes(b"mp3")
                return 0

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch("app.workers.download_job_worker.subprocess.Popen", side_effect=lambda command, **kwargs: FakeProcess(command)):
                worker.run(job.id)

        self.assertEqual(captured_commands[0].count("-x"), 1)
        self.assertIn("bestaudio/best", captured_commands[0])
        self.assertIn("--audio-format", captured_commands[0])
        self.assertIn("mp3", captured_commands[0])
        updated = self.container.job_service._repository.get(job.id)
        self.assertEqual(updated.status, JobStatus.COMPLETED)
        artifact = self.container.artifact_service._artifacts.get(updated.artifact_id)
        self.assertEqual(artifact.file_name, "music title.mp3")
        self.assertEqual(artifact.mime_type, "audio/mpeg")
        self.assertEqual(artifact.role, ArtifactRole.MEDIA)

    def test_audio_download_worker_extracts_mp3_from_direct_video(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-audio-direct-video",
        )
        source_url = "https://x.com/i/status/1234567890"
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url=source_url,
            normalized_url=source_url,
            selected_quality=None,
            job_type=JobType.AUDIO_DOWNLOAD,
        )
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )
        extracted = ExtractedMedia(
            provider="direct",
            title="direct video",
            author_handle="author",
            thumbnail_url=None,
            direct_url="https://cdn.example.com/video.mp4",
            direct_url_addresses=("93.184.216.34",),
            webpage_url=source_url,
            file_extension="mp4",
        )

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch.object(worker._downloader, "download", side_effect=lambda **kwargs: kwargs["output_path"].write_bytes(b"video")):
                with patch("app.workers.download_job_worker.subprocess.run") as run_mock:
                    run_mock.side_effect = lambda command, **kwargs: Path(command[-1]).write_bytes(b"mp3")
                    worker.run(job.id)

        command = run_mock.call_args.args[0]
        self.assertEqual(command[command.index("-threads") + 1], str(self.container.settings.ffmpeg_threads))
        ffmpeg_input = Path(command[command.index("-i") + 1])
        self.assertEqual(ffmpeg_input.name, "direct video.mp4")
        self.assertEqual(ffmpeg_input.parent.name, "Videos")
        self.assertIn("-vn", command)
        self.assertIn("libmp3lame", command)
        updated = self.container.job_service._repository.get(job.id)
        self.assertEqual(updated.status, JobStatus.COMPLETED)
        artifact = self.container.artifact_service._artifacts.get(updated.artifact_id)
        self.assertEqual(artifact.file_name, "direct video.mp3")
        self.assertEqual(artifact.mime_type, "audio/mpeg")
        self.assertFalse((self.container.settings.artifacts_dir / f"{job.id}.mp4").exists())

    def test_audio_download_worker_extracts_mp3_from_direct_video_without_title(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-audio-direct-video-no-title",
        )
        source_url = "https://x.com/i/status/1234567890"
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url=source_url,
            normalized_url=source_url,
            selected_quality=None,
            job_type=JobType.AUDIO_DOWNLOAD,
        )
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )
        extracted = ExtractedMedia(
            provider="direct",
            title=None,
            author_handle="author",
            thumbnail_url=None,
            direct_url="https://cdn.example.com/video.mp4",
            direct_url_addresses=("93.184.216.34",),
            webpage_url=source_url,
            file_extension="mp4",
        )

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch.object(worker._downloader, "download", side_effect=lambda **kwargs: kwargs["output_path"].write_bytes(b"video")):
                with patch("app.workers.download_job_worker.subprocess.run") as run_mock:
                    run_mock.side_effect = lambda command, **kwargs: Path(command[-1]).write_bytes(b"mp3")
                    worker.run(job.id)

        command = run_mock.call_args.args[0]
        self.assertTrue(command[-1].endswith(".audio.mp3"))
        updated = self.container.job_service._repository.get(job.id)
        self.assertEqual(updated.status, JobStatus.COMPLETED)
        artifact = self.container.artifact_service._artifacts.get(updated.artifact_id)
        self.assertEqual(artifact.file_name, f"{job.id}.mp3")

    def test_audio_download_worker_rejects_oversized_extracted_mp3(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-audio-direct-video-oversize",
        )
        source_url = "https://x.com/i/status/1234567890"
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url=source_url,
            normalized_url=source_url,
            selected_quality=None,
            job_type=JobType.AUDIO_DOWNLOAD,
        )
        self.container.settings.download_max_bytes = 2
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )
        extracted = ExtractedMedia(
            provider="direct",
            title="oversized audio",
            author_handle="author",
            thumbnail_url=None,
            direct_url="https://cdn.example.com/video.mp4",
            direct_url_addresses=("93.184.216.34",),
            webpage_url=source_url,
            file_extension="mp4",
        )

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch.object(worker._downloader, "download", side_effect=lambda **kwargs: kwargs["output_path"].write_bytes(b"video")):
                with patch("app.workers.download_job_worker.subprocess.run") as run_mock:
                    run_mock.side_effect = lambda command, **kwargs: Path(command[-1]).write_bytes(b"mp3")
                    worker.run(job.id)

        updated = self.container.job_service._repository.get(job.id)
        self.assertEqual(updated.status, JobStatus.FAILED)
        self.assertEqual(updated.error_message, "extracted mp3 exceeds size limit")
        self.assertFalse(any(self.container.settings.artifacts_dir.glob(f"{job.id}*")))

    def test_audio_download_worker_cleans_partial_mp3_when_extraction_fails(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-audio-direct-video-partial",
        )
        source_url = "https://x.com/i/status/1234567890"
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url=source_url,
            normalized_url=source_url,
            selected_quality=None,
            job_type=JobType.AUDIO_DOWNLOAD,
        )
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )
        extracted = ExtractedMedia(
            provider="direct",
            title="partial audio",
            author_handle="author",
            thumbnail_url=None,
            direct_url="https://cdn.example.com/video.mp4",
            direct_url_addresses=("93.184.216.34",),
            webpage_url=source_url,
            file_extension="mp4",
        )

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch.object(worker._downloader, "download", side_effect=lambda **kwargs: kwargs["output_path"].write_bytes(b"video")):
                with patch("app.workers.download_job_worker.subprocess.run") as run_mock:
                    def fail_after_partial_output(command, **kwargs):
                        Path(command[-1]).write_bytes(b"partial")
                        raise subprocess.CalledProcessError(returncode=1, cmd=command)

                    run_mock.side_effect = fail_after_partial_output
                    worker.run(job.id)

        updated = self.container.job_service._repository.get(job.id)
        self.assertEqual(updated.status, JobStatus.FAILED)
        self.assertFalse(any(self.container.settings.artifacts_dir.glob(f"{job.id}*")))

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

    def test_worker_retries_delegate_temporary_error_three_times_before_success(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-delegate-temporary-retry-success",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://x.com/demo/status/12345",
            normalized_url="https://x.com/demo/status/12345",
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
            webpage_url="https://x.com/demo/status/12345",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )
        artifacts_dir = self.container.settings.artifacts_dir
        attempts: list[int] = []

        class FakeStdout:
            def __init__(self, lines: list[str]) -> None:
                self._lines = lines

            def __iter__(self):
                return iter(self._lines)

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                if attempts:
                    self_case.assertFalse((artifacts_dir / f"{job.id}.f137.mp4").exists())
                attempts.append(len(attempts) + 1)
                self.stdout = FakeStdout(["ERROR: HTTP Error 503: Service Unavailable\n"] if len(attempts) < 4 else [])
                self.stderr = None

            def wait(self, timeout: int) -> int:
                if len(attempts) < 4:
                    (artifacts_dir / f"{job.id}.part").write_bytes(b"partial")
                    (artifacts_dir / f"{job.id}.f137.mp4").write_bytes(b"partial-video")
                    return 1
                (artifacts_dir / f"{job.id}.mp4").write_bytes(b"video")
                return 0

        self_case = self
        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch.object(worker, "_probe_media_details", return_value={}):
                with patch("app.workers.download_job_worker.subprocess.Popen", side_effect=lambda command, **kwargs: FakeProcess()):
                    worker.run(job.id)

        updated = self.container.job_service._repository.get(job.id)
        self.assertEqual(len(attempts), 4)
        self.assertEqual(updated.status, JobStatus.COMPLETED)
        self.assertFalse((artifacts_dir / f"{job.id}.part").exists())
        self.assertFalse((artifacts_dir / f"{job.id}.f137.mp4").exists())

    def test_worker_marks_failed_after_delegate_temporary_error_retries_exhausted(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-delegate-temporary-retry-failed",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://x.com/demo/status/12345",
            normalized_url="https://x.com/demo/status/12345",
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
            webpage_url="https://x.com/demo/status/12345",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )
        artifacts_dir = self.container.settings.artifacts_dir
        attempts: list[int] = []

        class FakeStdout:
            def __iter__(self):
                yield "ERROR: HTTP Error 503: Service Unavailable\n"

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                attempts.append(len(attempts) + 1)
                self.stdout = FakeStdout()
                self.stderr = None

            def wait(self, timeout: int) -> int:
                (artifacts_dir / f"{job.id}.part").write_bytes(b"partial")
                return 1

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch("app.workers.download_job_worker.subprocess.Popen", side_effect=lambda command, **kwargs: FakeProcess()):
                worker.run(job.id)

        updated = self.container.job_service._repository.get(job.id)
        self.assertEqual(len(attempts), 4)
        self.assertEqual(updated.status, JobStatus.FAILED)
        self.assertEqual(updated.error_code, "download_error")
        self.assertEqual(updated.error_message, "HTTP Error 503: Service Unavailable")
        self.assertFalse((artifacts_dir / f"{job.id}.part").exists())

    def test_worker_does_not_retry_delegate_temporary_error_after_cancellation(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-delegate-temporary-canceled-no-retry",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://x.com/demo/status/12345",
            normalized_url="https://x.com/demo/status/12345",
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
            webpage_url="https://x.com/demo/status/12345",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )
        attempts: list[int] = []

        class FakeStdout:
            def __iter__(self):
                yield "ERROR: HTTP Error 503: Service Unavailable\n"

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                attempts.append(len(attempts) + 1)
                self.stdout = FakeStdout()
                self.stderr = None

            def wait(self, timeout: int | None = None) -> int:
                self_repo.update_status(job.id, status=JobStatus.CANCELED, progress=45)
                return 1

        self_repo = self.container.job_service._repository
        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch("app.workers.download_job_worker.subprocess.Popen", side_effect=lambda command, **kwargs: FakeProcess()):
                worker.run(job.id)

        updated = self.container.job_service._repository.get(job.id)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(updated.status, JobStatus.CANCELED)

    def test_worker_does_not_retry_delegate_download_after_cancellation(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-delegate-canceled-no-retry",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://x.com/demo/status/12345",
            normalized_url="https://x.com/demo/status/12345",
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
            webpage_url="https://x.com/demo/status/12345",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )
        artifacts_dir = self.container.settings.artifacts_dir
        attempts: list[int] = []

        class FakeStdout:
            def __iter__(self):
                return iter(())

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                attempts.append(len(attempts) + 1)
                self.pid = 4321
                self.stdout = FakeStdout()
                self.stderr = None
                self._terminated = False

            def wait(self, timeout: int | None = None) -> int:
                if timeout is None:
                    self._terminated = True
                    return 0
                self_repo.update_status(job.id, status=JobStatus.CANCELED, progress=45)
                (artifacts_dir / f"{job.id}.part").write_bytes(b"partial")
                raise subprocess.TimeoutExpired(cmd="yt-dlp", timeout=timeout or 0)

        self_repo = self.container.job_service._repository
        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch("app.workers.download_job_worker.subprocess.Popen", side_effect=lambda command, **kwargs: FakeProcess()):
                with patch("app.workers.download_job_worker.os.killpg") as killpg_mock:
                    worker.run(job.id)

        updated = self.container.job_service._repository.get(job.id)
        self.assertEqual(len(attempts), 1)
        killpg_mock.assert_called_once_with(4321, signal.SIGKILL)
        self.assertEqual(updated.status, JobStatus.CANCELED)
        self.assertFalse((artifacts_dir / f"{job.id}.part").exists())

    def test_worker_fails_when_delegate_success_produces_no_artifact(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-delegate-no-artifact",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://x.com/demo/status/12345",
            normalized_url="https://x.com/demo/status/12345",
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
            webpage_url="https://x.com/demo/status/12345",
            file_extension="mp4",
            delivery_mode=DeliveryMode.DELEGATE_YTDLP,
        )
        attempts: list[int] = []

        class FakeStdout:
            def __iter__(self):
                return iter(())

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                attempts.append(len(attempts) + 1)
                self.stdout = FakeStdout()
                self.stderr = None

            def wait(self, timeout: int | None = None) -> int:
                return 0

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch("app.workers.download_job_worker.subprocess.Popen", side_effect=lambda command, **kwargs: FakeProcess()):
                worker.run(job.id)

        updated = self.container.job_service._repository.get(job.id)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(updated.status, JobStatus.FAILED)
        self.assertEqual(updated.error_message, "yt-dlp delegated download produced no artifact")

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

    def test_worker_retries_delegate_login_error_with_chrome_cookie(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-delegate-login-retry",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            normalized_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            selected_quality=None,
        )
        self.container.settings.youtube_cookies_from_browser = "chrome"
        self.container.settings.youtube_cookies_disabled = False
        self.container.settings.youtube_remote_components = "ejs:github"
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
        commands: list[list[str]] = []

        class FakeStdout:
            def __init__(self, lines: list[str]) -> None:
                self._lines = lines

            def __iter__(self):
                return iter(self._lines)

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self, command: list[str]) -> None:
                commands.append(command)
                self.stdout = FakeStdout(["ERROR: [youtube] Sign in to confirm you're not a bot\n"] if len(commands) == 1 else [])
                self.stderr = None

            def wait(self, timeout: int) -> int:
                if len(commands) == 1:
                    return 1
                (artifacts_dir / f"{job.id}.mp4").write_bytes(b"video")
                return 0

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch.object(worker, "_probe_media_details", return_value={}):
                with patch("app.workers.download_job_worker.subprocess.Popen", side_effect=lambda command, **kwargs: FakeProcess(command)):
                    worker.run(job.id)

        self.assertEqual(len(commands), 2)
        self.assertNotIn("--cookies-from-browser", commands[0])
        self.assertIn("--cookies-from-browser", commands[1])
        self.assertIn("chrome", commands[1])
        self.assertNotIn("--remote-components", commands[1])
        updated = self.container.job_service._repository.get(job.id)
        self.assertEqual(updated.status, JobStatus.COMPLETED)
        artifact = self.container.artifact_service._artifacts.get(updated.artifact_id)
        self.assertEqual(artifact.file_name, "title.mp4")

    def test_worker_does_not_retry_delegate_login_error_without_cookie_source(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-delegate-login-disabled",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            normalized_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            selected_quality=None,
        )
        self.container.settings.youtube_cookies_from_browser = None
        self.container.settings.youtube_cookies_disabled = False
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
        commands: list[list[str]] = []

        class FakeStdout:
            def __iter__(self):
                yield "ERROR: [youtube] Sign in to confirm you're not a bot\n"

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self, command: list[str]) -> None:
                commands.append(command)
                self.stdout = FakeStdout()
                self.stderr = None

            def wait(self, timeout: int) -> int:
                return 1

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch("app.workers.download_job_worker.subprocess.Popen", side_effect=lambda command, **kwargs: FakeProcess(command)):
                worker.run(job.id)

        self.assertEqual(len(commands), 1)
        self.assertNotIn("--cookies-from-browser", commands[0])
        updated = self.container.job_service._repository.get(job.id)
        self.assertEqual(updated.status, JobStatus.FAILED)
        self.assertEqual(updated.error_message, "yt-dlp login verification required")
        self.assertEqual(updated.user_message, "该平台需要登录验证。请在 Mac 端上传已登录平台的 Cookie 后重试。")

    def test_worker_maps_delegate_ytdlp_login_error_to_user_message(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-delegate-login-error",
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
                yield "ERROR: [youtube] Sign in to confirm you're not a bot\n"

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
        self.assertEqual(updated.status, JobStatus.FAILED)
        self.assertEqual(updated.error_message, "yt-dlp login verification required")
        self.assertEqual(updated.user_message, "该平台需要登录验证。已自动尝试使用已配置的登录 Cookie，请确认 Cookie 有效后重试。")

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
            with patch.object(worker, "_probe_media_details", return_value={}):
                with patch("app.workers.download_job_worker.subprocess.Popen", return_value=FakeProcess()) as popen_mock:
                    worker.run(job.id)

        command = popen_mock.call_args.args[0]
        self.assertIn("--ignore-config", command)
        self.assertIn("--max-filesize", command)
        self.assertIn("--newline", command)
        self.assertIn(str(self.container.settings.download_max_bytes), command)
        self.assertEqual(command[-1], "https://www.bilibili.com/video/BV1sRoHB5EHC?p=2")

    def test_worker_delegate_download_uses_resume_and_fragment_retry_flags(self) -> None:
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )

        command = worker._delegate_download_command(
            source_url="https://www.bilibili.com/video/BV1sRoHB5EHC?p=2",
            output_template=self.container.settings.artifacts_dir / "job-1.%(ext)s",
            ffmpeg_location="/opt/homebrew/bin",
            ext="mp4",
            audio_only=False,
        )

        self.assertIn("--continue", command)
        self.assertIn("--part", command)
        self.assertEqual(command[command.index("--retries") + 1], "3")
        self.assertEqual(command[command.index("--fragment-retries") + 1], "5")
        self.assertEqual(command[command.index("--concurrent-fragments") + 1], str(self.container.settings.ytdlp_concurrent_fragments))

    def test_worker_delegate_download_adds_rate_limit_and_external_downloader_flags(self) -> None:
        self.container.settings.download_rate_limit = "5M"
        self.container.settings.ytdlp_external_downloader = "aria2c"
        self.container.settings.ytdlp_external_downloader_args = "aria2c:-x 8 -s 8 -k 1M"
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )

        command = worker._delegate_download_command(
            source_url="https://www.bilibili.com/video/BV1sRoHB5EHC?p=2",
            output_template=self.container.settings.artifacts_dir / "job-1.%(ext)s",
            ffmpeg_location="/opt/homebrew/bin",
            ext="mp4",
            audio_only=False,
        )

        self.assertEqual(command[command.index("--limit-rate") + 1], "5M")
        self.assertEqual(command[command.index("--downloader") + 1], "aria2c")
        self.assertEqual(command[command.index("--downloader-args") + 1], "aria2c:-x 8 -s 8 -k 1M")

    def test_worker_delegate_download_auto_external_downloader_uses_aria2c_when_available(self) -> None:
        self.container.settings.ytdlp_external_downloader = "auto"
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )

        with patch("app.workers.download_job_worker.shutil.which", return_value="/opt/homebrew/bin/aria2c"):
            command = worker._delegate_download_command(
                source_url="https://www.bilibili.com/video/BV1sRoHB5EHC?p=2",
                output_template=self.container.settings.artifacts_dir / "job-1.%(ext)s",
                ffmpeg_location="/opt/homebrew/bin",
                ext="mp4",
                audio_only=False,
            )

        self.assertEqual(command[command.index("--downloader") + 1], "aria2c")
        self.assertEqual(command[command.index("--downloader-args") + 1], "aria2c:-x 8 -s 8 -k 1M")

    def test_worker_delegate_download_skips_auto_external_downloader_when_aria2c_is_missing(self) -> None:
        self.container.settings.ytdlp_external_downloader = "auto"
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )

        with patch("app.workers.download_job_worker.shutil.which", return_value=None):
            command = worker._delegate_download_command(
                source_url="https://www.bilibili.com/video/BV1sRoHB5EHC?p=2",
                output_template=self.container.settings.artifacts_dir / "job-1.%(ext)s",
                ffmpeg_location="/opt/homebrew/bin",
                ext="mp4",
                audio_only=False,
            )

        self.assertNotIn("--downloader", command)
        self.assertNotIn("--downloader-args", command)

    def test_worker_delegate_download_adds_ffmpeg_postprocessor_thread_flags(self) -> None:
        self.container.settings.ffmpeg_threads = 0
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )

        command = worker._delegate_download_command(
            source_url="https://www.bilibili.com/video/BV1sRoHB5EHC?p=2",
            output_template=self.container.settings.artifacts_dir / "job-1.%(ext)s",
            ffmpeg_location="/opt/homebrew/bin",
            ext="mp4",
            audio_only=False,
        )

        self.assertIn("--postprocessor-args", command)
        self.assertIn("Merger+ffmpeg:-threads 0", command)

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
            with patch.object(worker, "_probe_media_details", return_value={}):
                with patch("app.workers.download_job_worker.subprocess.Popen", return_value=FakeProcess()) as popen_mock:
                    worker.run(job.id)

        command = popen_mock.call_args.args[0]
        self.assertEqual(command[0], "yt-dlp")
        self.assertNotIn("--cookies-from-browser", command)
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
        self.container.settings.youtube_cookies_disabled = False
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
            with patch("app.workers.download_job_worker.subprocess.Popen", side_effect=lambda command, **kwargs: FakeProcess()):
                with patch("app.workers.download_job_worker.os.killpg") as killpg_mock:
                    worker.run(job.id)

        self.assertEqual(killpg_mock.call_count, 4)
        killpg_mock.assert_called_with(4321, signal.SIGKILL)
        updated = self.container.job_service._repository.get(job.id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, JobStatus.FAILED)
        self.assertEqual(updated.error_code, "download_error")
        self.assertEqual(updated.error_message, "yt-dlp download timed out")
        self.assertEqual(updated.progress, 45)
        self.assertFalse((artifacts_dir / f"{job.id}.part").exists())

    def test_worker_delegate_download_keeps_running_while_ytdlp_reports_progress(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-delegate-long-running-progress",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            normalized_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            selected_quality=None,
        )
        self.container.settings.provider_timeout_seconds = 1
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
        wait_timeouts: list[int | None] = []
        progress_read = threading.Event()

        class FakeStdout:
            def __iter__(self):
                yield "[download]   25.0% of 4.00GiB at 2.00MiB/s ETA 33:00\n"
                progress_read.set()

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                self.stdout = FakeStdout()
                self.stderr = None

            def wait(self, timeout: int | None = None) -> int:
                wait_timeouts.append(timeout)
                if len(wait_timeouts) == 1:
                    self_case.assertTrue(progress_read.wait(timeout=1))
                    raise subprocess.TimeoutExpired(cmd="yt-dlp", timeout=timeout or 0)
                output_path = artifacts_dir / f"{job.id}.mp4"
                output_path.write_bytes(b"video")
                return 0

        self_case = self
        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch("app.workers.download_job_worker.subprocess.Popen", return_value=FakeProcess()):
                worker.run(job.id)

        updated = self.container.job_service._repository.get(job.id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, JobStatus.COMPLETED)
        self.assertEqual(wait_timeouts, [1, 1])

    def test_worker_delegate_download_times_out_when_output_has_no_progress(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-delegate-output-no-progress",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            normalized_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            selected_quality=None,
        )
        self.container.settings.provider_timeout_seconds = 1
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
        output_read = threading.Event()

        class FakeStdout:
            def __iter__(self):
                yield "[youtube] Retrying after HTTP error\n"
                output_read.set()

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                self.pid = 4321
                self.stdout = FakeStdout()
                self.stderr = None

            def wait(self, timeout: int | None = None) -> int:
                if timeout is None:
                    return 0
                self_case.assertTrue(output_read.wait(timeout=1))
                raise subprocess.TimeoutExpired(cmd="yt-dlp", timeout=timeout)

        self_case = self
        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch("app.workers.download_job_worker.subprocess.Popen", side_effect=lambda command, **kwargs: FakeProcess()):
                with patch("app.workers.download_job_worker.os.killpg") as killpg_mock:
                    worker.run(job.id)

        self.assertEqual(killpg_mock.call_count, 4)
        killpg_mock.assert_called_with(4321, signal.SIGKILL)
        updated = self.container.job_service._repository.get(job.id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, JobStatus.FAILED)
        self.assertEqual(updated.progress, 45)

    def test_worker_delegate_download_times_out_after_progress_stalls(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-delegate-stalled-progress",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            normalized_url="https://www.youtube.com/watch?v=GEFehFHg_os",
            selected_quality=None,
        )
        self.container.settings.provider_timeout_seconds = 1
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
                yield "[download]   25.0% of 4.00GiB at 2.00MiB/s ETA 33:00\n"

            def close(self) -> None:
                return None

        class FakeProcess:
            def __init__(self) -> None:
                self.pid = 4321
                self.stdout = FakeStdout()
                self.stderr = None

            def wait(self, timeout: int | None = None) -> int:
                if timeout is None:
                    return 0
                raise subprocess.TimeoutExpired(cmd="yt-dlp", timeout=timeout)

        with patch.object(worker._selector, "extract", return_value=extracted):
            with patch("app.workers.download_job_worker.subprocess.Popen", side_effect=lambda command, **kwargs: FakeProcess()):
                with patch("app.workers.download_job_worker.time.monotonic", side_effect=[1, 3, 3] * 4):
                    with patch("app.workers.download_job_worker.os.killpg") as killpg_mock:
                        worker.run(job.id)

        self.assertEqual(killpg_mock.call_count, 4)
        killpg_mock.assert_called_with(4321, signal.SIGKILL)
        updated = self.container.job_service._repository.get(job.id)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.status, JobStatus.FAILED)
        self.assertEqual(updated.progress, 56)

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

        self.assertEqual(parsed, (1572864, None, 512000, 3, 56))

    def test_worker_estimates_unknown_total_progress_from_downloaded_bytes(self) -> None:
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )

        progress_values = [
            worker._progress_from_bytes(0, None),
            worker._progress_from_bytes(512 * 1024, None),
            worker._progress_from_bytes(2 * 1024 * 1024, None),
            worker._progress_from_bytes(32 * 1024 * 1024, None),
            worker._progress_from_bytes(512 * 1024 * 1024, None),
        ]

        self.assertEqual(progress_values[0], 45)
        self.assertEqual(progress_values, sorted(progress_values))
        self.assertGreater(progress_values[-1], 45)
        self.assertLessEqual(progress_values[-1], 89)

    def test_worker_download_progress_never_regresses(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-progress-device",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://x.com/demo/status/1",
            normalized_url="https://x.com/demo/status/1",
            selected_quality=None,
        )
        self.container.job_service._repository.update_status(job.id, status=JobStatus.DOWNLOADING, progress=60)
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )

        worker._update_download_progress(
            job.id,
            downloaded_bytes=1024,
            total_bytes=None,
            speed_bytes_per_sec=None,
            eta_seconds=None,
            progress=50,
        )

        updated = self.container.job_service._repository.get(job.id)
        self.assertEqual(updated.progress, 60)

    def test_worker_download_metrics_never_regress(self) -> None:
        device = DeviceRepository(self.container.database).create(
            name="Worker Device",
            platform=Platform.IOS,
            app_version="1.0",
            token_hash="worker-progress-metrics-device",
        )
        job = self.container.job_service._repository.create(
            device_id=device.id,
            source_url="https://x.com/demo/status/1",
            normalized_url="https://x.com/demo/status/1",
            selected_quality=None,
        )
        self.container.job_service._repository.update_status(
            job.id,
            status=JobStatus.DOWNLOADING,
            progress=60,
            downloaded_bytes=4096,
            total_bytes=8192,
        )
        worker = DownloadJobWorker(
            settings=self.container.settings,
            jobs=self.container.job_service._repository,
            artifacts=self.container.artifact_service._artifacts,
            selector=self.container.job_service._runner.worker._selector,
            downloader=self.container.job_service._runner.worker._downloader,
        )

        worker._update_download_progress(
            job.id,
            downloaded_bytes=1024,
            total_bytes=None,
            speed_bytes_per_sec=None,
            eta_seconds=None,
            progress=50,
        )

        updated = self.container.job_service._repository.get(job.id)
        self.assertEqual(updated.progress, 60)
        self.assertEqual(updated.downloaded_bytes, 4096)
        self.assertEqual(updated.total_bytes, 8192)

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
            with patch.object(worker, "_probe_media_details", return_value={}):
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
            with patch.object(worker, "_probe_media_details", return_value={}):
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
