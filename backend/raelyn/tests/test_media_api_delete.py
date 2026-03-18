from __future__ import annotations

import sys
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.api import media as media_api


@contextmanager
def _fake_session_scope(session):
    yield session


class _FakeSession:
    def __init__(self, media, existing_delete_job=None) -> None:
        self.media = media
        self.existing_delete_job = existing_delete_job

    def get(self, model, key):
        if model is media_api.Media and self.media and key == self.media.id:
            return self.media
        return None


class MediaApiDeleteTests(unittest.TestCase):
    def test_delete_media_enqueues_async_delete_job_and_disables_monitor(self) -> None:
        media_id = uuid.uuid4()
        media = SimpleNamespace(id=media_id, provider="youtube", monitor_enabled=True)
        session = _FakeSession(media=media)
        delete_job_id = uuid.uuid4()

        with patch("raelyn.api.media.session_scope", lambda: _fake_session_scope(session)):
            with patch("raelyn.api.media.active_media_delete_job", return_value=None):
                with patch("raelyn.api.media.enqueue_job", return_value=delete_job_id) as enqueue_job:
                    payload = media_api.delete_media(media_id)

        self.assertEqual(payload.status, "accepted")
        self.assertEqual(payload.media_id, media_id)
        self.assertEqual(payload.job_id, delete_job_id)
        self.assertEqual(payload.job_type, "media.delete")
        self.assertFalse(payload.reused)
        self.assertFalse(media.monitor_enabled)
        enqueue_job.assert_called_once()
        self.assertEqual(enqueue_job.call_args.kwargs["type_"], "media.delete")

    def test_delete_media_reuses_existing_active_delete_job(self) -> None:
        media_id = uuid.uuid4()
        media = SimpleNamespace(id=media_id, provider="youtube", monitor_enabled=False)
        existing_job = SimpleNamespace(id=uuid.uuid4())
        session = _FakeSession(media=media, existing_delete_job=existing_job)

        with patch("raelyn.api.media.session_scope", lambda: _fake_session_scope(session)):
            with patch("raelyn.api.media.active_media_delete_job", return_value=existing_job):
                with patch("raelyn.api.media.enqueue_job") as enqueue_job:
                    payload = media_api.delete_media(media_id)

        self.assertTrue(payload.reused)
        self.assertEqual(payload.job_id, existing_job.id)
        enqueue_job.assert_not_called()


if __name__ == "__main__":
    unittest.main()
