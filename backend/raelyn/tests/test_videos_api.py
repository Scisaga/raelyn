from __future__ import annotations

import importlib
import sys
import unittest
import uuid
from contextlib import ExitStack
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn import config
from raelyn.api.videos import _video_domain_scope_condition, _video_list_timeline_columns
from raelyn.models import Media, Video


@contextmanager
def _fake_session_scope_with_video(video_id: uuid.UUID):
    session = Mock()
    session.get.side_effect = lambda model, value: SimpleNamespace(id=value) if value == video_id else None
    yield session


def _load_app(stack: ExitStack):
    stack.enter_context(patch.object(config.settings, "api_bearer_token", ""))
    stack.enter_context(patch("raelyn.db.init_db"))
    stack.enter_context(patch("raelyn.services.s3.s3_ensure_bucket"))
    stack.enter_context(patch("raelyn.recover_orphan_jobs.recover"))
    sys.modules.pop("raelyn.main", None)
    module = importlib.import_module("raelyn.main")
    return module.app


class VideosApiTests(unittest.IsolatedAsyncioTestCase):
    def test_video_domain_scope_uses_playlist_media_membership(self) -> None:
        domain_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
        stmt = select(Video.id).where(_video_domain_scope_condition(domain_id))
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()

        self.assertIn("playlist_media.media_id", compiled)
        self.assertIn("playlist_media.playlist_id", compiled)
        self.assertIn(str(domain_id), compiled)

    def test_video_list_content_timeline_uses_set_based_join(self) -> None:
        selected_time, content_ts, timeline_ts, time_source, time_status, time_confidence = (
            _video_list_timeline_columns("content")
        )
        stmt = (
            select(Video.id, Media.id, content_ts, timeline_ts, time_source, time_status, time_confidence)
            .join(Media, Media.id == Video.media_id)
            .outerjoin(selected_time, selected_time.c.video_id == Video.id)
            .where(timeline_ts.is_not(None))
            .order_by(timeline_ts.desc().nullslast(), Video.created_at.desc(), Video.id.desc())
            .limit(20)
        )
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()

        self.assertIn("left outer join", compiled)
        self.assertIn("row_number() over", compiled)
        self.assertIn("partition by video_time_evidence.video_id", compiled)
        self.assertNotIn("video_time_evidence.video_id = video.id", compiled)

    def test_video_list_platform_timeline_keeps_fast_base_query(self) -> None:
        selected_time, content_ts, timeline_ts, time_source, time_status, time_confidence = (
            _video_list_timeline_columns("platform")
        )
        stmt = (
            select(Video.id, Media.id, content_ts, timeline_ts, time_source, time_status, time_confidence)
            .join(Media, Media.id == Video.media_id)
            .where(timeline_ts.is_not(None))
            .order_by(timeline_ts.desc().nullslast(), Video.created_at.desc(), Video.id.desc())
            .limit(20)
        )
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()

        self.assertIsNone(selected_time)
        self.assertIn("video.published_at", compiled)
        self.assertNotIn("video_time_evidence", compiled)

    async def test_transcript_endpoint_passes_variant_and_source(self) -> None:
        video_id = uuid.uuid4()
        with ExitStack() as stack:
            app = _load_app(stack)
            payload = {"ok": True, "text": "hello", "variant": "plain", "source": "qwen3-asr"}
            build_mock = Mock(return_value=payload)
            stack.enter_context(patch("raelyn.api.videos.session_scope", lambda: _fake_session_scope_with_video(video_id)))
            stack.enter_context(patch("raelyn.api.videos.build_transcript_payload", build_mock))
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                resp = await client.get(f"/api/videos/{video_id}/transcript?variant=plain&source=qwen3-asr")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), payload)
        build_mock.assert_called_once_with(
            ANY,
            video_id,
            max_chars=200_000,
            variant="plain",
            source="qwen3-asr",
        )

    async def test_transcript_endpoint_rejects_invalid_variant(self) -> None:
        video_id = uuid.uuid4()
        with ExitStack() as stack:
            app = _load_app(stack)
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                resp = await client.get(f"/api/videos/{video_id}/transcript?variant=raw")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"detail": "variant must be one of: plain, polished"})


if __name__ == "__main__":
    unittest.main()
