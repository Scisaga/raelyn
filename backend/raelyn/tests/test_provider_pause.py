from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy.dialects import postgresql

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs.claim import claim_next_job
from raelyn.services.provider_pause import (
    ProviderPauseRequestError,
    bilibili_provider_pause_message,
    clear_provider_pause,
    get_provider_pause,
    set_provider_paused,
)
from raelyn.services.ytdlp import (
    YtdlpCookiesInvalidError,
    _raise_if_provider_pause_messages,
    _raise_if_youtube_bot_check_messages,
)


class ProviderPauseStateTests(unittest.TestCase):
    def test_set_and_clear_provider_pause(self) -> None:
        item = SimpleNamespace(value={}, updated_at=None)
        session = Mock()
        session.get.return_value = item

        pause = set_provider_paused(
            session,
            provider="bilibili",
            reason="bilibili_risk_control",
            message="B站任务已暂停：请更新 Cookie",
        )

        self.assertTrue(pause["paused"])
        self.assertEqual(pause["reason"], "bilibili_risk_control")
        self.assertEqual(item.value["bilibili"]["message"], "B站任务已暂停：请更新 Cookie")

        cleared = clear_provider_pause(session, provider="bilibili")

        self.assertFalse(cleared["paused"])
        self.assertEqual(item.value, {})

    def test_get_provider_pause_defaults_to_not_paused(self) -> None:
        session = Mock()
        session.get.return_value = None

        pause = get_provider_pause(session, "bilibili")

        self.assertEqual(
            pause,
            {"paused": False, "reason": None, "message": None, "set_at": None},
        )


class ClaimNextJobProviderPauseTests(unittest.TestCase):
    def test_claim_next_job_prefers_newer_jobs_when_priority_matches(self) -> None:
        now = datetime(2026, 3, 20, 8, 0, tzinfo=timezone.utc)
        older_job = SimpleNamespace(
            type="video.download.youtube",
            status="pending",
            worker_id=None,
            error_message=None,
            error_stack=None,
            progress_current=None,
            progress_total=None,
            started_at=None,
            lease_expires_at=None,
            priority=7,
            scheduled_for=now,
            created_at=datetime(2026, 3, 20, 7, 0, tzinfo=timezone.utc),
            id="older-job",
        )
        newer_job = SimpleNamespace(
            type="video.download.youtube",
            status="pending",
            worker_id=None,
            error_message=None,
            error_stack=None,
            progress_current=None,
            progress_total=None,
            started_at=None,
            lease_expires_at=None,
            priority=7,
            scheduled_for=now,
            created_at=datetime(2026, 3, 20, 7, 30, tzinfo=timezone.utc),
            id="newer-job",
        )
        scalar_result = Mock()
        scalar_result.all.return_value = [newer_job, older_job]
        session = Mock()

        def _execute(stmt):
            compiled = str(stmt.compile(dialect=postgresql.dialect())).lower()
            self.assertIn("job.created_at desc", compiled)
            self.assertIn("job.id desc", compiled)
            return Mock(scalars=Mock(return_value=scalar_result))

        session.execute.side_effect = _execute

        with patch("raelyn.jobs.claim.is_paused", return_value=False):
            with patch("raelyn.jobs.claim.job_provider", return_value="youtube"):
                with patch("raelyn.jobs.claim.is_provider_paused", return_value=False):
                    claimed = claim_next_job(session, worker_id="worker-1", lease_seconds=60)

        self.assertIs(claimed, newer_job)
        self.assertEqual(newer_job.status, "running")
        self.assertEqual(newer_job.worker_id, "worker-1")

    def test_claim_next_job_skips_paused_provider_jobs(self) -> None:
        now = datetime.now(timezone.utc)
        bili_job = SimpleNamespace(
            type="video.download.bilibili",
            status="pending",
            worker_id=None,
            error_message="old",
            error_stack="old",
            progress_current=10,
            progress_total=20,
            started_at=None,
            lease_expires_at=None,
            priority=10,
            scheduled_for=now,
            created_at=now,
            id="bili-job",
        )
        yt_job = SimpleNamespace(
            type="video.download.youtube",
            status="pending",
            worker_id=None,
            error_message="old",
            error_stack="old",
            progress_current=10,
            progress_total=20,
            started_at=None,
            lease_expires_at=None,
            priority=9,
            scheduled_for=now,
            created_at=now,
            id="yt-job",
        )
        scalar_result = Mock()
        scalar_result.all.return_value = [bili_job, yt_job]
        session = Mock()
        session.execute.return_value = Mock(scalars=Mock(return_value=scalar_result))

        with patch("raelyn.jobs.claim.is_paused", return_value=False):
            with patch("raelyn.jobs.claim.job_provider", side_effect=["bilibili", "youtube"]):
                with patch(
                    "raelyn.jobs.claim.is_provider_paused",
                    side_effect=lambda _session, provider: provider == "bilibili",
                ):
                    claimed = claim_next_job(session, worker_id="worker-1", lease_seconds=60)

        self.assertIs(claimed, yt_job)
        self.assertEqual(yt_job.status, "running")
        self.assertEqual(yt_job.worker_id, "worker-1")
        self.assertIsNone(yt_job.error_message)
        self.assertIsNone(yt_job.error_stack)
        self.assertIsNone(yt_job.progress_current)
        self.assertIsNone(yt_job.progress_total)


class BilibiliProviderPauseDetectionTests(unittest.TestCase):
    def test_bilibili_pause_message_describes_412_not_just_cookies(self) -> None:
        message = bilibili_provider_pause_message()

        self.assertIn("412", message)
        self.assertIn("yt-dlp", message)
        self.assertIn("若仍 412", message)

    def test_raise_if_provider_pause_messages_detects_bilibili_412(self) -> None:
        with self.assertRaises(ProviderPauseRequestError) as ctx:
            _raise_if_provider_pause_messages(
                ["ERROR: [BiliBili] BV1xx: Unable to download webpage: HTTP Error 412: Precondition Failed"]
            )

        self.assertEqual(ctx.exception.provider, "bilibili")
        self.assertEqual(ctx.exception.reason, "bilibili_risk_control")


class YoutubeCookiesPauseDetectionTests(unittest.TestCase):
    def test_raise_if_youtube_bot_check_messages_treats_as_cookie_expired(self) -> None:
        with self.assertRaises(YtdlpCookiesInvalidError) as ctx:
            _raise_if_youtube_bot_check_messages(
                [
                    (
                        "ERROR: [youtube] abc123: Sign in to confirm you're not a bot. "
                        "This helps protect our community."
                    )
                ],
                provider="youtube",
            )

        self.assertEqual(ctx.exception.provider, "youtube")
        self.assertEqual(ctx.exception.reason, "ytdlp_cookies_expired")
        self.assertIn("YTDLP_COOKIES_YOUTUBE 已失效", str(ctx.exception))

    def test_raise_if_youtube_tab_authcheck_treats_as_cookie_expired(self) -> None:
        with self.assertRaises(YtdlpCookiesInvalidError) as ctx:
            _raise_if_youtube_bot_check_messages(
                [
                    (
                        "ERROR: [youtube:tab] @lukoudaye-: Playlists that require authentication "
                        "may not extract correctly without a successful webpage download. "
                        "If you are not downloading private content, pass "
                        '"--extractor-args youtubetab:skip=authcheck" to skip this check'
                    )
                ],
                provider="youtube",
            )

        self.assertEqual(ctx.exception.provider, "youtube")
        self.assertEqual(ctx.exception.reason, "ytdlp_cookies_expired")
        self.assertIn("频道/播放列表鉴权检查失败", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
