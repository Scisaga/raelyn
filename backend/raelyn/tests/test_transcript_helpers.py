from __future__ import annotations

import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.mcp.chunking import build_chunk_bounds, get_text_chunk, normalize_chunk_size
from raelyn.models import Asset
from raelyn.services.transcripts import build_transcript_payload, pick_transcript_asset


def _scalar_one_or_none(value):
    return Mock(scalar_one_or_none=Mock(return_value=value))


class TranscriptHelperTests(unittest.TestCase):
    def test_pick_transcript_asset_prefers_priority_sources(self) -> None:
        preferred = Asset(
            id=uuid.uuid4(),
            video_id=uuid.uuid4(),
            type="transcript",
            format="txt",
            language="zh",
            source="subtitle",
            variant="polished",
            s3_bucket="bucket",
            s3_key="preferred.txt",
            created_at=datetime(2026, 3, 10, tzinfo=timezone.utc),
        )
        fallback = Asset(
            id=uuid.uuid4(),
            video_id=preferred.video_id,
            type="transcript",
            format="txt",
            language="en",
            source="qwen3-asr",
            variant="plain",
            s3_bucket="bucket",
            s3_key="fallback.txt",
            created_at=datetime(2026, 3, 9, tzinfo=timezone.utc),
        )
        session = Mock()
        session.execute.side_effect = [
            _scalar_one_or_none(preferred),
            _scalar_one_or_none(fallback),
        ]

        result = pick_transcript_asset(session, preferred.video_id)

        self.assertIs(result, preferred)
        self.assertEqual(session.execute.call_count, 1)

    def test_pick_transcript_asset_falls_back_to_latest_variant(self) -> None:
        latest = Asset(
            id=uuid.uuid4(),
            video_id=uuid.uuid4(),
            type="transcript",
            format="txt",
            language="en",
            source="other",
            variant="polished",
            s3_bucket="bucket",
            s3_key="latest.txt",
            created_at=datetime(2026, 3, 10, tzinfo=timezone.utc),
        )
        session = Mock()
        session.execute.side_effect = [
            _scalar_one_or_none(None),
            _scalar_one_or_none(None),
            _scalar_one_or_none(None),
            _scalar_one_or_none(None),
            _scalar_one_or_none(None),
            _scalar_one_or_none(None),
            _scalar_one_or_none(latest),
        ]

        result = pick_transcript_asset(session, latest.video_id)

        self.assertIs(result, latest)
        self.assertEqual(session.execute.call_count, 7)

    def test_pick_transcript_asset_respects_requested_variant_and_source(self) -> None:
        exact = Asset(
            id=uuid.uuid4(),
            video_id=uuid.uuid4(),
            type="transcript",
            format="txt",
            language="zh",
            source="qwen3-asr",
            variant="plain",
            s3_bucket="bucket",
            s3_key="plain.txt",
            created_at=datetime(2026, 3, 10, tzinfo=timezone.utc),
        )
        session = Mock()
        session.execute.side_effect = [_scalar_one_or_none(exact)]

        result = pick_transcript_asset(session, exact.video_id, variant="plain", source="qwen3-asr")

        self.assertIs(result, exact)
        self.assertEqual(session.execute.call_count, 1)

    def test_build_transcript_payload_returns_requested_selector_when_missing(self) -> None:
        session = Mock()
        session.execute.side_effect = [_scalar_one_or_none(None), _scalar_one_or_none(None)]

        payload = build_transcript_payload(
            session,
            uuid.uuid4(),
            variant="plain",
            source="qwen3-asr",
        )

        self.assertEqual(
            payload,
            {
                "ok": False,
                "reason": "no transcript",
                "text": "",
                "variant": "plain",
                "source": "qwen3-asr",
            },
        )

    def test_build_chunk_bounds_prefers_newline_near_chunk_end(self) -> None:
        text = ("a" * 8000) + "\n" + ("b" * 8000)

        bounds = build_chunk_bounds(text, 10_000)

        self.assertEqual(bounds[0], (0, 8001))
        self.assertEqual(bounds[1], (8001, len(text)))

    def test_get_text_chunk_reports_next_chunk(self) -> None:
        text = ("x" * 7000) + "\n" + ("y" * 7000)

        chunk = get_text_chunk(text, chunk_index=0, chunk_size=10_000)

        self.assertTrue(chunk["has_more"])
        self.assertEqual(chunk["next_chunk_index"], 1)
        self.assertEqual(chunk["chunk_count"], 2)

    def test_get_text_chunk_raises_for_out_of_range_chunk(self) -> None:
        with self.assertRaises(IndexError):
            get_text_chunk("hello", chunk_index=2, chunk_size=4)

    def test_normalize_chunk_size_rejects_invalid_values(self) -> None:
        with self.assertRaises(ValueError):
            normalize_chunk_size(0)
        with self.assertRaises(ValueError):
            normalize_chunk_size(50_001)


if __name__ == "__main__":
    unittest.main()
