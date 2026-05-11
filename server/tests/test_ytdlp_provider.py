from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest
from urllib.parse import urlparse
from unittest.mock import patch

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app.core.config import Settings
from app.core.errors import ProviderAppError
from app.core.ytdlp_errors import is_ytdlp_login_required, ytdlp_user_message
from app.domain.models import DeliveryMode
from app.extractors.ytdlp_provider import YtDlpProvider


class YtDlpProviderTests(unittest.TestCase):
    def test_is_ytdlp_login_required_detects_only_login_errors(self) -> None:
        login_errors = [
            "[youtube] Sign in to confirm you're not a bot",
            "login required to confirm your age",
            "please use --cookies-from-browser",
            "This video may be inappropriate for some users",
        ]
        non_login_errors = [
            "requested format not available",
            "This video is private",
            "operation timed out",
            "merge failed",
        ]
        for error_text in login_errors:
            with self.subTest(error_text=error_text):
                self.assertTrue(is_ytdlp_login_required(error_text))
        for error_text in non_login_errors:
            with self.subTest(error_text=error_text):
                self.assertFalse(is_ytdlp_login_required(error_text))

    def test_ytdlp_user_message_maps_common_errors(self) -> None:
        cases = [
            ("[youtube] Sign in to confirm you're not a bot", "该平台需要登录验证。请在 Mac 端上传已登录平台的 Cookie 后重试。"),
            ("HTTP Error 429: Too Many Requests", "平台正在限流，已自动降速重试；仍失败时请稍后再试。"),
            ("requested format not available", "当前视频格式不可用，请稍后重试。"),
            ("This video is private", "该视频不可访问，可能已被删除、设为私密或需要权限。"),
            ("The uploader has not made this video available", "该视频不可访问，可能已被删除、设为私密或需要权限。"),
            ("operation timed out", "网络超时，请稍后重试。"),
            ("merge failed", "下载视频失败，请稍后重试。"),
        ]
        for error_text, user_message in cases:
            with self.subTest(error_text=error_text):
                self.assertEqual(ytdlp_user_message(error_text), user_message)

    def setUp(self) -> None:
        self.provider = YtDlpProvider(Settings(provider_timeout_seconds=5))

    def test_can_handle_supported_public_urls(self) -> None:
        urls = [
            "https://x.com/demo/status/123",
            "https://x.com/i/status/123",
            "https://twitter.com/demo/status/123",
            "https://twitter.com/i/status/123",
            "https://www.douyin.com/video/123456",
            "https://v.douyin.com/abc123/",
            "https://m.douyin.com/share/video/123456/",
            "https://www.iesdouyin.com/share/video/123456/",
            "https://h5.pipix.com/s/abc123/",
            "https://www.pipix.com/item/123456",
            "https://www.xiaohongshu.com/explore/abcdef",
            "https://xhslink.com/abc123",
            "https://www.bilibili.com/video/BV1sRoHB5EHC/?spm_id_from=333.1007.tianma.1-2-2.click",
            "https://www.youtube.com/watch?v=GEFehFHg_os",
            "https://youtu.be/GEFehFHg_os",
            "https://www.youtube.com/shorts/GEFehFHg_os",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertTrue(self.provider.can_handle(url), msg=urlparse(url).hostname)

    def test_can_handle_rejects_unsupported_urls(self) -> None:
        urls = [
            "https://example.com/video/123",
            "https://x.com/home",
            "https://www.douyin.com/video/",
            "https://www.douyin.com/user/demo",
            "https://m.douyin.com/share/video/",
            "https://m.douyin.com/share/video/123456/extra",
            "https://m.douyin.com/share/video//123456",
            "https://m.douyin.com/share/video/%2F123456",
            "https://m.douyin.com/share/video/%252F123456",
            "https://m.douyin.com/share/video/%255C123456",
            "https://m.douyin.com/share/video/%2525252F123456",
            "https://m.douyin.com/share/video/%2525255C123456",
            "https://m.douyin.com/share/video/%252525252F123456",
            "https://m.douyin.com/share/video/%252525255C123456",
            "https://m.douyin.com/share/video/%ZZ",
            "https://m.douyin.com/share/video/abc%2",
            "https://m.douyin.com/user/demo",
            "https://h5.pipix.com/",
            "https://h5.pipix.com/s/",
            "https://h5.pipix.com/s/abc123/extra",
            "https://h5.pipix.com/s//abc123",
            "https://h5.pipix.com/s/%2Fabc123",
            "https://h5.pipix.com/s/%252Fabc123",
            "https://h5.pipix.com/s/%ZZ",
            "https://www.pipix.com/item/",
            "https://www.pipix.com/item/123456/extra",
            "https://www.pipix.com/item//123456",
            "https://www.pipix.com/item/%2F123456",
            "https://www.pipix.com/item/%255C123456",
            "https://www.pipix.com/item/abc%2",
            "https://www.pipix.com/user/demo",
            "https://www.xiaohongshu.com/user/profile/demo",
            "https://www.bilibili.com/bangumi/play/ep123",
            "https://youtu.be/shorts/GEFehFHg_os",
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertFalse(self.provider.can_handle(url), msg=urlparse(url).hostname)

    def _completed_process(self, payload: dict) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["yt-dlp"],
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

    def _extract(self, payload: dict, url: str = "https://x.com/demo/status/123"):
        fake_infos = [(None, None, None, None, ("8.8.8.8", 443))]
        with patch(
            "app.extractors.ytdlp_provider.subprocess.run",
            return_value=self._completed_process(payload),
        ):
            with patch("app.services.url_tools.socket.getaddrinfo", return_value=fake_infos):
                return self.provider.extract(url)

    def test_extract_delegates_x_video_when_requested_formats_are_available(self) -> None:
        extracted = self._extract(
            {
                "title": "demo",
                "webpage_url": "https://x.com/demo/status/123",
                "ext": "mp4",
                "requested_downloads": [
                    {
                        "requested_formats": [
                            {
                                "format_id": "hls-1586",
                                "url": "https://video.twimg.com/media/playlist.m3u8?tag=1",
                                "protocol": "m3u8_native",
                                "ext": "mp4",
                            },
                            {
                                "format_id": "http-2176",
                                "url": "https://video.twimg.com/media/clip.mp4?tag=1",
                                "protocol": "https",
                                "ext": "mp4",
                            },
                        ]
                    }
                ],
            }
        )

        self.assertEqual(extracted.delivery_mode, DeliveryMode.DELEGATE_YTDLP)
        self.assertIsNone(extracted.direct_url)
        self.assertEqual(extracted.file_extension, "mp4")

    def test_extract_delegates_x_video_when_only_hls_formats_exist(self) -> None:
        extracted = self._extract(
            {
                "title": "demo",
                "webpage_url": "https://x.com/demo/status/123",
                "ext": "mp4",
                "formats": [
                    {
                        "format_id": "hls-1586",
                        "url": "https://video.twimg.com/media/playlist.m3u8?tag=1",
                        "protocol": "m3u8_native",
                        "ext": "mp4",
                        "vcodec": "h264",
                    }
                ],
            }
        )

        self.assertEqual(extracted.delivery_mode, DeliveryMode.DELEGATE_YTDLP)
        self.assertIsNone(extracted.direct_url)
        self.assertEqual(extracted.file_extension, "mp4")

    def test_extract_delegates_x_video_even_when_low_quality_direct_mp4_exists(self) -> None:
        extracted = self._extract(
            {
                "title": "demo",
                "webpage_url": "https://x.com/demo/status/123",
                "url": "https://video.twimg.com/media/low-quality.mp4?tag=2",
                "protocol": "https",
                "ext": "mp4",
                "formats": [
                    {
                        "format_id": "hls-900",
                        "url": "https://video.twimg.com/media/stream.m3u8?tag=2",
                        "protocol": "m3u8_native",
                        "ext": "mp4",
                    },
                    {
                        "format_id": "http-1200",
                        "url": "https://video.twimg.com/media/clip.mp4?tag=2",
                        "protocol": "https",
                        "ext": "mp4",
                    },
                ],
            }
        )

        self.assertEqual(extracted.delivery_mode, DeliveryMode.DELEGATE_YTDLP)
        self.assertIsNone(extracted.direct_url)
        self.assertEqual(extracted.file_extension, "mp4")

    def test_extract_keeps_x_animated_gif_mp4_direct_when_url_marks_tweet_video(self) -> None:
        extracted = self._extract(
            {
                "title": "gif demo",
                "webpage_url": "https://x.com/demo/status/123",
                "formats": [
                    {
                        "format_id": "http-gif",
                        "url": "https://video.twimg.com/tweet_video/demo.mp4",
                        "protocol": "https",
                        "ext": "mp4",
                        "vcodec": "h264",
                        "acodec": "none",
                    }
                ],
            }
        )

        self.assertEqual(extracted.delivery_mode, DeliveryMode.DIRECT)
        self.assertEqual(extracted.direct_url, "https://video.twimg.com/tweet_video/demo.mp4")
        self.assertEqual(extracted.file_extension, "mp4")

    def test_extract_accepts_x_image_candidate(self) -> None:
        extracted = self._extract(
            {
                "title": "image demo",
                "webpage_url": "https://x.com/demo/status/123",
                "formats": [
                    {
                        "format_id": "image-jpg",
                        "url": "https://pbs.twimg.com/media/demo.jpg?format=jpg&name=large",
                        "protocol": "https",
                        "ext": "jpg",
                    }
                ],
            }
        )

        self.assertEqual(extracted.direct_url, "https://pbs.twimg.com/media/demo.jpg?format=jpg&name=large")
        self.assertEqual(extracted.file_extension, "jpg")
        self.assertEqual(extracted.direct_url_addresses, ("8.8.8.8",))

    def test_extract_accepts_xiaohongshu_webp_image_candidate(self) -> None:
        extracted = self._extract(
            {
                "title": "image demo",
                "webpage_url": "https://www.xiaohongshu.com/explore/abcdef?xsec_token=1",
                "formats": [
                    {
                        "format_id": "image-webp",
                        "url": "https://sns-img-hw.xhscdn.com/demo.webp?imageView2/2/w/1080",
                        "protocol": "https",
                        "ext": "webp",
                    }
                ],
            },
            url="https://www.xiaohongshu.com/explore/abcdef?xsec_token=1",
        )

        self.assertEqual(extracted.direct_url, "https://sns-img-hw.xhscdn.com/demo.webp?imageView2/2/w/1080")
        self.assertEqual(extracted.file_extension, "webp")
        self.assertEqual(extracted.direct_url_addresses, ("8.8.8.8",))

    def test_extract_accepts_gif_image_candidate(self) -> None:
        extracted = self._extract(
            {
                "title": "gif demo",
                "webpage_url": "https://x.com/demo/status/123",
                "formats": [
                    {
                        "format_id": "image-gif",
                        "url": "https://pbs.twimg.com/tweet_video/demo.gif",
                        "protocol": "https",
                        "ext": "gif",
                    }
                ],
            }
        )

        self.assertEqual(extracted.direct_url, "https://pbs.twimg.com/tweet_video/demo.gif")
        self.assertEqual(extracted.file_extension, "gif")

    def test_extract_rejects_unsupported_direct_file_candidate(self) -> None:
        with self.assertRaises(ProviderAppError) as context:
            self._extract(
                {
                    "title": "html demo",
                    "webpage_url": "https://x.com/demo/status/123",
                    "formats": [
                        {
                            "format_id": "html",
                            "url": "https://pbs.twimg.com/media/demo.html",
                            "protocol": "https",
                            "ext": "html",
                        }
                    ],
                }
            )

        self.assertEqual(context.exception.message, "no downloadable media found")

    def test_extract_skips_top_level_hls_url_and_falls_back_to_direct_format_for_douyin(self) -> None:
        extracted = self._extract(
            {
                "title": "demo",
                "webpage_url": "https://www.douyin.com/video/123456",
                "url": "https://api-play-hl.amemv.com/aweme/v1/play/stream.m3u8",
                "ext": "mp4",
                "formats": [
                    {
                        "format_id": "http-720",
                        "url": "https://api-play-hl.amemv.com/aweme/v1/play/?video_id=1",
                        "protocol": "https",
                        "ext": "mp4",
                    }
                ],
            },
            url="https://www.douyin.com/video/123456",
        )

        self.assertEqual(extracted.direct_url, "https://api-play-hl.amemv.com/aweme/v1/play/?video_id=1")
        self.assertEqual(extracted.file_extension, "mp4")

    def test_extract_delegates_pipixia_download(self) -> None:
        extracted = self._extract(
            {
                "title": "demo",
                "webpage_url": "https://h5.pipix.com/s/abc123/",
                "ext": "mp4",
            },
            url="https://h5.pipix.com/s/abc123/",
        )

        self.assertEqual(extracted.delivery_mode, DeliveryMode.DELEGATE_YTDLP)
        self.assertIsNone(extracted.direct_url)
        self.assertEqual(extracted.file_extension, "mp4")

    def test_extract_rejects_pipixia_when_webpage_url_leaves_platform(self) -> None:
        with self.assertRaisesRegex(ProviderAppError, "unexpected delegated webpage URL"):
            self._extract(
                {
                    "title": "demo",
                    "webpage_url": "https://example.com/video/1",
                    "ext": "mp4",
                },
                url="https://h5.pipix.com/s/abc123/",
            )

    def test_extract_retries_youtube_login_error_with_chrome_cookie(self) -> None:
        settings = Settings(
            provider_timeout_seconds=5,
            youtube_cookies_file=Path("/tmp/missing-xdl-youtube-cookies.txt"),
            youtube_cookies_from_browser="chrome",
            youtube_cookies_disabled=False,
            youtube_remote_components="ejs:github",
        )
        provider = YtDlpProvider(settings)
        payload = {"title": "demo", "webpage_url": "https://www.youtube.com/watch?v=GEFehFHg_os", "ext": "mp4"}

        with patch(
            "app.extractors.ytdlp_provider.subprocess.run",
            side_effect=[
                subprocess.CompletedProcess(args=["yt-dlp"], returncode=1, stdout="", stderr="ERROR: [youtube] Sign in to confirm you're not a bot"),
                subprocess.CompletedProcess(args=["yt-dlp"], returncode=0, stdout=json.dumps(payload), stderr=""),
            ],
        ) as run_mock:
            extracted = provider.extract("https://www.youtube.com/watch?v=GEFehFHg_os")

        self.assertEqual(extracted.delivery_mode, DeliveryMode.DELEGATE_YTDLP)
        self.assertEqual(run_mock.call_count, 2)
        first_command = run_mock.call_args_list[0].args[0]
        retry_command = run_mock.call_args_list[1].args[0]
        self.assertNotIn("--cookies-from-browser", first_command)
        self.assertIn("--cookies-from-browser", retry_command)
        self.assertIn("chrome", retry_command)
        self.assertEqual(retry_command.count("--cookies-from-browser"), 1)
        self.assertNotIn("--remote-components", retry_command)

    def test_extract_retries_youtube_login_error_with_uploaded_cookie_file(self) -> None:
        cookie_path = Path(self.provider._settings.database_path).parent / "youtube" / "cookies.txt"
        cookie_path.parent.mkdir(parents=True, exist_ok=True)
        cookie_path.write_text(".youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\ttest\n")
        settings = Settings(provider_timeout_seconds=5, youtube_cookies_file=cookie_path, youtube_cookies_from_browser="chrome", youtube_cookies_disabled=False)
        provider = YtDlpProvider(settings)
        payload = {"title": "demo", "webpage_url": "https://www.youtube.com/watch?v=GEFehFHg_os", "ext": "mp4"}

        with patch(
            "app.extractors.ytdlp_provider.subprocess.run",
            side_effect=[
                subprocess.CompletedProcess(args=["yt-dlp"], returncode=1, stdout="", stderr="ERROR: [youtube] Sign in to confirm you're not a bot"),
                subprocess.CompletedProcess(args=["yt-dlp"], returncode=0, stdout=json.dumps(payload), stderr=""),
            ],
        ) as run_mock:
            provider.extract("https://www.youtube.com/watch?v=GEFehFHg_os")

        retry_command = run_mock.call_args_list[1].args[0]
        self.assertIn("--cookies", retry_command)
        self.assertIn(str(cookie_path), retry_command)
        self.assertNotIn("--cookies-from-browser", retry_command)

    def test_extract_falls_back_to_browser_cookie_when_uploaded_cookie_file_is_missing(self) -> None:
        settings = Settings(provider_timeout_seconds=5, youtube_cookies_file=Path("/tmp/missing-xdl-cookies.txt"), youtube_cookies_from_browser="chrome", youtube_cookies_disabled=False)

        args = settings.youtube_cookie_retry_args("https://www.youtube.com/watch?v=GEFehFHg_os")

        self.assertEqual(args, ["--cookies-from-browser", "chrome"])

    def test_extract_does_not_use_cookie_before_login_error(self) -> None:
        settings = Settings(provider_timeout_seconds=5, youtube_cookies_from_browser="chrome", youtube_cookies_disabled=False)
        provider = YtDlpProvider(settings)
        payload = {"title": "demo", "webpage_url": "https://www.youtube.com/watch?v=GEFehFHg_os", "ext": "mp4"}
        with patch(
            "app.extractors.ytdlp_provider.subprocess.run",
            return_value=subprocess.CompletedProcess(args=["yt-dlp"], returncode=0, stdout=json.dumps(payload), stderr=""),
        ) as run_mock:
            provider.extract("https://www.youtube.com/watch?v=GEFehFHg_os")

        self.assertEqual(run_mock.call_count, 1)
        command = run_mock.call_args.args[0]
        self.assertNotIn("--cookies-from-browser", command)

    def test_extract_does_not_retry_login_error_when_cookies_are_disabled(self) -> None:
        settings = Settings(provider_timeout_seconds=5, youtube_cookies_disabled=True)
        provider = YtDlpProvider(settings)
        with patch(
            "app.extractors.ytdlp_provider.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["yt-dlp"],
                returncode=1,
                stdout="",
                stderr="ERROR: [youtube] Sign in to confirm you're not a bot",
            ),
        ) as run_mock:
            with self.assertRaises(ProviderAppError):
                provider.extract("https://www.youtube.com/watch?v=GEFehFHg_os")

        self.assertEqual(run_mock.call_count, 1)
        command = run_mock.call_args.args[0]
        self.assertNotIn("--cookies-from-browser", command)
        self.assertEqual(settings.youtube_cookie_retry_args("https://www.youtube.com/watch?v=GEFehFHg_os"), [])

    def test_extract_maps_ytdlp_login_error_to_user_message(self) -> None:
        with patch(
            "app.extractors.ytdlp_provider.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["yt-dlp"],
                returncode=1,
                stdout="",
                stderr="ERROR: [youtube] Sign in to confirm you're not a bot",
            ),
        ):
            with self.assertRaises(ProviderAppError) as context:
                self.provider.extract("https://www.youtube.com/watch?v=GEFehFHg_os")

        self.assertEqual(context.exception.message, "yt-dlp login verification required")
        self.assertEqual(context.exception.user_message, "该平台需要登录验证。已自动尝试使用已配置的登录 Cookie，请确认 Cookie 有效后重试。")

    def test_extract_without_cookie_retry_uses_plain_login_message(self) -> None:
        settings = Settings(provider_timeout_seconds=5, youtube_cookies_from_browser=None, youtube_cookies_disabled=True)
        provider = YtDlpProvider(settings)
        with patch(
            "app.extractors.ytdlp_provider.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["yt-dlp"],
                returncode=1,
                stdout="",
                stderr="ERROR: [youtube] Sign in to confirm you're not a bot",
            ),
        ):
            with self.assertRaises(ProviderAppError) as context:
                provider.extract("https://www.youtube.com/watch?v=GEFehFHg_os")

        self.assertEqual(context.exception.user_message, "该平台需要登录验证。请在 Mac 端上传已登录平台的 Cookie 后重试。")

    def test_extract_maps_ytdlp_unavailable_format_to_user_message(self) -> None:
        with patch(
            "app.extractors.ytdlp_provider.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["yt-dlp"],
                returncode=1,
                stdout="",
                stderr="ERROR: requested format not available",
            ),
        ):
            with self.assertRaises(ProviderAppError) as context:
                self.provider.extract("https://www.youtube.com/watch?v=GEFehFHg_os")

        self.assertEqual(context.exception.message, "requested format not available")
        self.assertEqual(context.exception.user_message, "当前视频格式不可用，请稍后重试。")

    def test_extract_rejects_non_object_payload(self) -> None:
        with patch(
            "app.extractors.ytdlp_provider.subprocess.run",
            return_value=subprocess.CompletedProcess(args=["yt-dlp"], returncode=0, stdout="[]", stderr=""),
        ):
            with self.assertRaises(ProviderAppError) as context:
                self.provider.extract("https://x.com/demo/status/123")

        self.assertEqual(context.exception.message, "invalid yt-dlp JSON output")

    def test_extract_delegates_x_thread_video_entry(self) -> None:
        extracted = self._extract(
            {
                "title": "demo",
                "webpage_url": "https://x.com/demo/status/123",
                "ext": "mp4",
                "entries": [
                    {
                        "id": "entry-1",
                        "title": "entry demo",
                        "webpage_url": "https://x.com/demo/status/123",
                        "formats": [
                            {
                                "format_id": "hls-1586",
                                "url": "https://video.twimg.com/media/nested.m3u8?tag=4",
                                "protocol": "m3u8_native",
                                "ext": "mp4",
                            },
                            {
                                "format_id": "http-2176",
                                "url": "https://video.twimg.com/media/entry.mp4?tag=4",
                                "protocol": "https",
                                "ext": "mp4",
                            },
                        ],
                    }
                ],
            }
        )

        self.assertEqual(extracted.delivery_mode, DeliveryMode.DELEGATE_YTDLP)
        self.assertIsNone(extracted.direct_url)
        self.assertEqual(extracted.file_extension, "mp4")

    def test_extract_delegates_x_download_when_no_media_candidate_exists(self) -> None:
        extracted = self._extract(
            {
                "title": "demo",
                "webpage_url": "https://x.com/demo/status/123",
                "ext": "mp4",
                "formats": [],
            }
        )

        self.assertEqual(extracted.delivery_mode, DeliveryMode.DELEGATE_YTDLP)
        self.assertIsNone(extracted.direct_url)
        self.assertEqual(extracted.file_extension, "mp4")

    def test_extract_raises_for_douyin_when_only_manifest_candidates_exist(self) -> None:
        with self.assertRaises(ProviderAppError) as context:
            self._extract(
                {
                    "title": "demo",
                    "webpage_url": "https://www.douyin.com/video/123456",
                    "formats": [
                        {
                            "format_id": "hls-720",
                            "url": "https://api-play-hl.amemv.com/aweme/v1/play/stream.m3u8",
                            "protocol": "m3u8_native",
                            "ext": "mp4",
                        }
                    ],
                },
                url="https://www.douyin.com/video/123456",
            )

        self.assertEqual(context.exception.message, "no downloadable media found")

    def test_extract_raises_for_xiaohongshu_when_only_manifest_candidates_exist(self) -> None:
        with self.assertRaises(ProviderAppError) as context:
            self._extract(
                {
                    "title": "demo",
                    "webpage_url": "https://www.xiaohongshu.com/explore/abcdef?xsec_token=1",
                    "formats": [
                        {
                            "format_id": "hls-720",
                            "url": "https://sns-video-hw.xhscdn.com/stream/1/demo.m3u8",
                            "protocol": "m3u8_native",
                            "ext": "mp4",
                        }
                    ],
                },
                url="https://www.xiaohongshu.com/explore/abcdef?xsec_token=1",
            )

        self.assertEqual(context.exception.message, "no downloadable media found")

    def test_extract_accepts_douyin_download_host(self) -> None:
        extracted = self._extract(
            {
                "title": "demo",
                "webpage_url": "https://www.douyin.com/video/123456",
                "formats": [
                    {
                        "format_id": "http-720",
                        "url": "https://api-play-hl.amemv.com/aweme/v1/play/?video_id=1",
                        "protocol": "https",
                        "ext": "mp4",
                    }
                ],
            },
            url="https://www.douyin.com/video/123456",
        )

        self.assertEqual(extracted.direct_url, "https://api-play-hl.amemv.com/aweme/v1/play/?video_id=1")
        self.assertEqual(extracted.direct_url_addresses, ("8.8.8.8",))
        self.assertEqual(extracted.file_extension, "mp4")

    def test_extract_accepts_douyin_cdn_download_host(self) -> None:
        extracted = self._extract(
            {
                "title": "demo",
                "webpage_url": "https://v.douyin.com/yjaQ3bMm4us/",
                "formats": [
                    {
                        "format_id": "http-720",
                        "url": "https://v26-default.douyinvod.com/obj/tos-cn-ve-15/demo.mp4",
                        "protocol": "https",
                        "ext": "mp4",
                    }
                ],
            },
            url="https://v.douyin.com/yjaQ3bMm4us/",
        )

        self.assertEqual(extracted.direct_url, "https://v26-default.douyinvod.com/obj/tos-cn-ve-15/demo.mp4")
        self.assertEqual(extracted.direct_url_addresses, ("8.8.8.8",))
        self.assertEqual(extracted.file_extension, "mp4")

    def test_extract_accepts_xiaohongshu_download_host(self) -> None:
        extracted = self._extract(
            {
                "title": "demo",
                "webpage_url": "https://www.xiaohongshu.com/explore/abcdef?xsec_token=1&xsec_source=app_share",
                "url": "http://sns-bak-v6.xhscdn.com/stream/1/demo.mp4?tag=1",
                "protocol": "http",
                "ext": "mp4",
                "formats": [
                    {
                        "format_id": "http-720",
                        "url": "http://sns-video-hw.xhscdn.com/stream/1/demo.mp4?tag=1",
                        "protocol": "http",
                        "ext": "mp4",
                    }
                ],
            },
            url="https://www.xiaohongshu.com/explore/abcdef?xsec_token=1&xsec_source=app_share",
        )

        self.assertEqual(extracted.direct_url, "https://sns-bak-v6.xhscdn.com/stream/1/demo.mp4?tag=1")
        self.assertEqual(extracted.direct_url_addresses, ("8.8.8.8",))
        self.assertEqual(extracted.file_extension, "mp4")

    def test_extract_delegates_bilibili_split_stream_download(self) -> None:
        extracted = self._extract(
            {
                "title": "demo",
                "webpage_url": "https://www.bilibili.com/video/BV1sRoHB5EHC/?spm_id_from=333.1007.tianma.1-2-2.click",
                "ext": "mp4",
                "requested_downloads": [
                    {
                        "url": None,
                        "protocol": "https+https",
                        "ext": "mp4",
                        "format_id": "100026+30280",
                        "vcodec": "av01.0.00M.10.0.110.01.01.01.0",
                        "acodec": "mp4a.40.2",
                        "requested_formats": [
                            {
                                "url": "https://cn-hbwh-cm-01-02.bilivideo.com/upgcxcode/video.m4s",
                                "protocol": "https",
                                "ext": "mp4",
                                "vcodec": "av01.0.00M.10.0.110.01.01.01.0",
                                "acodec": "none",
                            },
                            {
                                "url": "https://cn-hbwh-cm-01-02.bilivideo.com/upgcxcode/audio.m4s",
                                "protocol": "https",
                                "ext": "m4a",
                                "vcodec": "none",
                                "acodec": "mp4a.40.2",
                            },
                        ],
                    }
                ],
            },
            url="https://www.bilibili.com/video/BV1sRoHB5EHC/?spm_id_from=333.1007.tianma.1-2-2.click",
        )

        self.assertEqual(extracted.delivery_mode, DeliveryMode.DELEGATE_YTDLP)
        self.assertIsNone(extracted.direct_url)
        self.assertEqual(extracted.file_extension, "mp4")

    def test_extract_delegates_youtube_download(self) -> None:
        extracted = self._extract(
            {
                "title": "demo",
                "webpage_url": "https://www.youtube.com/watch?v=GEFehFHg_os",
                "ext": "mp4",
                "requested_downloads": [
                    {
                        "url": None,
                        "protocol": "https+https",
                        "ext": "mp4",
                        "requested_formats": [
                            {"url": "https://rr1---sn.example.googlevideo.com/videoplayback?id=video", "protocol": "https", "ext": "mp4"},
                            {"url": "https://rr1---sn.example.googlevideo.com/videoplayback?id=audio", "protocol": "https", "ext": "m4a"},
                        ],
                    }
                ],
            },
            url="https://www.youtube.com/watch?v=GEFehFHg_os",
        )

        self.assertEqual(extracted.delivery_mode, DeliveryMode.DELEGATE_YTDLP)
        self.assertIsNone(extracted.direct_url)
        self.assertEqual(extracted.file_extension, "mp4")

    def test_extract_uses_ignore_config_flag(self) -> None:
        with patch(
            "app.extractors.ytdlp_provider.subprocess.run",
            return_value=self._completed_process({"title": "demo", "webpage_url": "https://x.com/demo/status/123", "url": "https://video.twimg.com/media/clip.mp4", "ext": "mp4"}),
        ) as run_mock:
            with patch("app.services.url_tools.socket.getaddrinfo", return_value=[(None, None, None, None, ("8.8.8.8", 443))]):
                self.provider.extract("https://x.com/demo/status/123")

        command = run_mock.call_args.args[0]
        self.assertIn("--ignore-config", command)

    def test_extract_adds_youtube_runtime_flags_only_for_youtube(self) -> None:
        settings = Settings(
            provider_timeout_seconds=5,
            yt_dlp_binary='yt-dlp',
            youtube_cookies_from_browser='chrome',
            youtube_cookies_disabled=False,
            youtube_js_runtime='node',
            youtube_remote_components='ejs:github',
        )
        provider = YtDlpProvider(settings)
        payload = {"title": "demo", "webpage_url": "https://www.youtube.com/watch?v=GEFehFHg_os", "ext": "mp4"}
        with patch(
            "app.extractors.ytdlp_provider.subprocess.run",
            return_value=subprocess.CompletedProcess(args=["yt-dlp"], returncode=0, stdout=json.dumps(payload), stderr=""),
        ) as run_mock:
            provider.extract("https://www.youtube.com/watch?v=GEFehFHg_os")

        command = run_mock.call_args.args[0]
        self.assertEqual(command[0], "yt-dlp")
        self.assertNotIn("--cookies-from-browser", command)
        self.assertNotIn("chrome", command)
        self.assertIn("--js-runtimes", command)
        self.assertIn("node", command)
        self.assertIn("--remote-components", command)
        self.assertIn("ejs:github", command)

    def test_extract_omits_youtube_cookie_runtime_flags_when_disabled(self) -> None:
        settings = Settings(
            provider_timeout_seconds=5,
            yt_dlp_binary='yt-dlp',
            youtube_cookies_from_browser='chrome',
            youtube_cookies_disabled=True,
            youtube_js_runtime='node',
            youtube_remote_components='ejs:github',
        )
        provider = YtDlpProvider(settings)
        payload = {"title": "demo", "webpage_url": "https://www.youtube.com/watch?v=GEFehFHg_os", "ext": "mp4"}
        with patch(
            "app.extractors.ytdlp_provider.subprocess.run",
            return_value=subprocess.CompletedProcess(args=["yt-dlp"], returncode=0, stdout=json.dumps(payload), stderr=""),
        ) as run_mock:
            provider.extract("https://www.youtube.com/watch?v=GEFehFHg_os")

        command = run_mock.call_args.args[0]
        self.assertNotIn("--cookies-from-browser", command)
        self.assertIn("--js-runtimes", command)
        self.assertIn("--remote-components", command)

    def test_extract_does_not_add_youtube_runtime_flags_for_non_youtube(self) -> None:
        settings = Settings(
            provider_timeout_seconds=5,
            yt_dlp_binary='yt-dlp',
            youtube_cookies_from_browser='chrome',
            youtube_cookies_disabled=False,
            youtube_js_runtime='node',
            youtube_remote_components='ejs:github',
        )
        provider = YtDlpProvider(settings)
        payload = {"title": "demo", "webpage_url": "https://x.com/demo/status/123", "url": "https://video.twimg.com/media/clip.mp4", "ext": "mp4"}
        with patch(
            "app.extractors.ytdlp_provider.subprocess.run",
            return_value=subprocess.CompletedProcess(args=["yt-dlp"], returncode=0, stdout=json.dumps(payload), stderr=""),
        ) as run_mock:
            with patch("app.services.url_tools.socket.getaddrinfo", return_value=[(None, None, None, None, ("8.8.8.8", 443))]):
                provider.extract("https://x.com/youtube.com/status/123")

        command = run_mock.call_args.args[0]
        self.assertNotIn("--cookies-from-browser", command)
        self.assertNotIn("--js-runtimes", command)
        self.assertNotIn("--remote-components", command)


if __name__ == "__main__":
    unittest.main()
