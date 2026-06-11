from __future__ import annotations

import sys
import unittest
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.models import Video
from raelyn.services.video_time import normalize_time_basis, selected_content_time_subquery, timeline_time_expr


class VideoTimeTests(unittest.TestCase):
    def test_content_timeline_prefers_trusted_evidence_and_falls_back_to_platform_time(self) -> None:
        stmt = select(Video.id, timeline_time_expr().label("timeline_at")).where(timeline_time_expr().is_not(None))
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()

        self.assertIn("coalesce", compiled)
        self.assertIn("make_timestamptz", compiled)
        self.assertIn("video_time_evidence", compiled)
        self.assertIn("content_published_at", compiled)
        self.assertIn("codex_batch_publish_time_inference", compiled)
        self.assertIn("external_title_search", compiled)
        self.assertIn("confidence", compiled)
        self.assertIn("video.published_at", compiled)

    def test_platform_timeline_uses_raw_video_published_at(self) -> None:
        stmt = select(Video.id, timeline_time_expr(time_basis="platform").label("timeline_at"))
        compiled = str(stmt.compile(dialect=postgresql.dialect())).lower()

        self.assertIn("video.published_at", compiled)
        self.assertNotIn("video_time_evidence", compiled)

    def test_selected_content_time_subquery_uses_set_based_ranking(self) -> None:
        selected = selected_content_time_subquery("selected_video_time")
        stmt = select(selected.c.video_id, selected.c.content_published_at)
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()

        self.assertIn("row_number() over", compiled)
        self.assertIn("partition by video_time_evidence.video_id", compiled)
        self.assertIn("content_published_at", compiled)
        self.assertIn("codex_batch_publish_time_inference", compiled)
        self.assertIn("external_title_search", compiled)

    def test_normalize_time_basis_rejects_unknown_values(self) -> None:
        self.assertEqual(normalize_time_basis(None), "content")
        self.assertEqual(normalize_time_basis("platform"), "platform")
        with self.assertRaises(ValueError):
            normalize_time_basis("event")


if __name__ == "__main__":
    unittest.main()
