from __future__ import annotations

import json
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs.enqueue import _normalize_dedupe_key_and_params
from raelyn.models import (
    EventRegimeCandidate,
    EventRegimeRun,
    EventRegimeSignal,
    EventRegimeState,
    MarketEvent,
    MarketEventEmbedding,
    MarketEventEntity,
    MarketEventEvidence,
    MarketEventRelation,
)
from raelyn.services import event_analysis


class EventAnalysisTests(unittest.TestCase):
    def test_event_models_have_expected_constraints(self) -> None:
        self.assertIn("market_event_source_event_ux", {c.name for c in MarketEvent.__table__.constraints})
        self.assertIn("market_event_evidence_ux", {c.name for c in MarketEventEvidence.__table__.constraints})
        self.assertIn("market_event_entity_ux", {c.name for c in MarketEventEntity.__table__.constraints})
        self.assertIn("market_event_embedding_ux", {c.name for c in MarketEventEmbedding.__table__.constraints})
        self.assertIn("event_regime_signal_ux", {c.name for c in EventRegimeSignal.__table__.constraints})
        self.assertIn("event_regime_candidate_ux", {c.name for c in EventRegimeCandidate.__table__.constraints})

        for model in [MarketEvent, MarketEventEvidence, MarketEventEntity, MarketEventRelation, MarketEventEmbedding]:
            self.assertIn("event_id" if model is not MarketEvent else "source_video_id", model.__table__.columns)
        self.assertIn("last_ready_run_id", EventRegimeState.__table__.columns)
        self.assertIn("event_total", EventRegimeRun.__table__.columns)

    def test_parse_event_response_strips_think_and_drops_bad_events(self) -> None:
        raw = """
<think>hidden</think>
```json
{
  "events": [
    {"title": "Fed cuts rates", "summary": "Fed lowered rates.", "confidence": 0.91},
    {"title": "", "summary": "", "confidence": 0.8},
    {"title": "bad confidence", "summary": "x", "confidence": "high"}
  ]
}
```
"""
        events, warnings = event_analysis.parse_event_extraction_response(raw)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["confidence"], 0.91)
        self.assertGreaterEqual(len(warnings), 2)

    def test_event_time_unknown_does_not_parse(self) -> None:
        start, end, precision = event_analysis._event_time({"event_time": {"start": "", "time_precision": "unknown"}})
        self.assertIsNone(start)
        self.assertIsNone(end)
        self.assertEqual(precision, "unknown")

    def test_update_event_status_accepts_and_enqueues_embedding(self) -> None:
        event_id = uuid.uuid4()
        video_id = uuid.uuid4()
        event = MarketEvent(
            id=event_id,
            source_video_id=video_id,
            source_hash="h",
            event_key="k",
            status="draft",
            event_type="macro",
            time_precision="day",
            event_time_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        session = Mock()
        session.get.return_value = event

        with patch("raelyn.services.event_analysis.mark_playlists_event_regime_dirty_for_video") as mark_dirty:
            with patch("raelyn.services.event_analysis.enqueue_job") as enqueue_job:
                updated = event_analysis.update_event_status(session, event_id=event_id, status="accepted")

        self.assertIs(updated, event)
        self.assertEqual(event.status, "accepted")
        mark_dirty.assert_called_once_with(session, video_id)
        enqueue_job.assert_called_once_with(session, type_="event.embed", params={"event_id": str(event_id)}, priority=0)

    def test_job_dedupe_keys_use_event_job_types(self) -> None:
        video_id = uuid.uuid4()
        event_id = uuid.uuid4()
        playlist_id = uuid.uuid4()

        with patch("raelyn.jobs.enqueue.settings.llm_model", "qwen3.6:35b"):
            key, params = _normalize_dedupe_key_and_params("video.extract_events", {"video_id": str(video_id)})
        self.assertEqual(key, f"video_event_extract:{video_id}:qwen3.6:35b:missing")
        self.assertFalse(params["force"])

        key, _ = _normalize_dedupe_key_and_params("event.embed", {"event_id": str(event_id)})
        self.assertIn(f"event_embedding:{event_id}", key)

        with patch("raelyn.jobs.enqueue.settings.llm_model", "qwen3.6:35b"):
            key, params = _normalize_dedupe_key_and_params("playlist.backfill_events", {"playlist_id": str(playlist_id), "force": True})
        self.assertEqual(key, f"playlist_event_backfill:{playlist_id}:qwen3.6:35b:force")
        self.assertTrue(params["force"])


if __name__ == "__main__":
    unittest.main()
