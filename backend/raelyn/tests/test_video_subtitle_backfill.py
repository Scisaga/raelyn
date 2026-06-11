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

from raelyn.jobs.handlers.video_download import (
    _target_subtitle_langs,
    video_backfill_subtitles_bilibili,
    video_backfill_subtitles_youtube,
)
from raelyn.models import Job, Media, Video


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    def __init__(self, *, video: Video, media: Media | None = None, existing_subtitle=None) -> None:
        self._video = video
        self._media = media
        self._existing_subtitle = existing_subtitle

    def get(self, model, key):
        if model is Video and key == self._video.id:
            return self._video
        if model is Media and self._media and key == self._media.id:
            return self._media
        return None

    def execute(self, _stmt):
        return _ScalarResult(self._existing_subtitle)


@contextmanager
def _lock():
    yield "youtube:download"


class VideoSubtitleBackfillTests(unittest.TestCase):
    def test_target_subtitle_langs_prefers_chinese_for_cjk_video(self) -> None:
        media = Media(id=uuid.uuid4(), provider="youtube", provider_media_id="m1", url="https://example.test", name="錢線百分百")
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="abc123",
            media_id=media.id,
            url="https://www.youtube.com/watch?v=abc123",
            title="台積電股東常會",
        )

        target, langs = _target_subtitle_langs(video, media)

        self.assertEqual(target, "zh")
        self.assertIn("zh-Hant", langs)
        self.assertNotIn("en", langs)

    def test_target_subtitle_langs_prefers_english_without_cjk_signal(self) -> None:
        media = Media(id=uuid.uuid4(), provider="youtube", provider_media_id="m1", url="https://example.test", name="Bloomberg Television")
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="abc123",
            media_id=media.id,
            url="https://www.youtube.com/watch?v=abc123",
            title="Fed Keeps Rates Unchanged",
        )

        target, langs = _target_subtitle_langs(video, media)

        self.assertEqual(target, "en")
        self.assertEqual(langs, ["en"])

    def test_target_subtitle_langs_uses_bilibili_ai_language_codes(self) -> None:
        media = Media(id=uuid.uuid4(), provider="bilibili", provider_media_id="m1", url="https://example.test", name="贝侃")
        video = Video(
            id=uuid.uuid4(),
            provider="bilibili",
            provider_video_id="BV16UPLzzEUr",
            media_id=media.id,
            url="https://www.bilibili.com/video/BV16UPLzzEUr",
            title="美伊互殴首个交易日",
        )

        target, langs = _target_subtitle_langs(video, media, "zh")

        self.assertEqual(target, "zh")
        self.assertIn("ai-zh", langs)
        self.assertIn("zh-Hant", langs)

    def test_backfill_subtitles_downloads_target_subtitle_and_enqueues_normalize(self) -> None:
        media = Media(id=uuid.uuid4(), provider="youtube", provider_media_id="m1", url="https://example.test", name="錢線百分百")
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="abc123",
            media_id=media.id,
            url="https://www.youtube.com/watch?v=abc123",
            title="台積電股東常會",
        )
        job = Job(
            id=uuid.uuid4(),
            type="video.backfill_subtitles.youtube",
            status="running",
            params={"video_id": str(video.id), "target_language": "auto"},
        )
        session = _FakeSession(video=video, media=media)
        normalize_job_id = uuid.uuid4()

        def download_subtitles(*, out_dir: Path, **_kwargs):
            (out_dir / "abc123.zh-Hant.vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n你好\n", encoding="utf-8")
            return {"automatic_captions": {"zh-Hant": [{"ext": "vtt"}]}, "subtitles": {}}

        with patch("raelyn.jobs.handlers.video_download.advisory_lock_any", return_value=_lock()):
            with patch("raelyn.jobs.handlers.video_download.ytdlp_download_subtitles", side_effect=download_subtitles) as ytdlp:
                with patch("raelyn.jobs.handlers.video_download.ensure_asset") as ensure_asset:
                    with patch("raelyn.jobs.handlers.video_download.enqueue_job", return_value=normalize_job_id) as enqueue_job:
                        result = video_backfill_subtitles_youtube(session, job)

        ytdlp.assert_called_once()
        self.assertEqual(ytdlp.call_args.kwargs["subtitles_langs"], ["zh-Hant", "zh-Hans", "zh-CN", "zh-TW", "zh-HK", "zh"])
        ensure_asset.assert_called_once()
        self.assertEqual(ensure_asset.call_args.kwargs["type_"], "subtitle")
        self.assertEqual(ensure_asset.call_args.kwargs["language"], "zh-Hant")
        self.assertIn("/subtitle/zh-Hant/raw.vtt", ensure_asset.call_args.kwargs["s3_key"])
        enqueue_job.assert_called_once()
        self.assertEqual(enqueue_job.call_args.kwargs["type_"], "video.normalize_subtitle")
        self.assertEqual(result["subtitle_languages"], ["zh-Hant"])
        self.assertEqual(result["normalize_job_id"], str(normalize_job_id))

    def test_backfill_subtitles_downloads_bilibili_ai_zh_subtitle(self) -> None:
        media = Media(id=uuid.uuid4(), provider="bilibili", provider_media_id="m1", url="https://example.test", name="贝侃")
        video = Video(
            id=uuid.uuid4(),
            provider="bilibili",
            provider_video_id="BV16UPLzzEUr",
            media_id=media.id,
            url="https://www.bilibili.com/video/BV16UPLzzEUr",
            title="美伊互殴首个交易日",
        )
        job = Job(
            id=uuid.uuid4(),
            type="video.backfill_subtitles.bilibili",
            status="running",
            params={"video_id": str(video.id), "target_language": "zh"},
        )
        session = _FakeSession(video=video, media=media)
        normalize_job_id = uuid.uuid4()

        def download_subtitles(*, out_dir: Path, **_kwargs):
            (out_dir / "BV16UPLzzEUr.ai-zh.vtt").write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n你好\n", encoding="utf-8")
            return {"subtitles": {"ai-zh": [{"ext": "vtt"}]}, "automatic_captions": {}}

        with patch("raelyn.jobs.handlers.video_download.advisory_lock_any", return_value=_lock()):
            with patch("raelyn.jobs.handlers.video_download.ytdlp_download_subtitles", side_effect=download_subtitles) as ytdlp:
                with patch("raelyn.jobs.handlers.video_download.ensure_asset") as ensure_asset:
                    with patch("raelyn.jobs.handlers.video_download.enqueue_job", return_value=normalize_job_id):
                        result = video_backfill_subtitles_bilibili(session, job)

        self.assertIn("ai-zh", ytdlp.call_args.kwargs["subtitles_langs"])
        self.assertEqual(ensure_asset.call_args.kwargs["language"], "ai-zh")
        self.assertIn("/subtitle/ai-zh/raw.vtt", ensure_asset.call_args.kwargs["s3_key"])
        self.assertEqual(result["subtitle_languages"], ["ai-zh"])


if __name__ == "__main__":
    unittest.main()
