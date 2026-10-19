from __future__ import annotations

from pathlib import Path
import socket
import struct
import sys
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast
from urllib.parse import urlsplit

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from yt_dlp import YoutubeDL
from yt_dlp.extractor.youtube import YoutubeTabIE
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.utils import ExtractorError


_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.services.provider_pause import ProviderPauseRequestError
from raelyn.models import Job
from raelyn.services.ytdlp import (
    YtdlpCookiesInvalidError,
    _YtdlpCaptureLogger,
    _raise_if_cookie_invalid_messages,
    _raise_if_youtube_bot_check_messages,
    _youtube_authcheck_diagnostics,
)
from raelyn.worker import _job_retry_policy, _persist_provider_pause_after_rollback


_AUTHCHECK_MESSAGE = (
    "ERROR: [youtube:tab] Playlists that require authentication may not "
    "extract correctly without a successful webpage download"
)


class YoutubeAuthDiagnosticsTests(unittest.TestCase):
    def test_extracts_transport_error_without_credentials_or_urls(self) -> None:
        result = _youtube_authcheck_diagnostics([
            "[youtube:tab] Unable to download webpage: HTTP Error 502: Bad Gateway "
            "https://test-user:test-secret@example.test/channel?token=test-token",
            "curl: (28) Operation timed out; HTTP Error 502",
        ])
        self.assertEqual(result, "HTTP 502、curl 28、网络超时、频道网页下载失败")
        for sensitive in ("test-user", "test-secret", "example.test", "test-token"):
            self.assertNotIn(sensitive, result)

    def test_does_not_invent_transport_error_for_missing_evidence(self) -> None:
        self.assertEqual(_youtube_authcheck_diagnostics([]), "未捕获明确底层原因")

    def test_authcheck_with_transport_failure_does_not_request_provider_pause(self) -> None:
        for failure in (
            "HTTP Error 503",
            "curl: (28) Operation too slow",
            "curl: (56) Recv failure",
            "Connection reset by peer",
            "Could not resolve host",
            "certificate verify failed",
        ):
            with self.subTest(failure=failure):
                with self.assertRaises(RuntimeError) as raised:
                    _raise_if_youtube_bot_check_messages([
                        f"[youtube:tab] Unable to download webpage: {failure}",
                        _AUTHCHECK_MESSAGE,
                    ], provider="youtube")
                self.assertIs(type(raised.exception), RuntimeError)
                self.assertIn("网络请求失败", str(raised.exception))
                self.assertNotIn("cookies", str(raised.exception))

    def test_curl_timeout_code_is_evidence_even_without_timeout_text(self) -> None:
        self.assertEqual(
            _youtube_authcheck_diagnostics(["curl: (28) Operation too slow"]),
            "curl 28、网络超时",
        )

    def test_explicit_bot_check_takes_priority_over_transport_and_authcheck(self) -> None:
        with self.assertRaises(ProviderPauseRequestError) as raised:
            _raise_if_youtube_bot_check_messages([
                "[youtube:tab] Unable to download webpage: curl: (28) Operation too slow",
                _AUTHCHECK_MESSAGE,
                "Sign in to confirm you're not a bot",
            ], provider="youtube")
        self.assertEqual(raised.exception.reason, "youtube_bot_check")

    def test_explicit_cookie_expiration_remains_a_cookie_error(self) -> None:
        with self.assertRaises(YtdlpCookiesInvalidError) as raised:
            _raise_if_cookie_invalid_messages([
                "[youtube:tab] Unable to download webpage: curl: (28) Operation too slow",
                _AUTHCHECK_MESSAGE,
                "The provided YouTube account cookies are no longer valid",
            ], provider="youtube")
        self.assertEqual(raised.exception.reason, "ytdlp_cookies_expired")

    def test_access_denial_is_not_overridden_by_an_earlier_timeout(self) -> None:
        for status in (401, 403, 429):
            with self.subTest(status=status):
                with self.assertRaises(ProviderPauseRequestError) as raised:
                    _raise_if_youtube_bot_check_messages([
                        "[youtube:tab] Unable to download webpage: curl: (28) Operation too slow",
                        f"[youtube:tab] Unable to download webpage: HTTP Error {status}",
                        _AUTHCHECK_MESSAGE,
                    ], provider="youtube")
                self.assertEqual(raised.exception.reason, "youtube_auth_check")

    def test_other_provider_is_not_classified_as_youtube_failure(self) -> None:
        _raise_if_youtube_bot_check_messages([
            "[youtube:tab] Unable to download webpage: curl: (28) Operation too slow",
            _AUTHCHECK_MESSAGE,
        ], provider="bilibili")


class YoutubeTabNetworkIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.release_requests = threading.Event()
        release = cls.release_requests

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = urlsplit(self.path).path
                if path == "/timeout":
                    release.wait(5)
                    return
                if path == "/reset":
                    self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                    self.connection.close()
                    return
                self.send_response(int(path.strip("/")))
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.release_requests.set()
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join()

    def _capture_webpage_failure(self, path: str) -> list[str]:
        logger = _YtdlpCaptureLogger()
        base_url = f"http://127.0.0.1:{self.server.server_port}"
        opts: dict[str, Any] = {
            "logger": logger,
            "quiet": True,
            # 所有请求都限定到本地服务，避免读取环境代理或访问上游。
            "proxy": base_url,
            "cachedir": False,
            "socket_timeout": 0.2,
            "extractor_retries": 0,
            "impersonate": ImpersonateTarget.from_str("chrome"),
        }
        with YoutubeDL(opts) as ydl:
            ie = cast(YoutubeTabIE, ydl.get_info_extractor("YoutubeTab"))
            # 仅使用测试 Cookie 进入 SDK 的真实 authcheck 路径，不加载业务 Cookie。
            ie._set_cookie(".youtube.com", "SAPISID", "regression-test-session")
            ie._set_cookie(".youtube.com", "LOGIN_INFO", "regression-test-login")
            self.assertTrue(ie.is_authenticated)
            cookies_before = [(cookie.domain, cookie.name, cookie.value) for cookie in ydl.cookiejar]
            with self.assertRaises(ExtractorError) as raised:
                ie._extract_data(f"{base_url}{path}", "network-regression")
            self.assertIn("without a successful webpage download", str(raised.exception))
            self.assertEqual(
                [(cookie.domain, cookie.name, cookie.value) for cookie in ydl.cookiejar],
                cookies_before,
            )
            return logger.warnings + logger.errors + [str(raised.exception)]

    def test_real_transport_failures_never_pause_even_after_retry_exhaustion(self) -> None:
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        job = Job(id=uuid.uuid4(), type="media.sync_videos", max_attempts=2)
        for path in ("/timeout", "/reset", "/502", "/503"):
            with self.subTest(path=path):
                msgs = self._capture_webpage_failure(path)
                with self.assertRaises(RuntimeError) as raised:
                    _raise_if_youtube_bot_check_messages(msgs, provider="youtube")
                self.assertIs(type(raised.exception), RuntimeError)
                self.assertIn("网络请求失败", str(raised.exception))
                for attempt in (1, 2):
                    self.assertEqual(
                        _job_retry_policy(job, raised.exception, next_attempt=attempt),
                        (2, 10 if attempt == 1 else None, None),
                    )
                    with Session(engine) as session:
                        _persist_provider_pause_after_rollback(session, job=job, err=raised.exception)
                        # 普通网络失败不读取或改写 provider/cookies 状态。
                        self.assertFalse(session.in_transaction())
                        self.assertEqual(len(session.new), 0)

    def test_real_access_denials_keep_the_existing_auth_pause_policy(self) -> None:
        for status in (401, 403, 429):
            with self.subTest(status=status):
                msgs = self._capture_webpage_failure(f"/{status}")
                with self.assertRaises(ProviderPauseRequestError) as raised:
                    _raise_if_youtube_bot_check_messages(msgs, provider="youtube")
                self.assertEqual(raised.exception.reason, "youtube_auth_check")


if __name__ == "__main__":
    # 示例：./.venv/bin/python -m unittest backend.raelyn.tests.test_ytdlp_auth_diagnostics
    unittest.main()
