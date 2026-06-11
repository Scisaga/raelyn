from __future__ import annotations

import sys
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.api.video_assets import list_video_assets
from raelyn.models import Media, Video


class _FirstResult:
    def __init__(self, value) -> None:
        self._value = value

    def first(self):
        return self._value


class _ScalarResult:
    def __init__(self, values) -> None:
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


@contextmanager
def _fake_session_scope(session):
    yield session


class VideoAssetsApiTests(unittest.TestCase):
    def test_list_video_assets_default_does_not_localize_youtube_title(self) -> None:
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="abc123",
            media_id=uuid.uuid4(),
            url="https://www.youtube.com/watch?v=abc123",
            title="English title",
            status="discovered",
        )
        media = Media(
            id=video.media_id,
            provider="youtube",
            provider_media_id="@demo",
            url="https://www.youtube.com/@demo",
            name="Demo",
        )
        session = Mock()
        session.execute.side_effect = [_FirstResult((video, media)), _ScalarResult([])]

        with patch("raelyn.api.video_assets.session_scope", lambda: _fake_session_scope(session)):
            with patch("raelyn.api.video_assets._maybe_localize_youtube_title") as localize:
                items = list_video_assets(video.id)

        self.assertEqual(items, [])
        localize.assert_not_called()

    def test_list_video_assets_can_opt_in_to_localize_youtube_title(self) -> None:
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="abc123",
            media_id=uuid.uuid4(),
            url="https://www.youtube.com/watch?v=abc123",
            title="English title",
            status="discovered",
        )
        media = Media(
            id=video.media_id,
            provider="youtube",
            provider_media_id="@demo",
            url="https://www.youtube.com/@demo",
            name="Demo",
        )
        session = Mock()
        session.execute.side_effect = [_FirstResult((video, media)), _ScalarResult([])]

        with patch("raelyn.api.video_assets.session_scope", lambda: _fake_session_scope(session)):
            with patch("raelyn.api.video_assets._maybe_localize_youtube_title") as localize:
                items = list_video_assets(video.id, localize_title=True)

        self.assertEqual(items, [])
        localize.assert_called_once_with(video, socket_timeout_seconds=3)


if __name__ == "__main__":
    unittest.main()
