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
from app.domain.models import DeliveryMode
from app.extractors.ytdlp_provider import YtDlpProvider


class YtDlpProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = YtDlpProvider(Settings(provider_timeout_seconds=5))

    def test_can_handle_supported_public_urls(self) -> None:
        urls = [
            "https://x.com/demo/status/123",
            "https://twitter.com/demo/status/123",
            "https://www.douyin.com/video/123456",
            "https://v.douyin.com/abc123/",
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
            "https://www.douyin.com/user/demo",
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

    def test_extract_uses_requested_formats_when_direct_url_is_missing(self) -> None:
        extracted = self._extract(
            {
                "title": "demo",
                "webpage_url": "https://x.com/demo/status/123",
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

        self.assertEqual(extracted.direct_url, "https://video.twimg.com/media/clip.mp4?tag=1")
        self.assertEqual(extracted.file_extension, "mp4")
        self.assertEqual(extracted.direct_url_addresses, ("8.8.8.8",))

    def test_extract_prefers_mp4_format_when_multiple_direct_candidates_exist(self) -> None:
        extracted = self._extract(
            {
                "title": "demo",
                "webpage_url": "https://x.com/demo/status/123",
                "formats": [
                    {
                        "format_id": "hls-900",
                        "url": "https://video.twimg.com/media/stream.m3u8?tag=2",
                        "protocol": "m3u8_native",
                        "ext": "mp4",
                    },
                    {
                        "format_id": "http-900",
                        "url": "https://video.twimg.com/media/clip.mov?tag=2",
                        "protocol": "https",
                        "ext": "mov",
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

        self.assertEqual(extracted.direct_url, "https://video.twimg.com/media/clip.mp4?tag=2")
        self.assertEqual(extracted.file_extension, "mp4")

    def test_extract_skips_top_level_hls_url_and_falls_back_to_direct_format(self) -> None:
        extracted = self._extract(
            {
                "title": "demo",
                "webpage_url": "https://x.com/demo/status/123",
                "url": "https://video.twimg.com/media/top-level.m3u8?tag=3",
                "ext": "mp4",
                "formats": [
                    {
                        "format_id": "http-720",
                        "url": "https://video.twimg.com/media/direct.mp4?tag=3",
                        "protocol": "https",
                        "ext": "mp4",
                    }
                ],
            }
        )

        self.assertEqual(extracted.direct_url, "https://video.twimg.com/media/direct.mp4?tag=3")
        self.assertEqual(extracted.file_extension, "mp4")

    def test_extract_rejects_non_object_payload(self) -> None:
        with patch(
            "app.extractors.ytdlp_provider.subprocess.run",
            return_value=subprocess.CompletedProcess(args=["yt-dlp"], returncode=0, stdout="[]", stderr=""),
        ):
            with self.assertRaises(ProviderAppError) as context:
                self.provider.extract("https://x.com/demo/status/123")

        self.assertEqual(context.exception.message, "invalid yt-dlp JSON output")

    def test_extract_raises_when_only_manifest_candidates_exist(self) -> None:
        with self.assertRaises(ProviderAppError) as context:
            self._extract(
                {
                    "title": "demo",
                    "webpage_url": "https://x.com/demo/status/123",
                    "url": "https://video.twimg.com/media/top-level.m3u8?tag=4",
                    "requested_downloads": [
                        {
                            "requested_formats": [
                                {
                                    "format_id": "hls-1586",
                                    "url": "https://video.twimg.com/media/nested.m3u8?tag=4",
                                    "protocol": "m3u8_native",
                                    "ext": "mp4",
                                }
                            ]
                        }
                    ],
                    "formats": [
                        {
                            "format_id": "hls-900",
                            "url": "https://video.twimg.com/media/stream.m3u8?tag=4",
                            "protocol": "m3u8_native",
                            "ext": "mp4",
                        }
                    ],
                }
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
        self.assertIn("--cookies-from-browser", command)
        self.assertIn("chrome", command)
        self.assertIn("--js-runtimes", command)
        self.assertIn("node", command)
        self.assertIn("--remote-components", command)
        self.assertIn("ejs:github", command)

    def test_extract_does_not_add_youtube_runtime_flags_for_non_youtube(self) -> None:
        settings = Settings(
            provider_timeout_seconds=5,
            yt_dlp_binary='yt-dlp',
            youtube_cookies_from_browser='chrome',
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
                provider.extract("https://x.com/demo/status/123")

        command = run_mock.call_args.args[0]
        self.assertNotIn("--cookies-from-browser", command)
        self.assertNotIn("--js-runtimes", command)
        self.assertNotIn("--remote-components", command)


if __name__ == "__main__":
    unittest.main()
