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

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn import config


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
