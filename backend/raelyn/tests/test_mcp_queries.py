from __future__ import annotations

import sys
import unittest
import uuid
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.mcp import queries
from raelyn.models import Asset, Media, Playlist, Video


def _scalar_one_or_none(value):
    return Mock(scalar_one_or_none=Mock(return_value=value))


def _first(value):
    return Mock(first=Mock(return_value=value))


def _scalars_all(values):
    scalars = Mock(all=Mock(return_value=list(values)))
    return Mock(scalars=Mock(return_value=scalars))


class McpQueriesTests(unittest.TestCase):
    def test_get_brief_returns_not_ready_when_brief_missing(self) -> None:
        playlist = Playlist(id=uuid.uuid4(), name="Daily", brief_granularity="day")
        session = Mock()
        session.get.return_value = playlist
        session.execute.return_value = _scalar_one_or_none(None)

        with patch("raelyn.mcp.queries.session_scope", return_value=nullcontext(session)):
            result = queries.get_brief(playlist.id, granularity="day", date_in_period="2026-03-10")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "not_ready")
        self.assertEqual(result["playlist_id"], str(playlist.id))
        self.assertEqual(result["markdown"], "")

    def test_get_video_context_returns_joined_context_payload(self) -> None:
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="channel-a",
            url="https://example.com/channel-a",
            monitor_enabled=True,
            name="Channel A",
        )
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="vid-1",
            media_id=media.id,
            url="https://example.com/watch?v=vid-1",
            title="Demo",
            status="ready",
        )
        asset = Asset(
            id=uuid.uuid4(),
            video_id=video.id,
            type="transcript",
            format="txt",
            language="zh",
            source="subtitle",
            variant="polished",
            s3_bucket="bucket",
            s3_key="demo.txt",
        )
        session = Mock()
        session.execute.side_effect = [
            _first((video, media)),
            _scalars_all([asset]),
        ]

        with patch("raelyn.mcp.queries.session_scope", return_value=nullcontext(session)):
            with patch("raelyn.mcp.queries._video_payload", return_value={"id": str(video.id), "title": "Demo"}):
                with patch("raelyn.mcp.queries._media_payload", return_value={"id": str(media.id), "name": "Channel A"}):
                    with patch("raelyn.mcp.queries._asset_payload", return_value={"id": str(asset.id), "type": "transcript"}):
                        with patch("raelyn.mcp.queries._video_transcript_payload", return_value={"ok": True, "status": "ready", "text": "hello"}):
                            with patch("raelyn.mcp.queries._note_payload", return_value={"ok": False, "status": "not_ready", "text": ""}):
                                result = queries.get_video_context(video.id)

        self.assertEqual(result["video"]["id"], str(video.id))
        self.assertEqual(result["media"]["id"], str(media.id))
        self.assertEqual(result["assets"], [{"id": str(asset.id), "type": "transcript"}])
        self.assertEqual(result["transcript"]["status"], "ready")
        self.assertEqual(result["note"]["status"], "not_ready")

    def test_get_playlist_context_counts_ready_assets_and_embeds_transcript(self) -> None:
        playlist = Playlist(id=uuid.uuid4(), name="Daily", brief_granularity="day")
        video_a = uuid.uuid4()
        video_b = uuid.uuid4()
        transcript_asset = Asset(
            id=uuid.uuid4(),
            video_id=video_a,
            type="transcript",
            format="txt",
            language="zh",
            source="subtitle",
            variant="polished",
            s3_bucket="bucket",
            s3_key="a.txt",
        )
        note_asset = Asset(
            id=uuid.uuid4(),
            video_id=video_a,
            type="note",
            format="md",
            language="zh",
            source="llm",
            variant="summary",
            s3_bucket="bucket",
            s3_key="note.md",
        )
        session = Mock()
        session.get.return_value = playlist
        videos = [{"id": str(video_a), "title": "A"}, {"id": str(video_b), "title": "B"}]

        with patch("raelyn.mcp.queries.session_scope", return_value=nullcontext(session)):
            with patch("raelyn.mcp.queries._playlist_videos", return_value=videos):
                with patch("raelyn.mcp.queries._playlist_summary_payload", return_value={"id": str(playlist.id), "name": "Daily"}):
                    with patch("raelyn.mcp.queries._brief_payload", return_value={"ok": False, "status": "not_ready"}):
                        with patch("raelyn.mcp.queries.pick_transcript_asset", side_effect=[transcript_asset, None]):
                            with patch("raelyn.mcp.queries._pick_note_asset", side_effect=[note_asset, note_asset]):
                                with patch(
                                    "raelyn.mcp.queries._build_transcript_chunk_payload",
                                    return_value={"ok": True, "status": "ready", "text": "chunk"},
                                ):
                                    result = queries.get_playlist_context(
                                        playlist.id,
                                        granularity="day",
                                        date_in_period=date(2026, 3, 10),
                                        include_transcript=True,
                                        limit=50,
                                    )

        self.assertEqual(result["playlist"]["id"], str(playlist.id))
        self.assertEqual(result["video_count"], 2)
        self.assertEqual(result["transcript_ready_count"], 1)
        self.assertEqual(result["note_ready_count"], 2)
        self.assertEqual(result["videos"][0]["transcript_status"], "ready")
        self.assertEqual(result["videos"][0]["transcript"]["text"], "chunk")
        self.assertEqual(result["videos"][1]["transcript_status"], "not_ready")
        self.assertNotIn("transcript", result["videos"][1])


if __name__ == "__main__":
    unittest.main()
