from __future__ import annotations

import sys
import unittest
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy.dialects import postgresql

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.api.playlists import (
    _EVENT_MAP_SCENE_RECORD,
    _active_playlist_event_backfill_job,
    _event_map_day,
    _event_map_local_entity_relations,
    _event_map_query_timeout,
    _event_map_scene_chunks,
    _event_map_scene_statement,
    _playlist_event_backfill_job_out,
    PlaylistEventsSummaryOut,
    get_playlist_events_summary,
    get_playlist_event_map_manifest,
    list_playlist_event_entity_suggestions,
    list_playlist_events,
    list_playlist_video_counts_by_period,
    list_playlist_videos_by_period,
    search_playlist_event_map,
)
from raelyn.models import EventMapCanonical, EventMapSnapshot, EventMapState, EventMapTopic, Job, Playlist


class EventMapQueryGuardTests(unittest.TestCase):
    def test_only_postgresql_statement_timeout_is_reported_as_timeout(self) -> None:
        self.assertTrue(_event_map_query_timeout(SimpleNamespace(orig=SimpleNamespace(sqlstate="57014"))))
        self.assertFalse(_event_map_query_timeout(SimpleNamespace(orig=SimpleNamespace(sqlstate="23505"))))
        self.assertFalse(_event_map_query_timeout(SimpleNamespace(orig=SimpleNamespace())))


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def one(self):
        return self._rows[0]


class _IterableRows:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False

    def __iter__(self):
        return iter(self.rows)

    def close(self):
        self.closed = True


class _ScalarOneResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class _BackfillProgressSession:
    def __init__(
        self,
        *,
        parent: Job,
        range_ids: list[uuid.UUID],
        range_rows: list[tuple[str, dict | None]],
        video_status_rows: list[tuple[str, int]],
    ) -> None:
        self.parent = parent
        self.range_ids = list(range_ids)
        self.range_rows = list(range_rows)
        self.video_jobs = [
            Job(
                id=uuid.uuid4(),
                type="video.extract_events_batch",
                status=status,
                params={"video_ids": [str(index) for index in range(count)]},
                result={"videos": count} if status == "succeeded" else None,
            )
            for status, count in video_status_rows
        ]
        self.execute_calls = 0

    def get(self, model, key):
        if model is Job and key == self.parent.id:
            return self.parent
        return None

    def execute(self, _stmt):
        self.execute_calls += 1
        if self.execute_calls == 1:
            return _ScalarResult(self.range_ids)
        if self.execute_calls == 2:
            return _RowsResult(self.range_rows)
        return _ScalarResult(self.video_jobs)


class _ActiveBackfillSession:
    def __init__(self, *, video_job: Job, range_job: Job) -> None:
        self.video_job = video_job
        self.range_job = range_job
        self.execute_calls = 0

    def get(self, model, key):
        if model is Job and key == self.range_job.id:
            return self.range_job
        return None

    def execute(self, _stmt):
        self.execute_calls += 1
        if self.execute_calls == 1:
            return _ScalarResult([])
        return _ScalarResult([self.video_job])


@contextmanager
def _fake_session_scope(session):
    yield session


class PlaylistApiTests(unittest.TestCase):
    def test_compact_event_map_manifest_skips_heavy_scene_metadata(self) -> None:
        playlist_id = uuid.uuid4()
        snapshot_id = uuid.uuid4()
        state = SimpleNamespace(
            current_snapshot_id=snapshot_id,
            dirty_generation=4,
            built_generation=4,
            last_error=None,
            last_requested_at=None,
            last_built_at=None,
        )
        snapshot = SimpleNamespace(
            id=snapshot_id,
            status="ready",
            layout_algorithm_version="event_map_projection_v2",
            projection_method="incremental_pca50_umap3_cosine",
            canonical_count=12,
            member_count=18,
            input_record_count=18,
            skipped_reason_counts={"missing_event_time": 2},
        )
        session = Mock()

        def get(model, key):
            if model is Playlist:
                return object()
            if model is EventMapState:
                return state
            if model is EventMapSnapshot:
                return snapshot
            return None

        session.get.side_effect = get
        with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
            with patch("raelyn.api.playlists._active_event_map_build_job", return_value=None):
                with patch("raelyn.api.playlists._active_playlist_event_backfill_job", return_value=None):
                    with patch("raelyn.api.playlists.playlist_event_map_coverage") as coverage:
                        result = get_playlist_event_map_manifest(playlist_id, compact=True)

        coverage.assert_not_called()
        session.execute.assert_not_called()
        self.assertEqual(result["snapshot_id"], str(snapshot_id))
        self.assertEqual(result["canonical_count"], 12)
        self.assertEqual(result["record_count"], 18)
        self.assertNotIn("topics", result)
        self.assertNotIn("topics", result)

    def test_event_map_local_entities_keep_frozen_roles_and_relations(self) -> None:
        source_entity_id = uuid.uuid4()
        target_entity_id = uuid.uuid4()
        revision = SimpleNamespace(
            id=uuid.uuid4(),
            entities_json=[
                {
                    "id": str(source_entity_id),
                    "entity_type": "country",
                    "normalized_key": "United States",
                    "name": "United States",
                    "role": "actor",
                },
                {
                    "id": str(target_entity_id),
                    "entity_type": "sector",
                    "normalized_key": "Energy",
                    "name": "Energy",
                    "role": "target",
                },
            ],
            source_json={
                "relations": [
                    {
                        "id": str(uuid.uuid4()),
                        "source_entity_id": str(source_entity_id),
                        "target_entity_id": str(target_entity_id),
                        "relation_type": "regulates",
                        "direction": "directed",
                        "confidence": 0.9,
                    }
                ]
            },
        )
        entities = {
            ("country", "unitedstates"): {"id": "country:unitedstates"},
            ("sector", "energy"): {"id": "sector:energy"},
        }

        relations = _event_map_local_entity_relations([(SimpleNamespace(), revision)], entities)

        self.assertEqual(entities[("country", "unitedstates")]["role_label"], "actor")
        self.assertEqual(entities[("sector", "energy")]["role_label"], "target")
        self.assertEqual(relations[0]["source_entity_id"], "country:unitedstates")
        self.assertEqual(relations[0]["target_entity_id"], "sector:energy")
        self.assertEqual(relations[0]["relation_type"], "regulates")

    def test_event_map_scene_record_is_fixed_canonical_layout(self) -> None:
        canonical_id = uuid.uuid4()
        payload = _EVENT_MAP_SCENE_RECORD.pack(
            7,
            canonical_id.bytes,
            1.25,
            -3.5,
            2.75,
            _event_map_day(date(2012, 3, 1)),
            _event_map_day(date(2012, 3, 31)),
            4,
            2,
            1,
            0,
            17,
            3,
            8,
        )

        self.assertEqual(_EVENT_MAP_SCENE_RECORD.size, 56)
        values = _EVENT_MAP_SCENE_RECORD.unpack(payload)
        self.assertEqual(values[0], 7)
        self.assertEqual(uuid.UUID(bytes=values[1]), canonical_id)
        self.assertEqual(values[11:], (17, 3, 8))

    def test_event_map_scene_query_selects_only_binary_record_columns(self) -> None:
        statement = _event_map_scene_statement(snapshot_id=uuid.uuid4())

        self.assertEqual(
            list(statement.selected_columns.keys()),
            [
                "point_index",
                "canonical_id",
                "x",
                "y",
                "z",
                "event_start_day",
                "event_end_day",
                "event_type_code",
                "time_precision_code",
                "uncertainty_flags",
                "time_disagreement_count",
                "member_count",
                "macro_topic_id",
                "local_topic_id",
            ],
        )
        compiled = str(statement.compile(dialect=postgresql.dialect())).lower()
        self.assertNotIn("event_map_canonical.title", compiled)
        self.assertNotIn("event_map_canonical.summary", compiled)
        self.assertNotIn("event_map_canonical.time_basis", compiled)
        self.assertNotIn("event_map_canonical.reason_codes", compiled)

    def test_event_map_scene_batches_binary_records_into_one_chunk(self) -> None:
        topic_id = uuid.uuid4()
        canonical_ids = [uuid.uuid4(), uuid.uuid4()]
        rows = [
            (
                index,
                canonical_id,
                float(index),
                -float(index),
                float(index) * 0.5,
                10 + index,
                11 + index,
                2,
                1,
                ["uncertain"] if index == 0 else [],
                index,
                3 + index,
                topic_id,
                topic_id,
            )
            for index, canonical_id in enumerate(canonical_ids)
        ]

        chunks = list(
            _event_map_scene_chunks(
                rows,
                canonical_count=2,
                topic_order={topic_id: 4},
            )
        )

        self.assertEqual(len(chunks), 1)
        self.assertEqual(len(chunks[0]), 2 * _EVENT_MAP_SCENE_RECORD.size)
        first = _EVENT_MAP_SCENE_RECORD.unpack_from(chunks[0], 0)
        second = _EVENT_MAP_SCENE_RECORD.unpack_from(chunks[0], _EVENT_MAP_SCENE_RECORD.size)
        self.assertEqual(first[0], 0)
        self.assertEqual(uuid.UUID(bytes=first[1]), canonical_ids[0])
        self.assertEqual(first[3:5], (0.0, 0.0))
        self.assertEqual(first[9], 1)
        self.assertEqual(first[11:], (3, 4, 4))
        self.assertEqual(second[0], 1)
        self.assertEqual(second[9], 2)

    def test_event_map_topic_search_returns_anchor_location(self) -> None:
        playlist_id = uuid.uuid4()
        snapshot_id = uuid.uuid4()
        topic_id = uuid.uuid4()
        canonical_id = uuid.uuid4()
        snapshot = SimpleNamespace(
            id=snapshot_id,
            playlist_id=playlist_id,
            status="ready",
            layout_algorithm_version="event_map_projection_v2",
            projection_method="incremental_pca50_umap3_cosine",
        )
        topic = SimpleNamespace(
            topic_id=topic_id,
            label="人工智能产业",
            canonical_count=8,
            anchor_canonical_id=canonical_id,
        )
        anchor = SimpleNamespace(canonical_id=canonical_id, point_index=27)
        session = Mock()
        session.get.return_value = snapshot
        session.execute.side_effect = [
            _ScalarResult([]),
            _RowsResult([(topic, anchor)]),
            _RowsResult([]),
        ]

        with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
            result = search_playlist_event_map(
                playlist_id=playlist_id,
                snapshot_id=snapshot_id,
                q="人工智能",
            )

        self.assertEqual(
            result,
            [
                {
                    "kind": "topic",
                    "id": str(topic_id),
                    "canonical_id": str(canonical_id),
                    "point_index": 27,
                    "title": "人工智能产业",
                    "label": "人工智能产业",
                }
            ],
        )
        topic_stmt = session.execute.call_args_list[1].args[0]
        compiled = str(topic_stmt.compile(dialect=postgresql.dialect())).lower()
        self.assertIn("join event_map_canonical", compiled)
        self.assertIn("event_map_canonical.snapshot_id = event_map_topic.snapshot_id", compiled)
        self.assertIn("event_map_canonical.canonical_id = event_map_topic.anchor_canonical_id", compiled)

    def test_active_backfill_detects_running_video_child_after_range_finished(self) -> None:
        playlist_id = uuid.uuid4()
        range_id = uuid.uuid4()
        video_job = Job(
            id=uuid.uuid4(),
            type="video.extract_events",
            status="running",
            params={"video_id": str(uuid.uuid4()), "force": True},
            parent_job_id=range_id,
        )
        range_job = Job(
            id=range_id,
            type="playlist.backfill_events_range",
            status="succeeded",
            params={"playlist_id": str(playlist_id), "force": True},
        )
        session = _ActiveBackfillSession(video_job=video_job, range_job=range_job)

        self.assertIs(_active_playlist_event_backfill_job(session, playlist_id), video_job)

    def test_event_backfill_job_out_reports_force_video_progress_and_duration(self) -> None:
        parent_id = uuid.uuid4()
        range_id = uuid.uuid4()
        started_at = datetime(2026, 6, 5, 1, 0, tzinfo=timezone.utc)
        parent = Job(
            id=parent_id,
            type="playlist.backfill_events",
            status="succeeded",
            params={"playlist_id": str(uuid.uuid4()), "force": True},
            created_at=started_at,
            started_at=started_at,
        )
        range_job = Job(
            id=range_id,
            type="playlist.backfill_events_range",
            status="running",
            params={"playlist_id": str(uuid.uuid4()), "force": True},
            result={"scanned": 10, "enqueued": 8, "skipped": 2, "force": True},
            parent_job_id=parent_id,
            created_at=started_at,
            started_at=started_at,
        )
        session = _BackfillProgressSession(
            parent=parent,
            range_ids=[range_id, uuid.uuid4()],
            range_rows=[
                ("succeeded", {"scanned": 10, "enqueued": 8, "skipped": 2}),
                ("pending", None),
            ],
            video_status_rows=[("succeeded", 3), ("pending", 4), ("running", 1), ("failed", 2)],
        )

        with patch("raelyn.api.playlists.utcnow", return_value=datetime(2026, 6, 5, 1, 1, 40, tzinfo=timezone.utc)):
            payload = _playlist_event_backfill_job_out(session, range_job)

        self.assertTrue(payload.force)
        self.assertEqual(payload.scanned, 10)
        self.assertEqual(payload.enqueued, 8)
        self.assertEqual(payload.skipped, 2)
        self.assertEqual(payload.range_finished, 1)
        self.assertEqual(payload.range_pending, 1)
        self.assertEqual(payload.range_running, 0)
        self.assertEqual(payload.range_failed, 0)
        self.assertEqual(payload.range_total, 2)
        self.assertEqual(payload.video_extracted, 3)
        self.assertEqual(payload.video_pending, 4)
        self.assertEqual(payload.video_running, 1)
        self.assertEqual(payload.video_failed, 2)
        self.assertEqual(payload.video_total, 10)
        self.assertEqual(payload.elapsed_seconds, 100)
        self.assertEqual(payload.estimated_total_seconds, 367)

    def test_event_backfill_eta_counts_unscanned_months_as_future_video_work(self) -> None:
        parent_id = uuid.uuid4()
        started_at = datetime(2026, 6, 5, 1, 0, tzinfo=timezone.utc)
        parent = Job(
            id=parent_id,
            type="playlist.backfill_events",
            status="succeeded",
            params={"playlist_id": str(uuid.uuid4()), "force": True},
            created_at=started_at,
            started_at=started_at,
            progress_current=218,
            progress_total=218,
        )
        range_ids = [uuid.uuid4() for _ in range(23)]
        session = _BackfillProgressSession(
            parent=parent,
            range_ids=range_ids,
            range_rows=[("succeeded", {"scanned": 463, "enqueued": 463, "skipped": 0}) for _ in range(23)],
            video_status_rows=[("succeeded", 10108), ("pending", 537), ("running", 1)],
        )
        elapsed_seconds = 44 * 3600 + 48 * 60 + 39

        with patch(
            "raelyn.api.playlists.utcnow",
            return_value=datetime(2026, 6, 5, 1, 0, tzinfo=timezone.utc)
            + timedelta(seconds=elapsed_seconds),
        ):
            payload = _playlist_event_backfill_job_out(session, parent)

        self.assertEqual(payload.range_finished, 23)
        self.assertEqual(payload.range_pending, 195)
        self.assertEqual(payload.range_total, 218)
        self.assertEqual(payload.video_extracted, 10108)
        self.assertEqual(payload.video_total, 10646)
        self.assertGreater(payload.estimated_total_seconds or 0, elapsed_seconds * 5)

    def test_list_playlist_events_orders_by_observable_time_before_target_time(self) -> None:
        playlist_id = uuid.uuid4()
        session = Mock()
        session.get.return_value = object()
        session.execute.return_value = _ScalarResult([])

        with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
            result = list_playlist_events(playlist_id=playlist_id)

        self.assertEqual(result, [])
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect())).lower()
        available_pos = compiled.find("market_event.available_at desc")
        target_pos = compiled.find("market_event.event_time_start desc")
        self.assertGreaterEqual(available_pos, 0)
        self.assertGreater(target_pos, available_pos)

    def test_list_playlist_events_filters_current_period_by_available_time(self) -> None:
        playlist_id = uuid.uuid4()
        session = Mock()
        session.get.return_value = object()
        session.execute.return_value = _ScalarResult([])

        with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
            result = list_playlist_events(playlist_id=playlist_id, period=date(2026, 6, 1), granularity="day")

        self.assertEqual(result, [])
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()
        self.assertIn("market_event.available_at >=", compiled)
        self.assertIn("market_event.available_at <", compiled)

    def test_playlist_events_summary_accepts_current_period_filter(self) -> None:
        playlist_id = uuid.uuid4()
        session = Mock()
        session.get.return_value = object()
        summary = PlaylistEventsSummaryOut(playlist_id=playlist_id)

        with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
            with patch("raelyn.api.playlists._playlist_events_summary", return_value=summary) as summarize:
                result = get_playlist_events_summary(playlist_id=playlist_id, period=date(2026, 6, 1), granularity="day")

        self.assertEqual(result, summary)
        kwargs = summarize.call_args.kwargs
        self.assertIsNotNone(kwargs.get("available_start"))
        self.assertIsNotNone(kwargs.get("available_end"))

    def test_playlist_event_entity_suggestions_filter_current_period(self) -> None:
        playlist_id = uuid.uuid4()
        session = Mock()
        session.get.return_value = object()
        session.execute.return_value = _RowsResult([])

        with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
            result = list_playlist_event_entity_suggestions(
                playlist_id=playlist_id,
                q="goldman",
                status="accepted",
                period=date(2026, 6, 1),
                granularity="day",
            )

        self.assertEqual(result, [])
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).lower()
        self.assertIn("market_event.available_at >=", compiled)
        self.assertIn("market_event.available_at <", compiled)
        self.assertIn("market_event.status = 'accepted'", compiled)
        self.assertIn("market_event_entity.name ilike", compiled)
        self.assertIn("group by market_event_entity.entity_type", compiled)

    def test_list_playlist_video_counts_by_period_requires_playback_admission(self) -> None:
        playlist_id = uuid.uuid4()
        session = Mock()
        session.bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
        session.execute.return_value = _RowsResult(
            [
                (datetime(2026, 3, 13, 16, 30, tzinfo=timezone.utc),),
                (datetime(2026, 3, 15, 3, 26, 55, tzinfo=timezone.utc),),
                (datetime(2026, 3, 15, 12, 16, 47, tzinfo=timezone.utc),),
            ]
        )

        with patch("raelyn.api.playlists.ensure_video_published_at_backfilled"):
            with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
                result = list_playlist_video_counts_by_period(
                    playlist_id=playlist_id,
                    granularity="day",
                    start=date(2026, 3, 14),
                    end=date(2026, 3, 15),
                )

        self.assertEqual(
            [(item.period_start, item.count) for item in result],
            [(date(2026, 3, 14), 1), (date(2026, 3, 15), 2)],
        )
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect())).lower()
        self.assertIn("video_time_evidence", compiled)
        self.assertIn("make_timestamptz", compiled)
        self.assertIn("coalesce", compiled)
        self.assertIn("row_number() over", compiled)
        self.assertIn("left outer join", compiled)
        self.assertIn("asset.type =", compiled)
        self.assertIn("exists (select 1", compiled)

    def test_list_playlist_videos_by_period_requires_playback_admission(self) -> None:
        playlist_id = uuid.uuid4()

        session = Mock()
        session.execute.return_value = _RowsResult([])

        with patch("raelyn.api.playlists.ensure_video_published_at_backfilled"):
            with patch("raelyn.api.playlists.session_scope", lambda: _fake_session_scope(session)):
                result = list_playlist_videos_by_period(
                    playlist_id=playlist_id,
                    granularity="day",
                    date=date(2026, 3, 15),
                )

        self.assertEqual(result, [])
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(dialect=postgresql.dialect())).lower()
        self.assertIn("video_time_evidence", compiled)
        self.assertIn("make_timestamptz", compiled)
        self.assertIn("coalesce", compiled)
        self.assertIn("row_number() over", compiled)
        self.assertIn("left outer join", compiled)
        self.assertIn("asset.type =", compiled)
        self.assertIn("exists (select 1", compiled)


if __name__ == "__main__":
    unittest.main()
