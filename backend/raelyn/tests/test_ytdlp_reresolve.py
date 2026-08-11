from __future__ import annotations

import subprocess
import sys
import threading
import unittest
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.config import settings
from raelyn.services.ffmpeg import ffmpeg_bin
from raelyn.services.ytdlp import (
    YtdlpTransientDownloadError,
    _is_youtube_media_transport_error,
    _is_youtube_original_url_reresolve_error,
    ytdlp_download,
)


class _RotatingMediaHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self) -> None:
        counts: Counter[str] = getattr(self.server, "request_counts")
        counts[self.path] += 1

        if self.path in {"/video", "/always-bad-video", "/fallback-video"}:
            if self.path == "/always-bad-video":
                media_path = "/bad.mp4"
            elif self.path == "/fallback-video":
                media_path = "/bad.mp4" if counts[self.path] <= 2 else "/good.mp4"
            else:
                media_path = "/bad.mp4" if counts["/bad.mp4"] == 0 else "/good.mp4"
            body = (
                "<html><head><title>重新解析测试</title></head>"
                f"<body><video src=\"{media_path}\"></video></body></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/bad.mp4":
            self.send_error(502, "Bad Gateway")
            return

        if self.path == "/good.mp4":
            body: bytes = getattr(self.server, "media_payload")
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_error(404)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class YtdlpReresolveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with TemporaryDirectory(prefix="raelyn-reresolve-media-") as tmp:
            media_path = Path(tmp) / "valid.mp4"
            completed = subprocess.run(
                [
                    ffmpeg_bin(),
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=32x32:r=10:d=0.2",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=0.2",
                    "-shortest",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    str(media_path),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if completed.returncode != 0:
                raise unittest.SkipTest("需要项目 ffmpeg 生成真实 502 重解析测试视频")
            cls.media_payload = media_path.read_bytes()

    def test_classifies_only_download_transport_errors(self) -> None:
        self.assertTrue(
            _is_youtube_media_transport_error(
                "ERROR: [download] Got error: curl: (56) CONNECT tunnel failed, response 502"
            )
        )
        self.assertTrue(
            _is_youtube_media_transport_error(
                "ERROR: [download] Got error: curl: (56) Connection closed abruptly"
            )
        )
        self.assertFalse(
            _is_youtube_media_transport_error(
                "ERROR: [youtube] Unable to download webpage: HTTP Error 502: Bad Gateway"
            )
        )
        self.assertFalse(
            _is_youtube_media_transport_error(
                "ERROR: [download] HTTP Error 404: Not Found"
            )
        )
        self.assertFalse(
            _is_youtube_media_transport_error(
                "ERROR: [download] Got error: HTTP Error 502: Bad Gateway\n"
                "ERROR: Unable to download video subtitles"
            )
        )
        self.assertTrue(
            _is_youtube_media_transport_error(
                "WARNING: en subtitles are not available\n"
                "ERROR: [download] Got error: HTTP Error 502: Bad Gateway"
            )
        )
        self.assertFalse(
            _is_youtube_media_transport_error(
                "WARNING: [download] Unable to download thumbnail\n"
                "ERROR: [youtube] Unable to download webpage: HTTP Error 502: Bad Gateway"
            )
        )
        self.assertTrue(
            _is_youtube_original_url_reresolve_error(
                "ERROR: [youtube] xICvY586XZ8: No video formats found; please report this issue"
            )
        )

    def test_reresolves_original_page_after_media_502(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _RotatingMediaHandler)
        server.request_counts = Counter()  # type: ignore[attr-defined]
        server.media_payload = self.media_payload  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        original_proxy = settings.ytdlp_proxy
        original_impersonate = settings.ytdlp_youtube_impersonate
        activities: list[str] = []
        try:
            settings.ytdlp_proxy = ""
            settings.ytdlp_youtube_impersonate = ""
            with TemporaryDirectory() as tmp:
                out_dir = Path(tmp)
                port = server.server_address[1]
                info = ytdlp_download(
                    url=f"http://127.0.0.1:{port}/video",
                    provider="youtube",
                    out_dir=out_dir,
                    use_provider_cookies=False,
                    write_subtitles=False,
                    write_auto_subtitles=False,
                    activity_hook=lambda: activities.append("touch"),
                )
                media_files = sorted(path for path in out_dir.iterdir() if path.suffix == ".mp4")
                attempt_dirs = sorted(path for path in out_dir.iterdir() if path.name.startswith(".ytdlp-resolve-"))
                media_payloads = [path.read_bytes() for path in media_files]
                returned_media_path = Path(info["requested_downloads"][0]["filepath"])
                returned_media_path_exists = returned_media_path.exists()

            counts: Counter[str] = server.request_counts  # type: ignore[attr-defined]
            self.assertTrue(info.get("id"))
            self.assertGreaterEqual(counts["/video"], 2)
            self.assertEqual(counts["/bad.mp4"], 2)
            self.assertEqual(counts["/good.mp4"], 1)
            self.assertEqual(len(media_files), 1)
            self.assertEqual(media_payloads, [self.media_payload])
            self.assertEqual(attempt_dirs, [])
            self.assertGreater(len(activities), 0)
            self.assertEqual(returned_media_path.parent, out_dir)
            self.assertTrue(returned_media_path_exists)
        finally:
            settings.ytdlp_proxy = original_proxy
            settings.ytdlp_youtube_impersonate = original_impersonate
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_reports_exhausted_original_page_reresolve_after_media_502(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _RotatingMediaHandler)
        server.request_counts = Counter()  # type: ignore[attr-defined]
        server.media_payload = self.media_payload  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        original_proxy = settings.ytdlp_proxy
        original_impersonate = settings.ytdlp_youtube_impersonate
        try:
            settings.ytdlp_proxy = ""
            settings.ytdlp_youtube_impersonate = ""
            with TemporaryDirectory() as tmp:
                port = server.server_address[1]
                with self.assertRaises(YtdlpTransientDownloadError) as raised:
                    ytdlp_download(
                        url=f"http://127.0.0.1:{port}/always-bad-video",
                        provider="youtube",
                        out_dir=Path(tmp),
                        use_provider_cookies=False,
                        write_subtitles=False,
                        write_auto_subtitles=False,
                    )

            counts: Counter[str] = server.request_counts  # type: ignore[attr-defined]
            self.assertEqual(raised.exception.reason, "youtube_media_transport")
            self.assertIn("已尝试原 selector 和一个 fallback selector", str(raised.exception))
            self.assertIn("原 selector 从视频页解析 2 轮、fallback selector 解析 1 轮", str(raised.exception))
            self.assertEqual(counts["/always-bad-video"], 3)
        finally:
            settings.ytdlp_proxy = original_proxy
            settings.ytdlp_youtube_impersonate = original_impersonate
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_tries_one_fallback_selector_after_reresolve_exhausted(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _RotatingMediaHandler)
        server.request_counts = Counter()  # type: ignore[attr-defined]
        server.media_payload = self.media_payload  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        original_proxy = settings.ytdlp_proxy
        original_impersonate = settings.ytdlp_youtube_impersonate
        try:
            settings.ytdlp_proxy = ""
            settings.ytdlp_youtube_impersonate = ""
            with TemporaryDirectory() as tmp:
                port = server.server_address[1]
                info = ytdlp_download(
                    url=f"http://127.0.0.1:{port}/fallback-video",
                    provider="youtube",
                    out_dir=Path(tmp),
                    use_provider_cookies=False,
                    write_subtitles=False,
                    write_auto_subtitles=False,
                )

            counts: Counter[str] = server.request_counts  # type: ignore[attr-defined]
            self.assertTrue(info.get("id"))
            self.assertEqual(counts["/fallback-video"], 3)
        finally:
            settings.ytdlp_proxy = original_proxy
            settings.ytdlp_youtube_impersonate = original_impersonate
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
