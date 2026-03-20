from __future__ import annotations

import sys
import unittest
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy.dialects import postgresql

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.models import Playlist
from raelyn.services.brief_prompt import build_brief_prompt_for_period


def _scalar_one_or_none(value):
    return Mock(scalar_one_or_none=Mock(return_value=value))


def _scalars_all(values):
    scalars = Mock(all=Mock(return_value=list(values)))
    return Mock(scalars=Mock(return_value=scalars))


class BriefPromptTests(unittest.TestCase):
    def test_build_brief_prompt_for_period_reports_no_transcript_when_only_playback_exists(self) -> None:
        playlist_id = uuid.uuid4()
        session = Mock()
        session.get.return_value = Playlist(id=playlist_id, name="示例", brief_granularity="week")
        session.execute.side_effect = [
            _scalars_all([uuid.uuid4()]),
            _scalars_all([]),
            _scalar_one_or_none(uuid.uuid4()),
        ]

        with patch("raelyn.services.brief_prompt.ensure_video_published_at_backfilled"):
            with self.assertRaises(LookupError) as ctx:
                build_brief_prompt_for_period(
                    session,
                    playlist_id=playlist_id,
                    granularity="week",
                    date_in_period=date(2026, 3, 9),
                )

        self.assertEqual(str(ctx.exception), "no transcript")
        transcript_stmt = session.execute.call_args_list[1].args[0]
        playback_stmt = session.execute.call_args_list[2].args[0]
        transcript_sql = str(transcript_stmt.compile(dialect=postgresql.dialect())).lower()
        playback_sql = str(playback_stmt.compile(dialect=postgresql.dialect())).lower()
        self.assertIn("asset.type =", transcript_sql)
        self.assertIn("asset.format =", transcript_sql)
        self.assertIn("asset.type =", playback_sql)


if __name__ == "__main__":
    unittest.main()
