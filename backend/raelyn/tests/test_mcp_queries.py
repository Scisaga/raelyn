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
from raelyn.models import Asset, Brief, Media, Playlist, Video


def _scalar_one_or_none(value):
    return Mock(scalar_one_or_none=Mock(return_value=value))


def _first(value):
    return Mock(first=Mock(return_value=value))


def _scalars_all(values):
    scalars = Mock(all=Mock(return_value=list(values)))
    return Mock(scalars=Mock(return_value=scalars))


class McpQueriesTests(unittest.TestCase):
    def test_get_playlist_brief_returns_not_ready_when_brief_missing(self) -> None:
        playlist = Playlist(id=uuid.uuid4(), name="Daily", brief_granularity="day")
        session = Mock()
        session.get.side_effect = lambda model, ident: playlist if ident == playlist.id else None
        session.execute.return_value = _scalar_one_or_none(None)

        with patch("raelyn.mcp.queries.session_scope", return_value=nullcontext(session)):
            result = queries.get_playlist_brief(playlist.id, date="2026-03-10")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "not_ready")
        self.assertEqual(result["playlist_id"], str(playlist.id))
        self.assertEqual(result["period_start"], "2026-03-10")
        self.assertFalse(result["body_readable"])
        self.assertIsNone(result["body_resource_uri"])
        self.assertEqual(result["body_markdown"], "")

    def test_get_brief_reads_by_brief_id_and_reports_body_resource(self) -> None:
        playlist_id = uuid.uuid4()
        brief = Brief(
            id=uuid.uuid4(),
            playlist_id=playlist_id,
            granularity="day",
            period_start=date(2026, 3, 10),
            status="ready",
            markdown_asset_id=uuid.uuid4(),
        )
        asset = Asset(
            id=brief.markdown_asset_id,
            type="brief",
            format="md",
            s3_bucket="bucket",
            s3_key="brief.md",
        )
        session = Mock()
        session.get.side_effect = lambda model, ident: {brief.id: brief, asset.id: asset}.get(ident)

        with patch("raelyn.mcp.queries.session_scope", return_value=nullcontext(session)):
            with patch("raelyn.mcp.queries._read_brief_body", return_value=(True, "# 标题")):
                result = queries.get_brief(brief.id, include_body=False)

        self.assertEqual(result["brief_id"], str(brief.id))
        self.assertTrue(result["body_readable"])
        self.assertEqual(result["body_resource_uri"], f"raelyn://brief/{brief.id}/body")
        self.assertEqual(result["body_mime_type"], "text/markdown")
        self.assertEqual(result["body_markdown"], "")

    def test_get_playlist_brief_resolves_playlist_granularity_from_date(self) -> None:
        playlist = Playlist(id=uuid.uuid4(), name="Weekly", brief_granularity="week")
        brief = Brief(
            id=uuid.uuid4(),
            playlist_id=playlist.id,
            granularity="week",
            period_start=date(2026, 3, 9),
            status="ready",
            markdown_asset_id=uuid.uuid4(),
        )
        asset = Asset(
            id=brief.markdown_asset_id,
            type="brief",
            format="md",
            s3_bucket="bucket",
            s3_key="weekly.md",
        )
        session = Mock()
        session.get.side_effect = lambda model, ident: {playlist.id: playlist, asset.id: asset}.get(ident)
        session.execute.return_value = _scalar_one_or_none(brief)

        with patch("raelyn.mcp.queries.session_scope", return_value=nullcontext(session)):
            with patch("raelyn.mcp.queries._read_brief_body", return_value=(True, "weekly body")):
                result = queries.get_playlist_brief(playlist.id, date=date(2026, 3, 10), include_body=True)

        self.assertEqual(result["granularity"], "week")
        self.assertEqual(result["period_start"], "2026-03-09")
        self.assertEqual(result["period_end"], "2026-03-15")
        self.assertEqual(result["body_resource_uri"], f"raelyn://playlist/{playlist.id}/briefs/by-date/2026-03-10/body")
        self.assertEqual(result["body_markdown"], "weekly body")

    def test_get_playlist_latest_brief_uses_brief_body_uri(self) -> None:
        playlist = Playlist(id=uuid.uuid4(), name="Monthly", brief_granularity="month")
        brief = Brief(
            id=uuid.uuid4(),
            playlist_id=playlist.id,
            granularity="month",
            period_start=date(2026, 3, 1),
            status="ready",
            markdown_asset_id=uuid.uuid4(),
        )
        asset = Asset(
            id=brief.markdown_asset_id,
            type="brief",
            format="md",
            s3_bucket="bucket",
            s3_key="monthly.md",
        )
        session = Mock()
        session.get.side_effect = lambda model, ident: {playlist.id: playlist, asset.id: asset}.get(ident)

        with patch("raelyn.mcp.queries.session_scope", return_value=nullcontext(session)):
            with patch(
                "raelyn.mcp.queries.resolve_latest_brief_target",
                return_value=("month", date(2026, 3, 24), date(2026, 3, 1), date(2026, 3, 31)),
            ):
                with patch("raelyn.mcp.queries._find_brief", return_value=brief):
                    with patch("raelyn.mcp.queries._read_brief_body", return_value=(True, "monthly body")):
                        result = queries.get_playlist_latest_brief(playlist.id, include_body=False)

        self.assertEqual(result["brief_id"], str(brief.id))
        self.assertEqual(result["body_resource_uri"], f"raelyn://brief/{brief.id}/body")
        self.assertEqual(result["body_markdown"], "")

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

    def test_get_playlist_summary_counts_ready_assets_and_embeds_transcript(self) -> None:
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
        videos = [{"id": str(video_a), "title": "A"}, {"id": str(video_b), "title": "B"}]

        with patch("raelyn.mcp.queries.session_scope", return_value=nullcontext(session)):
            with patch(
                "raelyn.mcp.queries.resolve_playlist_brief_target",
                return_value=(playlist, "day", date(2026, 3, 10), date(2026, 3, 10)),
            ):
                with patch("raelyn.mcp.queries._playlist_videos", return_value=videos):
                    with patch("raelyn.mcp.queries._playlist_summary_payload", return_value={"id": str(playlist.id), "name": "Daily"}):
                        with patch("raelyn.mcp.queries.build_brief_payload", return_value={"ok": False, "status": "not_ready"}):
                            with patch("raelyn.mcp.queries.pick_transcript_asset", side_effect=[transcript_asset, None]):
                                with patch("raelyn.mcp.queries._pick_note_asset", side_effect=[note_asset, note_asset]):
                                    with patch(
                                        "raelyn.mcp.queries._build_transcript_chunk_payload",
                                        return_value={"ok": True, "status": "ready", "text": "chunk"},
                                    ):
                                        result = queries.get_playlist_summary(
                                            playlist.id,
                                            date=date(2026, 3, 10),
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
