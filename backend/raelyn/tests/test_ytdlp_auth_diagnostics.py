from __future__ import annotations

from pathlib import Path
import sys
import unittest


_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.services.provider_pause import ProviderPauseRequestError
from raelyn.services.ytdlp import _raise_if_youtube_bot_check_messages, _youtube_authcheck_diagnostics


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

    def test_authcheck_keeps_provider_pause_reason_and_transport_evidence(self) -> None:
        with self.assertRaises(ProviderPauseRequestError) as raised:
            _raise_if_youtube_bot_check_messages([
                "[youtube:tab] Unable to download webpage: HTTP Error 503",
                "ERROR: [youtube:tab] Playlists that require authentication may not "
                "extract correctly without a successful webpage download",
            ], provider="youtube")
        self.assertEqual(raised.exception.reason, "youtube_auth_check")
        self.assertIn("底层诊断：HTTP 503、频道网页下载失败", str(raised.exception))


if __name__ == "__main__":
    # 示例：./.venv/bin/python -m unittest backend.raelyn.tests.test_ytdlp_auth_diagnostics
    unittest.main()
