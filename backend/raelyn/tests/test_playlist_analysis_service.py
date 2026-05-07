from __future__ import annotations

import sys
import unittest
import uuid
from collections import Counter
from datetime import date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy.dialects import postgresql

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.services.playlist_analysis import (
    AnalysisEmbeddingItem,
    BoundaryDetection,
    EmbeddingBackfillBatchResult,
    EmbeddingBackfillBatchWrite,
    EmbeddingBackfillFetchResult,
    EmbeddingBackfillItem,
    EmbeddingBackfillTranscriptCandidate,
    OpenTopicBurstDetection,
    OpenTopicBurstItem,
    CoverageStats,
    _candidate_top_terms,
    _iter_playlist_backfill_candidates,
    _prepare_backfill_batch_result,
    _limit_open_topic_burst_detections_by_year,
    _select_open_topic_burst_detections,
    _select_month_boundaries,
    _upsert_video_embedding,
    backfill_playlist_embeddings,
    ensure_analysis_memory_budget,
    ensure_analysis_resource_budget,
    fetch_plain_transcript_for_embedding,
    next_china_trading_day,
    build_playlist_analysis_snapshot,
    pending_playlist_analysis_job,
    playlist_coverage_stats,
    prune_playlist_analysis_runs,
    request_playlist_analysis_rebuild,
    transcript_checksum,
    video_embedding_needs_refresh,
)
from raelyn.services.embeddings import EmbeddingOverBudgetError, EmbeddingTransientError
from raelyn.jobs.reschedule import JobTerminalFailure
from raelyn.models import PlaylistAnalysisCandidate, PlaylistAnalysisPeriod, PlaylistAnalysisRun, PlaylistAnalysisSignal, PlaylistAnalysisState


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class _AllResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class _ScalarsResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _AllResult(self._values)


class _FakeSnapshotSession:
    def __init__(self):
        self.state = None
        self.added = []
        self.execute_calls = []

    def get(self, model, key):
        if model is PlaylistAnalysisState:
            return self.state
        return None

    def add(self, value):
        if isinstance(value, PlaylistAnalysisState):
            self.state = value
        self.added.append(value)

    def flush(self, values=None):
        for value in values or []:
            if hasattr(value, "id") and getattr(value, "id", None) is None:
                value.id = uuid.uuid4()
        return None

    def execute(self, statement):
        self.execute_calls.append(statement)
        return Mock(all=Mock(return_value=[]))


def _analysis_item(
    day_offset: int,
    vector: list[float],
    *,
    title: str | None = None,
    media_name: str = "media",
) -> AnalysisEmbeddingItem:
    published_at = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc) + timedelta(days=day_offset)
    return AnalysisEmbeddingItem(
        video_id=uuid.uuid4(),
        media_id=uuid.uuid4(),
        title=title or f"video-{day_offset}",
        media_name=media_name,
        published_at=published_at,
        vector=vector,
    )


def _topic_item(
    day_offset: int,
    vector: list[float],
    *,
    title: str,
    media_name: str,
) -> OpenTopicBurstItem:
    item = _analysis_item(day_offset, vector, title=title, media_name=media_name)
    return OpenTopicBurstItem(
        video_id=item.video_id,
        media_id=item.media_id,
        title=item.title,
        media_name=item.media_name,
        published_at=item.published_at,
        day=item.published_at.date(),
        vector=vector,
    )


def _topic_items_by_day(items: list[OpenTopicBurstItem]) -> dict[date, list[OpenTopicBurstItem]]:
    result: dict[date, list[OpenTopicBurstItem]] = {}
    for item in items:
        result.setdefault(item.day, []).append(item)
    return result


def _burst_detection(
    candidate_date: date,
    *,
    score: float,
    title: str,
) -> OpenTopicBurstDetection:
    return OpenTopicBurstDetection(
        candidate_date=candidate_date,
        event_start=candidate_date,
        event_end=candidate_date,
        video_count=8,
        media_count=3,
        active_days=2,
        score=score,
        confidence=0.8,
        cohesion=0.8,
        centroid=[1.0, 0.0],
        representative_title=title,
        top_terms=[],
        count_by_day=Counter({candidate_date: 8}),
    )


class PlaylistAnalysisServiceTests(unittest.TestCase):
    def test_next_china_trading_day_skips_weekend(self) -> None:
        self.assertEqual(next_china_trading_day(date(2026, 4, 24)), date(2026, 4, 27))
        self.assertEqual(next_china_trading_day(date(2026, 4, 22)), date(2026, 4, 23))

    def test_transcript_checksum_is_stable(self) -> None:
        self.assertEqual(transcript_checksum("hello"), transcript_checksum("hello"))
        self.assertNotEqual(transcript_checksum("hello"), transcript_checksum("world"))

    def test_video_embedding_needs_refresh_for_missing_or_stale_rows(self) -> None:
        self.assertTrue(video_embedding_needs_refresh(None, text_checksum_value="a"))
        self.assertTrue(
            video_embedding_needs_refresh(
                SimpleNamespace(status="ready", text_checksum="old", vector=[1.0]),
                text_checksum_value="new",
            )
        )
        self.assertTrue(
            video_embedding_needs_refresh(
                SimpleNamespace(status="failed", text_checksum="a", vector=None),
                text_checksum_value="a",
            )
        )
        self.assertFalse(
            video_embedding_needs_refresh(
                SimpleNamespace(status="ready", text_checksum="a", vector=[1.0]),
                text_checksum_value="a",
            )
        )

    def test_upsert_video_embedding_sets_checksum_before_first_flush(self) -> None:
        session = Mock()
        session.execute.return_value = Mock(scalar_one_or_none=Mock(return_value=None))

        def assert_checksum_present(values=None):
            target = values[0] if values else None
            self.assertEqual(target.text_checksum, "checksum")

        session.flush.side_effect = assert_checksum_present

        embedding = _upsert_video_embedding(
            session,
            video_id=uuid.uuid4(),
            text_checksum_value="checksum",
            status="ready",
            vector=[1.0],
        )

        self.assertEqual(embedding.text_checksum, "checksum")
        self.assertEqual(embedding.status, "ready")
        session.flush.assert_called_once()

    def test_pending_playlist_analysis_job_includes_running_jobs(self) -> None:
        session = Mock()
        session.execute.return_value = Mock(scalar_one_or_none=Mock(return_value=None))

        pending_playlist_analysis_job(session, uuid.uuid4())

        statement = session.execute.call_args.args[0]
        compiled = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()
        self.assertIn("job.status in ('pending', 'running')", compiled)

    def test_request_playlist_analysis_rebuild_reuses_running_job(self) -> None:
        session = Mock()
        playlist_id = uuid.uuid4()
        job_id = uuid.uuid4()
        state = SimpleNamespace(last_requested_at=None)
        running_job = SimpleNamespace(id=job_id, status="running")

        with patch("raelyn.services.playlist_analysis.ensure_playlist_analysis_state", return_value=state):
            with patch("raelyn.services.playlist_analysis.active_playlist_analysis_run", return_value=None):
                with patch("raelyn.services.playlist_analysis.pending_playlist_analysis_job", return_value=running_job):
                    with patch("raelyn.services.playlist_analysis.enqueue_job") as enqueue_job:
                        result = request_playlist_analysis_rebuild(session, playlist_id=playlist_id)

        self.assertEqual(result, (job_id, False))
        enqueue_job.assert_not_called()
        self.assertIsNotNone(state.last_requested_at)

    def test_fetch_plain_transcript_for_embedding_skips_empty_text(self) -> None:
        with patch("raelyn.services.playlist_analysis.pick_transcript_asset", return_value=object()):
            with patch("raelyn.services.playlist_analysis.read_text_asset", return_value=("   \n", None)):
                self.assertIsNone(fetch_plain_transcript_for_embedding(Mock(), uuid.uuid4()))

    def test_backfill_playlist_embeddings_streams_candidates(self) -> None:
        playlist_id = uuid.uuid4()
        candidates = [
            EmbeddingBackfillTranscriptCandidate(
                video_id=uuid.uuid4(),
                embedding_status=None,
                text_checksum=None,
                has_vector=False,
                s3_bucket="bucket",
                s3_key="a",
            ),
            EmbeddingBackfillTranscriptCandidate(
                video_id=uuid.uuid4(),
                embedding_status=None,
                text_checksum=None,
                has_vector=False,
                s3_bucket="bucket",
                s3_key="b",
            ),
            EmbeddingBackfillTranscriptCandidate(
                video_id=uuid.uuid4(),
                embedding_status="failed",
                text_checksum="old",
                has_vector=False,
                s3_bucket="bucket",
                s3_key="c",
            ),
        ]

        def transcript_result(candidate, *, force):
            if candidate.video_id == candidates[1].video_id:
                return EmbeddingBackfillFetchResult(item=None, skipped_empty=True)
            return EmbeddingBackfillFetchResult(
                item=EmbeddingBackfillItem(video_id=candidate.video_id, text="needs embedding", checksum="new")
            )

        def batch_result(items):
            writes = tuple(
                EmbeddingBackfillBatchWrite(item=item, status="ready", vector=[1.0], skip_reason=None)
                for item in items
            )
            return EmbeddingBackfillBatchResult(
                selected=len(items),
                embedded=len(items),
                skipped_over_budget=0,
                writes=writes,
            )

        with patch("raelyn.services.playlist_analysis._playlist_backfill_items", side_effect=AssertionError("full loader called")):
            with patch("raelyn.services.playlist_analysis._iter_playlist_backfill_candidates", return_value=iter(candidates)):
                with patch("raelyn.services.playlist_analysis._read_backfill_transcript_item", side_effect=transcript_result):
                    with patch("raelyn.services.playlist_analysis._prepare_backfill_batch_result", side_effect=batch_result) as prepare_batch:
                        with patch("raelyn.services.playlist_analysis._upsert_video_embedding"):
                            with patch("raelyn.services.playlist_analysis.mark_playlist_analysis_dirty") as mark_dirty:
                                result = backfill_playlist_embeddings(Mock(), playlist_id=playlist_id, batch_size=8)

        self.assertEqual(result["selected"], 2)
        self.assertEqual(result["embedded"], 2)
        self.assertEqual(result["skipped_empty"], 1)
        prepare_batch.assert_called_once()
        self.assertEqual(len(prepare_batch.call_args.args[0]), 2)
        mark_dirty.assert_called_once()

    def test_backfill_playlist_embeddings_commits_successful_batches_before_transient_failure(self) -> None:
        playlist_id = uuid.uuid4()
        candidates = [
            EmbeddingBackfillTranscriptCandidate(
                video_id=uuid.uuid4(),
                embedding_status=None,
                text_checksum=None,
                has_vector=False,
                s3_bucket="bucket",
                s3_key="a",
            ),
            EmbeddingBackfillTranscriptCandidate(
                video_id=uuid.uuid4(),
                embedding_status=None,
                text_checksum=None,
                has_vector=False,
                s3_bucket="bucket",
                s3_key="b",
            ),
        ]
        job = SimpleNamespace(
            progress_total=None,
            progress_current=0,
            result=None,
            cancel_requested_at=None,
        )
        session = Mock()

        def transcript_result(candidate, *, force):
            return EmbeddingBackfillFetchResult(
                item=EmbeddingBackfillItem(video_id=candidate.video_id, text="needs embedding", checksum="new")
            )

        def success_result(items):
            writes = tuple(
                EmbeddingBackfillBatchWrite(item=item, status="ready", vector=[1.0], skip_reason=None)
                for item in items
            )
            return EmbeddingBackfillBatchResult(
                selected=len(items),
                embedded=len(items),
                skipped_over_budget=0,
                writes=writes,
            )

        with patch("raelyn.services.playlist_analysis._iter_playlist_backfill_candidates", return_value=iter(candidates)):
            with patch("raelyn.services.playlist_analysis._read_backfill_transcript_item", side_effect=transcript_result):
                with patch(
                    "raelyn.services.playlist_analysis._prepare_backfill_batch_result",
                    side_effect=[
                        success_result([EmbeddingBackfillItem(video_id=candidates[0].video_id, text="needs embedding", checksum="new")]),
                        EmbeddingTransientError("http 502"),
                    ],
                ):
                    with patch("raelyn.services.playlist_analysis._upsert_video_embedding"):
                        with patch("raelyn.services.playlist_analysis.mark_playlist_analysis_dirty"):
                            with self.assertRaises(EmbeddingTransientError):
                                backfill_playlist_embeddings(session, playlist_id=playlist_id, batch_size=1, job=job)

        self.assertGreaterEqual(session.commit.call_count, 2)
        self.assertEqual(job.progress_current, 2)
        self.assertIsInstance(job.result, dict)
        self.assertEqual(job.result.get("embedded"), 1)
        self.assertEqual(job.result.get("remote_errors"), 1)

    def test_backfill_playlist_embeddings_splits_batches_by_character_budget(self) -> None:
        playlist_id = uuid.uuid4()
        candidates = [
            EmbeddingBackfillTranscriptCandidate(
                video_id=uuid.uuid4(),
                embedding_status=None,
                text_checksum=None,
                has_vector=False,
                s3_bucket="bucket",
                s3_key=str(index),
            )
            for index in range(3)
        ]
        batch_lengths: list[int] = []

        def transcript_result(candidate, *, force):
            return EmbeddingBackfillFetchResult(
                item=EmbeddingBackfillItem(video_id=candidate.video_id, text="abcd", checksum="new")
            )

        def batch_result(items):
            batch_lengths.append(len(items))
            writes = tuple(
                EmbeddingBackfillBatchWrite(item=item, status="ready", vector=[1.0], skip_reason=None)
                for item in items
            )
            return EmbeddingBackfillBatchResult(
                selected=len(items),
                embedded=len(items),
                skipped_over_budget=0,
                writes=writes,
            )

        with patch("raelyn.services.playlist_analysis._embedding_batch_max_chars", return_value=8):
            with patch("raelyn.services.playlist_analysis._iter_playlist_backfill_candidates", return_value=iter(candidates)):
                with patch("raelyn.services.playlist_analysis._read_backfill_transcript_item", side_effect=transcript_result):
                    with patch("raelyn.services.playlist_analysis._prepare_backfill_batch_result", side_effect=batch_result):
                        with patch("raelyn.services.playlist_analysis._upsert_video_embedding"):
                            with patch("raelyn.services.playlist_analysis.mark_playlist_analysis_dirty"):
                                result = backfill_playlist_embeddings(Mock(), playlist_id=playlist_id, batch_size=10)

        self.assertEqual(result["embedded"], 3)
        self.assertEqual(sorted(batch_lengths), [1, 2])
        self.assertEqual(result["batch_max_chars"], 8)

    def test_backfill_playlist_embeddings_preserves_previous_committed_progress(self) -> None:
        playlist_id = uuid.uuid4()
        session = Mock()
        job = SimpleNamespace(
            progress_total=None,
            progress_current=0,
            result={
                "selected": 5,
                "embedded": 5,
                "skipped_over_budget": 1,
                "embedding_batches": 2,
                "remote_errors": 1,
                "last_committed_video_id": str(uuid.uuid4()),
            },
            cancel_requested_at=None,
        )

        with patch("raelyn.services.playlist_analysis._iter_playlist_backfill_candidates", return_value=iter(())):
            result = backfill_playlist_embeddings(session, playlist_id=playlist_id, batch_size=8, job=job)

        self.assertTrue(result["ok"])
        self.assertEqual(result["selected"], 5)
        self.assertEqual(result["embedded"], 5)
        self.assertEqual(result["skipped_over_budget"], 1)
        self.assertEqual(result["embedding_batches"], 2)
        self.assertEqual(result["remote_errors"], 1)
        self.assertEqual(job.result["embedded"], 5)

    def test_prepare_backfill_batch_result_marks_single_over_budget(self) -> None:
        item = EmbeddingBackfillItem(video_id=uuid.uuid4(), text="too long", checksum="checksum")

        with patch("raelyn.services.playlist_analysis.embed_texts", side_effect=EmbeddingOverBudgetError("too long")):
            result = _prepare_backfill_batch_result([item])

        self.assertEqual(result.selected, 1)
        self.assertEqual(result.embedded, 0)
        self.assertEqual(result.skipped_over_budget, 1)
        self.assertEqual(result.writes[0].status, "skipped_over_budget")
        self.assertEqual(result.writes[0].skip_reason, "too long")

    def test_iter_playlist_backfill_candidates_filters_ready_rows_in_missing_mode(self) -> None:
        session = Mock()
        session.execute.return_value = _AllResult([])

        list(_iter_playlist_backfill_candidates(session, uuid.uuid4(), force=False))

        statement = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect())).lower()
        self.assertIn("asset", statement)
        self.assertIn("video_embedding.id IS NULL".lower(), statement)
        self.assertIn("video_embedding.status !=", statement)
        self.assertIn("video_embedding.vector IS NULL".lower(), statement)
        self.assertIn("ORDER BY video.id ASC".lower(), statement)

    def test_playlist_coverage_stats_uses_database_aggregate(self) -> None:
        playlist_id = uuid.uuid4()
        media_ids = [uuid.uuid4(), uuid.uuid4()]
        session = Mock()
        session.execute.side_effect = [
            _ScalarsResult(media_ids),
            _ScalarResult(180759),
            _AllResult([("ready", 3095), ("skipped", 1), ("skipped_over_budget", 1), ("failed", 96)]),
        ]

        stats = playlist_coverage_stats(session, playlist_id)

        self.assertEqual(stats.video_total, 180759)
        self.assertEqual(stats.video_embedded, 3095)
        self.assertEqual(stats.video_skipped, 2)
        self.assertEqual(stats.video_failed, 96)
        self.assertEqual(session.execute.call_count, 3)

    def test_analysis_memory_budget_raises_terminal_failure_below_threshold(self) -> None:
        minimum = 1024 * 1024 * 1024
        with patch("raelyn.services.playlist_analysis.settings.analysis_min_available_memory_bytes", minimum):
            with patch("raelyn.services.playlist_analysis._available_memory_bytes", return_value=minimum - 1):
                with self.assertRaises(JobTerminalFailure) as ctx:
                    ensure_analysis_memory_budget()

        self.assertIn("available memory", str(ctx.exception))

    def test_analysis_memory_budget_allows_disabled_threshold(self) -> None:
        with patch("raelyn.services.playlist_analysis.settings.analysis_min_available_memory_bytes", 0):
            with patch("raelyn.services.playlist_analysis._available_memory_bytes", return_value=1):
                ensure_analysis_memory_budget()

    def test_analysis_resource_budget_raises_terminal_failure_above_rss_limit(self) -> None:
        maximum = 6 * 1024 * 1024 * 1024
        with patch("raelyn.services.playlist_analysis.settings.analysis_max_rss_bytes", maximum):
            with patch("raelyn.services.playlist_analysis.settings.analysis_min_available_memory_bytes", 0):
                with patch("raelyn.services.playlist_analysis._resident_memory_bytes", return_value=maximum + 1):
                    with self.assertRaises(JobTerminalFailure) as ctx:
                        ensure_analysis_resource_budget()

        self.assertIn("resident memory", str(ctx.exception))

    def test_prune_playlist_analysis_runs_keeps_current_and_active_runs(self) -> None:
        playlist_id = uuid.uuid4()
        keep_run_id = uuid.uuid4()
        session = Mock()
        session.execute.return_value = SimpleNamespace(rowcount=3)

        deleted = prune_playlist_analysis_runs(session, playlist_id, keep_run_ids=[keep_run_id])

        self.assertEqual(deleted, 3)
        statement = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect())).lower()
        self.assertIn("delete from playlist_analysis_run", statement)
        self.assertIn("playlist_analysis_run.playlist_id", statement)
        self.assertIn("playlist_analysis_run.status not in", statement)
        self.assertIn("playlist_analysis_run.id not in", statement)

    def test_open_topic_burst_detects_cross_media_without_keywords(self) -> None:
        media_names = ["MacroTalk", "RatesDaily", "CreditDesk", "AsiaFlow"]
        items = [
            _topic_item(
                index % 3,
                [1.0, 0.0],
                title=f"Liquidity desk stress update {index}",
                media_name=media_names[index % len(media_names)],
            )
            for index in range(9)
        ]

        detections = _select_open_topic_burst_detections(_topic_items_by_day(items))

        self.assertEqual(len(detections), 1)
        detection = detections[0]
        self.assertEqual(detection.video_count, 9)
        self.assertEqual(detection.media_count, 4)
        self.assertEqual(detection.active_days, 3)
        self.assertGreaterEqual(detection.cohesion, 0.78)
        self.assertEqual(detection.candidate_date, date(2026, 1, 1))

    def test_open_topic_burst_ignores_single_media_duplicates_when_other_media_active(self) -> None:
        items = [
            _topic_item(
                index % 3,
                [1.0, 0.0],
                title=f"Liquidity desk stress update {index}",
                media_name="MacroTalk",
            )
            for index in range(12)
        ]
        items.extend(
            [
                _topic_item(0, [0.0, 1.0], title="Broad market update", media_name="RatesDaily"),
                _topic_item(1, [0.0, 1.0], title="Credit desk morning note", media_name="CreditDesk"),
                _topic_item(2, [0.0, 1.0], title="Asia flow closing note", media_name="AsiaFlow"),
            ]
        )

        self.assertEqual(_select_open_topic_burst_detections(_topic_items_by_day(items)), [])

    def test_open_topic_burst_adapts_to_sparse_media_coverage(self) -> None:
        media_names = ["ArchiveWire", "MarketTape"]
        items = [
            _topic_item(
                index % 3,
                [1.0, 0.0],
                title=f"Archive liquidity stress update {index}",
                media_name=media_names[index % len(media_names)],
            )
            for index in range(9)
        ]

        detections = _select_open_topic_burst_detections(_topic_items_by_day(items))

        self.assertEqual(len(detections), 1)
        detection = detections[0]
        self.assertEqual(detection.media_count, 2)
        self.assertEqual(detection.available_media_count, 2)
        self.assertEqual(detection.required_media_count, 2)

    def test_open_topic_burst_limits_dense_years_when_timeline_spans_many_years(self) -> None:
        recent = [
            _burst_detection(date(2025, 1, 1) + timedelta(days=index), score=100.0 - index, title=f"recent-{index}")
            for index in range(70)
        ]
        older = [
            _burst_detection(date(year, 6, 1), score=10.0 + index, title=f"older-{year}")
            for index, year in enumerate([2009, 2011, 2015, 2016])
        ]

        selected = _limit_open_topic_burst_detections_by_year(recent + older)

        self.assertLess(len([item for item in selected if item.candidate_date.year == 2025]), 70)
        self.assertTrue({2009, 2011, 2015, 2016}.issubset({item.candidate_date.year for item in selected}))

    def test_open_topic_burst_ignores_single_day_spike(self) -> None:
        media_names = ["MacroTalk", "RatesDaily", "CreditDesk", "AsiaFlow"]
        items = [
            _topic_item(
                0,
                [1.0, 0.0],
                title=f"Liquidity desk stress update {index}",
                media_name=media_names[index % len(media_names)],
            )
            for index in range(10)
        ]

        self.assertEqual(_select_open_topic_burst_detections(_topic_items_by_day(items)), [])

    def test_open_topic_burst_detects_sustained_cross_media_topic(self) -> None:
        media_names = ["Reuters", "MacroTalk", "RatesDaily", "CreditDesk"]
        items = [
            _topic_item(
                index,
                [1.0, 0.0],
                title=f"Russia Ukraine war update {index}",
                media_name=media_names[index % len(media_names)],
            )
            for index in range(12)
        ]

        detections = _select_open_topic_burst_detections(_topic_items_by_day(items))

        self.assertEqual(len(detections), 1)
        detection = detections[0]
        self.assertEqual(detection.video_count, 12)
        self.assertEqual(detection.media_count, 4)
        self.assertEqual(detection.active_days, 12)
        self.assertEqual(detection.window_days, 14)
        self.assertGreaterEqual(detection.cohesion, 0.68)
        self.assertTrue(detection.top_terms)

    def test_open_topic_burst_merges_overlapping_windows(self) -> None:
        media_names = ["MacroTalk", "RatesDaily", "CreditDesk", "AsiaFlow"]
        items = [
            _topic_item(
                index % 4,
                [1.0, 0.0],
                title=f"Funding pressure watch {index}",
                media_name=media_names[index % len(media_names)],
            )
            for index in range(16)
        ]

        detections = _select_open_topic_burst_detections(_topic_items_by_day(items))

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0].video_count, 12)

    def test_open_topic_burst_avoids_occupied_candidate_date(self) -> None:
        media_names = ["MacroTalk", "RatesDaily", "CreditDesk", "AsiaFlow"]
        items = [
            _topic_item(0, [1.0, 0.0], title=f"Funding pressure watch early {index}", media_name=media_names[index % 4])
            for index in range(2)
        ]
        items.extend(
            _topic_item(1, [1.0, 0.0], title=f"Funding pressure watch peak {index}", media_name=media_names[index % 4])
            for index in range(5)
        )
        items.extend(
            _topic_item(2, [1.0, 0.0], title=f"Funding pressure watch late {index}", media_name=media_names[index % 4])
            for index in range(2)
        )

        detections = _select_open_topic_burst_detections(
            _topic_items_by_day(items),
            occupied_dates={date(2026, 1, 2)},
        )

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0].candidate_date, date(2026, 1, 1))

    def test_candidate_top_terms_supports_chinese_ngrams_and_detection_terms(self) -> None:
        evidence = {
            "videos": [
                {"title": "以色列突袭伊朗核设施，中东冲突升级"},
                {"title": "US strikes Iran nuclear facilities"},
            ]
        }
        terms = _candidate_top_terms(evidence)

        self.assertTrue(set(terms) & {"伊朗", "iran"})

        detection_terms = _candidate_top_terms({"detection": {"top_terms": ["伊朗", "美国"]}, "videos": []})
        self.assertEqual(detection_terms, ["伊朗", "美国"])

    def test_build_snapshot_streams_ready_embeddings_without_full_loader(self) -> None:
        playlist_id = uuid.uuid4()
        session = _FakeSnapshotSession()
        items = [_analysis_item(0, [1.0, 0.0]), _analysis_item(1, [0.0, 1.0])]

        def iter_batches(_session, _playlist_id, **_kwargs):
            yield items

        with patch("raelyn.services.playlist_analysis.playlist_coverage_stats", return_value=CoverageStats(2, 2, 0, 0)):
            with patch("raelyn.services.playlist_analysis.ensure_analysis_resource_budget"):
                with patch("raelyn.services.playlist_analysis._playlist_ready_embedding_items", side_effect=AssertionError("full loader called")):
                    with patch("raelyn.services.playlist_analysis._iter_playlist_ready_embedding_batches", side_effect=iter_batches):
                        result = build_playlist_analysis_snapshot(session, playlist_id)

        self.assertEqual(result["period_count"], 2)
        self.assertEqual(result["signal_count"], 4)
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["pruned_run_count"], 0)
        periods = [item for item in session.added if isinstance(item, PlaylistAnalysisPeriod)]
        self.assertEqual([period.video_count for period in periods], [1, 1])
        self.assertAlmostEqual(periods[0].dispersion_score, 0.0)
        self.assertIsNone(periods[0].drift_score)
        self.assertAlmostEqual(periods[1].drift_score, 1.0)
        signals = [item for item in session.added if isinstance(item, PlaylistAnalysisSignal)]
        self.assertEqual(sorted({signal.granularity for signal in signals}), ["day", "month", "week"])
        self.assertTrue(all(signal.ready_embedding_count == signal.video_count for signal in signals))
        run = next(item for item in session.added if isinstance(item, PlaylistAnalysisRun))
        self.assertEqual(run.status, "ready")

    def test_build_snapshot_does_not_promote_single_day_spike_to_event(self) -> None:
        playlist_id = uuid.uuid4()
        session = _FakeSnapshotSession()
        all_items = [
            _analysis_item(index, [0.0, 1.0] if index == 100 else [1.0, 0.0])
            for index in range(240)
        ]

        def iter_batches(_session, _playlist_id, **kwargs):
            only_days = kwargs.get("only_days")
            if only_days is None:
                yield all_items
                return
            selected = [item for item in all_items if item.published_at.date() in only_days]
            yield selected

        with patch("raelyn.services.playlist_analysis.playlist_coverage_stats", return_value=CoverageStats(len(all_items), len(all_items), 0, 0)):
                with patch("raelyn.services.playlist_analysis.ensure_analysis_resource_budget"):
                    with patch("raelyn.services.playlist_analysis._iter_playlist_ready_embedding_batches", side_effect=iter_batches):
                        result = build_playlist_analysis_snapshot(session, playlist_id)

        self.assertEqual(result["candidate_count"], 0)
        self.assertFalse([item for item in session.added if isinstance(item, PlaylistAnalysisCandidate)])

    def test_build_snapshot_adds_open_topic_burst_candidate(self) -> None:
        playlist_id = uuid.uuid4()
        session = _FakeSnapshotSession()
        titles = [
            "Liquidity desk stress rises into close",
            "Funding desks report overnight pressure",
            "Credit desks flag collateral squeeze",
            "Repo market pressure spreads across funds",
            "Short-term funding stress draws trader focus",
            "Money market desks discuss collateral shortage",
            "Rates desk sees cash funding pressure",
            "Funding squeeze becomes macro focus",
            "Traders debate liquidity pressure window",
        ]
        media_names = ["MacroTalk", "RatesDaily", "CreditDesk", "AsiaFlow", "GlobalMarkets"]
        background = [_analysis_item(index, [0.0, 1.0], title=f"background-{index}", media_name="Background") for index in range(30)]
        event_items = [
            _analysis_item(10 + index % 3, [1.0, 0.0], title=title, media_name=media_names[index % len(media_names)])
            for index, title in enumerate(titles)
        ]
        all_items = background + event_items

        def iter_batches(_session, _playlist_id, **kwargs):
            only_days = kwargs.get("only_days")
            if only_days is None:
                yield all_items
                return
            selected = [item for item in all_items if item.published_at.date() in only_days]
            yield selected

        with patch("raelyn.services.playlist_analysis.playlist_coverage_stats", return_value=CoverageStats(len(all_items), len(all_items), 0, 0)):
            with patch("raelyn.services.playlist_analysis.ensure_analysis_resource_budget"):
                with patch("raelyn.services.playlist_analysis._iter_playlist_ready_embedding_batches", side_effect=iter_batches):
                    result = build_playlist_analysis_snapshot(session, playlist_id)

        self.assertEqual(result["candidate_count"], 1)
        candidate = next(item for item in session.added if isinstance(item, PlaylistAnalysisCandidate))
        detection = candidate.evidence_json["detection"]
        self.assertEqual(detection["method"], "open_topic_burst_v1")
        self.assertEqual(detection["video_count"], 9)
        self.assertEqual(detection["required_media_count"], 3)
        self.assertGreaterEqual(detection["available_media_count"], 3)
        self.assertGreaterEqual(detection["cohesion"], 0.78)
        self.assertEqual(candidate.event_type, "burst")
        self.assertGreaterEqual(len({item["media_name"] for item in candidate.evidence_json["videos"]}), 3)

    def test_build_snapshot_adds_sustained_open_topic_burst_candidate(self) -> None:
        playlist_id = uuid.uuid4()
        session = _FakeSnapshotSession()
        media_names = ["Reuters", "MacroTalk", "RatesDaily", "CreditDesk"]
        background = [_analysis_item(index, [0.0, 1.0], title=f"background-{index}", media_name="Background") for index in range(30)]
        event_items = [
            _analysis_item(
                index,
                [1.0, 0.0],
                title=f"Trump tariff trade war update {index}",
                media_name=media_names[index % len(media_names)],
            )
            for index in range(12)
        ]
        all_items = background + event_items

        def iter_batches(_session, _playlist_id, **kwargs):
            only_days = kwargs.get("only_days")
            if only_days is None:
                yield all_items
                return
            selected = [item for item in all_items if item.published_at.date() in only_days]
            yield selected

        with patch("raelyn.services.playlist_analysis.playlist_coverage_stats", return_value=CoverageStats(len(all_items), len(all_items), 0, 0)):
            with patch("raelyn.services.playlist_analysis.ensure_analysis_resource_budget"):
                with patch("raelyn.services.playlist_analysis._iter_playlist_ready_embedding_batches", side_effect=iter_batches):
                    result = build_playlist_analysis_snapshot(session, playlist_id)

        self.assertEqual(result["candidate_count"], 1)
        candidate = next(item for item in session.added if isinstance(item, PlaylistAnalysisCandidate))
        detection = candidate.evidence_json["detection"]
        self.assertEqual(detection["method"], "open_topic_burst_v1")
        self.assertEqual(detection["window_days"], 14)
        self.assertEqual(detection["video_count"], 12)
        self.assertEqual(detection["required_media_count"], 3)
        self.assertGreaterEqual(detection["available_media_count"], 3)
        self.assertEqual(candidate.event_type, "burst")
        self.assertIn("tariff", candidate.top_terms)

    def test_build_snapshot_detects_sustained_regime_boundary_with_week_refinement(self) -> None:
        playlist_id = uuid.uuid4()
        session = _FakeSnapshotSession()
        all_items = [
            _analysis_item(index, [1.0, 0.0] if index < 120 else [0.0, 1.0], title=f"regime-{index}")
            for index in range(365)
        ]

        def iter_batches(_session, _playlist_id, **kwargs):
            only_days = kwargs.get("only_days")
            if only_days is None:
                yield all_items
                return
            selected = [item for item in all_items if item.published_at.date() in only_days]
            yield selected

        with patch("raelyn.services.playlist_analysis.playlist_coverage_stats", return_value=CoverageStats(len(all_items), len(all_items), 0, 0)):
            with patch("raelyn.services.playlist_analysis.ensure_analysis_resource_budget"):
                with patch("raelyn.services.playlist_analysis._iter_playlist_ready_embedding_batches", side_effect=iter_batches):
                    result = build_playlist_analysis_snapshot(session, playlist_id)

        self.assertEqual(result["candidate_count"], 1)
        candidate = next(item for item in session.added if isinstance(item, PlaylistAnalysisCandidate))
        evidence = candidate.evidence_json
        self.assertEqual(len(evidence["videos"]), 5)
        self.assertTrue(evidence["preview"])
        detection = evidence["detection"]
        self.assertEqual(detection["method"], "two_window_centroid_drift_v1")
        self.assertEqual(detection["granularity"], "week")
        self.assertEqual(detection["supporting_granularities"], ["month", "week"])
        self.assertEqual(candidate.status, "draft")
        self.assertEqual(candidate.event_type, "regime")
        self.assertEqual(candidate.peak_date, candidate.candidate_date)
        self.assertEqual(candidate.score, detection["boundary_z"])
        self.assertEqual(candidate.drift_score, detection["boundary_score"])
        self.assertEqual(candidate.event_start.isoformat(), detection["before_start"])
        self.assertEqual(candidate.event_end.isoformat(), detection["after_end"])
        self.assertIsNotNone(candidate.available_at)
        self.assertTrue(candidate.evidence_video_ids)
        self.assertIsNone(candidate.train_start)
        self.assertIsNone(candidate.valid_start)
        self.assertIsNone(candidate.test_start)
        self.assertGreater(candidate.drift_rolling_z, 1.5)
        linked_signals = [
            item
            for item in session.added
            if isinstance(item, PlaylistAnalysisSignal) and item.linked_event_id == candidate.id
        ]
        self.assertTrue(linked_signals)

    def test_select_month_boundaries_merges_close_candidates(self) -> None:
        def boundary(day: date, z_value: float) -> BoundaryDetection:
            return BoundaryDetection(
                granularity="month",
                breakpoint_date=day,
                before_start=day,
                before_end=day,
                after_start=day,
                after_end=day,
                boundary_score=0.5,
                boundary_z=z_value,
                before_centroid=[1.0, 0.0],
                after_centroid=[0.0, 1.0],
            )

        selected = _select_month_boundaries(
            [
                boundary(date(2026, 1, 1), 2.2),
                boundary(date(2026, 2, 1), 3.1),
                boundary(date(2026, 4, 1), 2.6),
                boundary(date(2026, 6, 1), 2.4),
            ]
        )

        self.assertEqual([item.breakpoint_date for item in selected], [date(2026, 2, 1), date(2026, 6, 1)])


if __name__ == "__main__":
    unittest.main()
