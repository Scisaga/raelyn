from __future__ import annotations

import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import Uuid, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from raelyn.db import _create_event_analysis_indexes as _create_runtime_event_analysis_indexes
from raelyn.db import _migrate_schema as _migrate_runtime_schema
from raelyn.jobs.enqueue import _default_max_attempts, _normalize_dedupe_key_and_params
from raelyn.jobs.handlers.event_analysis import _event_map_build_scheduled_for
from raelyn.models import (
    Base,
    EventMapEntityIndex,
    EventMapCanonicalIdentity,
    EventMapSnapshot,
    EventMapState,
    EventMapStoryEdge,
    EventMapStoryIdentity,
    EventMapTopic,
    EventMapTopicMember,
    Job,
)
from raelyn.services.worker_roles import WORKER_ROLE_TYPES, job_type_worker_role
from raelyn.tools.migrate_data import (
    _create_event_analysis_indexes as _create_data_event_analysis_indexes,
    _drop_legacy_event_analysis,
    _legacy_event_analysis_drop_blockers,
    _migrate_schema as _migrate_data_schema,
)


class EventMapModelTests(unittest.TestCase):
    def test_six_object_snapshot_tables_are_registered_without_old_analysis_models(self) -> None:
        table_names = set(Base.metadata.tables)
        self.assertTrue(
            {
                "event_map_state",
                "event_map_snapshot",
                "event_map_record_revision",
                "event_map_canonical_identity",
                "event_map_canonical",
                "event_map_canonical_member",
                "event_map_canonical_lineage",
                "event_map_entity_index",
                "event_map_topic",
                "event_map_topic_member",
                "event_map_story",
                "event_map_story_member",
                "event_map_story_edge",
                "event_map_projection_anchor",
            }.issubset(table_names)
        )
        self.assertNotIn("event_regime_run", table_names)
        self.assertNotIn("event_regime_state", table_names)
        self.assertNotIn("event_regime_signal", table_names)
        self.assertNotIn("event_regime_candidate", table_names)
        self.assertNotIn("event_graph_projection_point", table_names)

    def test_state_and_snapshot_expose_generation_and_snapshot_pin_contract(self) -> None:
        self.assertTrue(
            {
                "current_snapshot_id",
                "dirty_generation",
                "built_generation",
                "active_job_id",
                "first_dirty_at",
                "last_dirty_at",
            }.issubset(EventMapState.__table__.columns.keys())
        )
        self.assertTrue(
            {
                "input_generation",
                "input_fingerprint",
                "build_key",
                "layout_continuity",
                "type_categories",
                "canonical_count",
                "entity_count",
                "execution_token",
                "peak_rss_bytes",
            }.issubset(EventMapSnapshot.__table__.columns.keys())
        )
        self.assertIn("event_map_snapshot_ready_build_ux", {index.name for index in EventMapSnapshot.__table__.indexes})
        for execution_token in (
            Job.__table__.columns["execution_token"],
            EventMapSnapshot.__table__.columns["execution_token"],
        ):
            self.assertIsInstance(execution_token.type, Uuid)
            self.assertTrue(execution_token.nullable)
        constraint_names = {constraint.name for constraint in EventMapSnapshot.__table__.constraints}
        self.assertIn("event_map_snapshot_job_execution_ux", constraint_names)
        self.assertNotIn("event_map_snapshot_job_attempt_ux", constraint_names)

    def test_entity_index_is_snapshot_pinned_and_queryable_without_record_scans(self) -> None:
        columns = EventMapEntityIndex.__table__.columns
        self.assertTrue(
            {
                "snapshot_id",
                "canonical_id",
                "entity_type",
                "normalized_key",
                "name",
                "point_index",
                "record_count",
            }.issubset(columns.keys())
        )
        self.assertEqual(
            [column.name for column in EventMapEntityIndex.__table__.primary_key.columns],
            ["snapshot_id", "canonical_id", "entity_type", "normalized_key"],
        )
        constraint_names = {constraint.name for constraint in EventMapEntityIndex.__table__.constraints}
        self.assertIn("event_map_entity_index_point_key_ux", constraint_names)
        self.assertIn("event_map_entity_index_record_count_ck", constraint_names)
        self.assertIn("event_map_entity_index_key_idx", {index.name for index in EventMapEntityIndex.__table__.indexes})

    def test_scene_member_join_keys_and_topic_level_index_are_declared(self) -> None:
        self.assertEqual(
            [column.name for column in EventMapTopicMember.__table__.primary_key.columns],
            ["snapshot_id", "canonical_id", "level"],
        )
        self.assertIn(
            "event_map_topic_member_level_canonical_idx",
            {index.name for index in EventMapTopicMember.__table__.indexes},
        )

    def test_snapshot_delete_foreign_keys_have_supporting_indexes(self) -> None:
        expected = {
            EventMapSnapshot: {"event_map_snapshot_parent_idx"},
            EventMapCanonicalIdentity: {
                "event_map_canonical_identity_created_snapshot_idx",
                "event_map_canonical_identity_retired_snapshot_idx",
            },
            EventMapTopic: {"event_map_topic_anchor_idx"},
            EventMapStoryEdge: {"event_map_story_edge_story_idx"},
        }
        for model, names in expected.items():
            with self.subTest(table=model.__tablename__):
                self.assertTrue(names.issubset({index.name for index in model.__table__.indexes}))

    def test_story_identity_exposes_frozen_title_and_material_update_cursor(self) -> None:
        columns = EventMapStoryIdentity.__table__.columns
        self.assertFalse(columns["stable_title"].nullable)
        self.assertTrue(columns["last_material_snapshot_id"].nullable)
        self.assertEqual(list(columns["last_material_snapshot_id"].foreign_keys), [])
        self.assertFalse(columns["last_material_changed_at"].nullable)
        self.assertIn(
            "event_map_story_identity_material_idx",
            {index.name for index in EventMapStoryIdentity.__table__.indexes},
        )

    def test_runtime_and_data_migrations_add_snapshot_delete_indexes(self) -> None:
        expected_by_table = {
            "event_map_snapshot": {"event_map_snapshot_parent_idx"},
            "event_map_canonical_identity": {
                "event_map_canonical_identity_created_snapshot_idx",
                "event_map_canonical_identity_retired_snapshot_idx",
            },
            "event_map_topic": {"event_map_topic_anchor_idx"},
            "event_map_story_edge": {"event_map_story_edge_story_idx"},
        }
        definitions = (
            "create table event_map_snapshot (parent_snapshot_id text)",
            "create table event_map_canonical_identity (created_snapshot_id text, retired_snapshot_id text)",
            "create table event_map_topic (snapshot_id text, anchor_canonical_id text)",
            "create table event_map_story_edge (snapshot_id text, story_id text)",
        )
        for create_indexes in (
            _create_runtime_event_analysis_indexes,
            _create_data_event_analysis_indexes,
        ):
            with self.subTest(create_indexes=create_indexes.__module__):
                with tempfile.TemporaryDirectory() as tmp:
                    engine = create_engine(f"sqlite:///{Path(tmp) / 'schema.sqlite'}")
                    try:
                        with engine.begin() as conn:
                            for definition in definitions:
                                conn.execute(text(definition))
                            create_indexes(conn)
                            inspector = inspect(conn)
                            for table_name, expected_names in expected_by_table.items():
                                names = {index["name"] for index in inspector.get_indexes(table_name)}
                                self.assertTrue(expected_names.issubset(names))
                    finally:
                        engine.dispose()

    def test_runtime_and_data_migrations_add_scene_topic_member_index(self) -> None:
        for create_indexes in (
            _create_runtime_event_analysis_indexes,
            _create_data_event_analysis_indexes,
        ):
            with self.subTest(create_indexes=create_indexes.__module__):
                with tempfile.TemporaryDirectory() as tmp:
                    engine = create_engine(f"sqlite:///{Path(tmp) / 'schema.sqlite'}")
                    try:
                        with engine.begin() as conn:
                            conn.execute(
                                text(
                                    "create table event_map_topic_member ("
                                    "snapshot_id text not null, canonical_id text not null, "
                                    "level integer not null, topic_id text not null, "
                                    "primary key (snapshot_id, canonical_id, level))"
                                )
                            )
                            create_indexes(conn)
                            self.assertIn(
                                "event_map_topic_member_level_canonical_idx",
                                {
                                    index["name"]
                                    for index in inspect(conn).get_indexes("event_map_topic_member")
                                },
                            )
                    finally:
                        engine.dispose()

    def test_runtime_and_data_migrations_add_execution_ownership_columns(self) -> None:
        for migrate_schema in (_migrate_runtime_schema, _migrate_data_schema):
            with self.subTest(migrate_schema=migrate_schema.__module__):
                with tempfile.TemporaryDirectory() as tmp:
                    engine = create_engine(f"sqlite:///{Path(tmp) / 'schema.sqlite'}")
                    try:
                        with engine.begin() as conn:
                            conn.execute(
                                text(
                                    "create table event_map_snapshot ("
                                    "id text primary key, job_id text, job_attempt integer not null default 0, "
                                    "monthly_distribution json)"
                                )
                            )
                            conn.execute(
                                text(
                                    "create table job ("
                                    "id text primary key, type text, status text, priority integer, "
                                    "scheduled_for datetime, created_at datetime, started_at datetime, "
                                    "finished_at datetime, lease_expires_at datetime, worker_id text, "
                                    "error_message text)"
                                )
                            )
                            conn.execute(
                                text(
                                    "insert into event_map_snapshot (id, job_id, job_attempt) "
                                    "values ('snapshot-1', 'job-1', 1)"
                                )
                            )
                            conn.execute(
                                text(
                                    "insert into job (id, type, status, priority, scheduled_for, created_at) "
                                    "values ('job-1', 'playlist.build_event_map_snapshot', 'pending', 0, "
                                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                                )
                            )
                            migrate_schema(conn)

                            inspector = inspect(conn)
                            snapshot_columns = {
                                column["name"]: column for column in inspector.get_columns("event_map_snapshot")
                            }
                            job_columns = {column["name"]: column for column in inspector.get_columns("job")}
                            self.assertIn("entity_count", snapshot_columns)
                            self.assertFalse(snapshot_columns["entity_count"]["nullable"])
                            self.assertIn("execution_token", snapshot_columns)
                            self.assertIn("execution_token", job_columns)
                            self.assertEqual(
                                conn.execute(
                                    text(
                                        "select entity_count, execution_token "
                                        "from event_map_snapshot where id = 'snapshot-1'"
                                    )
                                ).one(),
                                (0, None),
                            )
                            self.assertIsNone(
                                conn.execute(text("select execution_token from job where id = 'job-1'")).scalar_one()
                            )
                            self.assertIn(
                                "event_map_snapshot_job_execution_ux",
                                {index["name"] for index in inspector.get_indexes("event_map_snapshot")},
                            )

                            first_token = str(uuid.uuid4())
                            second_token = str(uuid.uuid4())
                            conn.execute(
                                text(
                                    "insert into event_map_snapshot "
                                    "(id, job_id, job_attempt, execution_token) "
                                    "values ('snapshot-2', 'job-2', 1, :execution_token)"
                                ),
                                {"execution_token": first_token},
                            )
                            conn.execute(
                                text(
                                    "insert into event_map_snapshot "
                                    "(id, job_id, job_attempt, execution_token) "
                                    "values ('snapshot-3', 'job-2', 1, :execution_token)"
                                ),
                                {"execution_token": second_token},
                            )
                            with self.assertRaises(IntegrityError), conn.begin_nested():
                                conn.execute(
                                    text(
                                        "insert into event_map_snapshot "
                                        "(id, job_id, job_attempt, execution_token) "
                                        "values ('snapshot-4', 'job-2', 2, :execution_token)"
                                    ),
                                    {"execution_token": first_token},
                                )
                    finally:
                        engine.dispose()

    def test_runtime_and_data_migrations_add_story_evidence_graph_columns(self) -> None:
        expected = {
            "event_map_story": {"anchor_key", "maturity", "quality_score"},
            "event_map_story_edge": {"evidence_json"},
            "event_map_story_history_revision": {"anchor_key", "maturity", "quality_score"},
        }
        for migrate_schema in (_migrate_runtime_schema, _migrate_data_schema):
            with self.subTest(migrate_schema=migrate_schema.__module__):
                with tempfile.TemporaryDirectory() as tmp:
                    engine = create_engine(f"sqlite:///{Path(tmp) / 'story-schema.sqlite'}")
                    try:
                        with engine.begin() as conn:
                            conn.execute(text("create table event_map_story (story_id text primary key)"))
                            conn.execute(text("create table event_map_story_edge (edge_id text primary key)"))
                            conn.execute(
                                text(
                                    "create table event_map_story_history_revision "
                                    "(id text primary key)"
                                )
                            )
                            migrate_schema(conn)
                            inspector = inspect(conn)
                            for table_name, expected_columns in expected.items():
                                observed = {
                                    column["name"]
                                    for column in inspector.get_columns(table_name)
                                }
                                self.assertTrue(expected_columns.issubset(observed))
                    finally:
                        engine.dispose()

    def test_runtime_and_data_migrations_backfill_story_identity_reading_metadata(self) -> None:
        for migrate_schema in (_migrate_runtime_schema, _migrate_data_schema):
            with self.subTest(migrate_schema=migrate_schema.__module__):
                with tempfile.TemporaryDirectory() as tmp:
                    engine = create_engine(f"sqlite:///{Path(tmp) / 'story-identity.sqlite'}")
                    try:
                        with engine.begin() as conn:
                            conn.execute(
                                text(
                                    "create table event_map_story_identity ("
                                    "id text primary key, playlist_id text not null, status text not null, "
                                    "created_snapshot_id text, retired_snapshot_id text, "
                                    "created_at datetime not null, updated_at datetime not null)"
                                )
                            )
                            conn.execute(
                                text(
                                    "create table event_map_story_history_revision ("
                                    "id text primary key, story_identity_id text not null, snapshot_id text not null, "
                                    "title text not null, observed_at datetime not null)"
                                )
                            )
                            conn.execute(
                                text(
                                    "create table event_map_change ("
                                    "id text primary key, object_id text not null, object_type text not null, "
                                    "change_type text not null, to_snapshot_id text not null, "
                                    "observed_at datetime not null)"
                                )
                            )
                            conn.execute(
                                text(
                                    "insert into event_map_story_identity "
                                    "(id, playlist_id, status, created_snapshot_id, created_at, updated_at) "
                                    "values ('story-1', 'playlist-1', 'active', 'snapshot-1', "
                                    "'2026-08-01 00:00:00', '2026-08-01 00:00:00')"
                                )
                            )
                            conn.execute(
                                text(
                                    "insert into event_map_story_history_revision "
                                    "(id, story_identity_id, snapshot_id, title, observed_at) values "
                                    "('revision-1', 'story-1', 'snapshot-1', '最早稳定标题', '2026-08-01 01:00:00'), "
                                    "('revision-2', 'story-1', 'snapshot-2', '后来措辞标题', '2026-08-02 01:00:00')"
                                )
                            )
                            conn.execute(
                                text(
                                    "insert into event_map_change "
                                    "(id, object_id, object_type, change_type, to_snapshot_id, observed_at) values "
                                    "('change-1', 'story-1', 'story', 'story_added', 'snapshot-1', "
                                    "'2026-08-01 01:00:00'), "
                                    "('change-2', 'story-1', 'story', 'story_maturity_changed', 'snapshot-2', "
                                    "'2026-08-02 01:00:00'), "
                                    "('change-3', 'story-1', 'story', 'story_summary_changed', 'snapshot-3', "
                                    "'2026-08-03 01:00:00')"
                                )
                            )

                            migrate_schema(conn)

                            row = conn.execute(
                                text(
                                    "select stable_title, last_material_snapshot_id, last_material_changed_at "
                                    "from event_map_story_identity where id = 'story-1'"
                                )
                            ).one()
                            self.assertEqual(row[0], "最早稳定标题")
                            self.assertEqual(row[1], "snapshot-2")
                            self.assertEqual(str(row[2]), "2026-08-02 01:00:00")
                            conn.execute(
                                text(
                                    "insert into event_map_story_history_revision "
                                    "(id, story_identity_id, snapshot_id, title, observed_at) values "
                                    "('revision-0', 'story-1', 'snapshot-0', '后来补录的更早标题', "
                                    "'2026-07-31 01:00:00')"
                                )
                            )
                            migrate_schema(conn)
                            self.assertEqual(
                                conn.execute(
                                    text(
                                        "select stable_title from event_map_story_identity "
                                        "where id = 'story-1'"
                                    )
                                ).scalar_one(),
                                "最早稳定标题",
                            )
                            self.assertIn(
                                "event_map_story_identity_material_idx",
                                {
                                    index["name"]
                                    for index in inspect(conn).get_indexes(
                                        "event_map_story_identity"
                                    )
                                },
                            )
                    finally:
                        engine.dispose()

    def test_runtime_and_data_migrations_reject_active_identity_without_a_title(self) -> None:
        for migrate_schema in (_migrate_runtime_schema, _migrate_data_schema):
            with self.subTest(migrate_schema=migrate_schema.__module__):
                with tempfile.TemporaryDirectory() as tmp:
                    engine = create_engine(f"sqlite:///{Path(tmp) / 'broken-story-identity.sqlite'}")
                    try:
                        with self.assertRaisesRegex(RuntimeError, "cannot backfill stable_title"):
                            with engine.begin() as conn:
                                conn.execute(
                                    text(
                                        "create table event_map_story_identity ("
                                        "id text primary key, playlist_id text not null, status text not null, "
                                        "created_snapshot_id text, retired_snapshot_id text, "
                                        "created_at datetime not null, updated_at datetime not null)"
                                    )
                                )
                                conn.execute(
                                    text(
                                        "insert into event_map_story_identity "
                                        "(id, playlist_id, status, created_at, updated_at) values "
                                        "('story-without-title', 'playlist-1', 'active', "
                                        "'2026-08-01 00:00:00', '2026-08-01 00:00:00')"
                                    )
                                )
                                migrate_schema(conn)
                    finally:
                        engine.dispose()

    def test_runtime_migration_only_seeds_unlinked_legacy_story_identities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = create_engine(f"sqlite:///{Path(tmp) / 'legacy-story-identity.sqlite'}")
            try:
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "create table event_map_snapshot ("
                            "id text primary key, playlist_id text not null, job_id text)"
                        )
                    )
                    conn.execute(
                        text(
                            "create table event_map_story_identity ("
                            "id text primary key, playlist_id text not null, status text not null, "
                            "created_snapshot_id text, retired_snapshot_id text, "
                            "created_at datetime not null, updated_at datetime not null)"
                        )
                    )
                    conn.execute(
                        text(
                            "create table event_map_story ("
                            "snapshot_id text not null, story_id text not null, "
                            "story_identity_id text, title text not null, created_at datetime not null)"
                        )
                    )
                    conn.execute(
                        text(
                            "insert into event_map_snapshot (id, playlist_id, job_id) values "
                            "('snapshot-1', 'playlist-1', null), "
                            "('snapshot-2', 'playlist-1', null)"
                        )
                    )
                    conn.execute(
                        text(
                            "insert into event_map_story_identity "
                            "(id, playlist_id, status, created_snapshot_id, created_at, updated_at) "
                            "values ('stable-story', 'playlist-1', 'active', 'snapshot-1', "
                            "'2026-08-01 00:00:00', '2026-08-01 00:00:00')"
                        )
                    )
                    conn.execute(
                        text(
                            "insert into event_map_story "
                            "(snapshot_id, story_id, story_identity_id, title, created_at) values "
                            "('snapshot-1', 'already-linked-story', 'stable-story', "
                            "'已连接稳定身份', '2026-08-01 00:00:00'), "
                            "('snapshot-2', 'legacy-unlinked-story', null, "
                            "'旧快照故事', '2026-08-02 00:00:00')"
                        )
                    )

                    _migrate_runtime_schema(conn)

                    self.assertEqual(
                        conn.execute(
                            text("select count(*) from event_map_story_identity")
                        ).scalar_one(),
                        2,
                    )
                    self.assertIsNone(
                        conn.execute(
                            text(
                                "select id from event_map_story_identity "
                                "where id = 'already-linked-story'"
                            )
                        ).scalar_one_or_none()
                    )
                    self.assertEqual(
                        conn.execute(
                            text(
                                "select story_identity_id from event_map_story "
                                "where story_id = 'legacy-unlinked-story'"
                            )
                        ).scalar_one(),
                        "legacy-unlinked-story",
                    )
            finally:
                engine.dispose()


class EventMapJobTests(unittest.TestCase):
    def test_job_names_have_stable_per_playlist_dedupe_keys(self) -> None:
        playlist_id = uuid.uuid4()
        build_key, build_params = _normalize_dedupe_key_and_params(
            "playlist.build_event_map_snapshot",
            {"playlist_id": str(playlist_id), "trigger": "dirty"},
        )
        dirty_key, dirty_params = _normalize_dedupe_key_and_params(
            "playlist.mark_event_map_dirty",
            {"playlist_id": str(playlist_id), "reason": "embedding_ready"},
        )
        prune_key, prune_params = _normalize_dedupe_key_and_params(
            "playlist.prune_event_map_snapshots",
            {"playlist_id": str(playlist_id)},
        )
        self.assertEqual(build_key, f"playlist_event_map_build:{playlist_id}")
        self.assertEqual(dirty_key, f"playlist_event_map_dirty:{playlist_id}")
        self.assertEqual(prune_key, f"playlist_event_map_prune:{playlist_id}")
        self.assertEqual(build_params["playlist_id"], str(playlist_id))
        self.assertEqual(dirty_params["reason"], "embedding_ready")
        self.assertEqual(prune_params["playlist_id"], str(playlist_id))
        self.assertEqual(_default_max_attempts("playlist.build_event_map_snapshot"), 2)
        self.assertEqual(_default_max_attempts("playlist.prune_event_map_snapshots"), 2)

    def test_new_jobs_are_owned_by_analysis_role_and_old_jobs_are_not_registered(self) -> None:
        self.assertEqual(
            WORKER_ROLE_TYPES["analysis"],
            [
                "playlist.mark_event_map_dirty",
                "playlist.build_event_map_snapshot",
                "playlist.prune_event_map_snapshots",
            ],
        )
        self.assertEqual(job_type_worker_role("playlist.mark_event_map_dirty"), "analysis")
        self.assertEqual(job_type_worker_role("playlist.build_event_map_snapshot"), "analysis")
        self.assertEqual(job_type_worker_role("playlist.prune_event_map_snapshots"), "analysis")
        self.assertIsNone(job_type_worker_role("playlist.mark_event_regime_dirty"))
        self.assertIsNone(job_type_worker_role("playlist.build_event_regime_snapshot"))

    def test_microbatch_waits_for_quiet_period_but_never_past_fifteen_minutes(self) -> None:
        first_dirty_at = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
        state = EventMapState(playlist_id=uuid.uuid4(), first_dirty_at=first_dirty_at)
        self.assertEqual(
            _event_map_build_scheduled_for(state, first_dirty_at + timedelta(minutes=1)),
            first_dirty_at + timedelta(minutes=3),
        )
        self.assertEqual(
            _event_map_build_scheduled_for(state, first_dirty_at + timedelta(minutes=14, seconds=30)),
            first_dirty_at + timedelta(minutes=15),
        )


class LegacyEventAnalysisMigrationTests(unittest.TestCase):
    @staticmethod
    def _create_schema(database_url: str) -> None:
        engine = create_engine(database_url)
        try:
            with engine.begin() as conn:
                conn.execute(text("create table event_regime_run (id text primary key, status text not null)"))
                conn.execute(
                    text(
                        "create table event_regime_state (playlist_id text primary key, last_ready_run_id text)"
                    )
                )
                conn.execute(text("create table event_regime_signal (id text primary key)"))
                conn.execute(text("create table event_regime_candidate (id text primary key)"))
                conn.execute(text("create table event_graph_projection_point (id text primary key)"))
                conn.execute(
                    text(
                        "create table event_map_snapshot (id text primary key, playlist_id text not null, status text not null)"
                    )
                )
                conn.execute(
                    text(
                        "create table event_map_state (playlist_id text primary key, current_snapshot_id text)"
                    )
                )
                conn.execute(
                    text(
                        "create table job ("
                        "id text primary key, type text, status text, error_message text, finished_at datetime, "
                        "lease_expires_at datetime, worker_id text, execution_token text)"
                    )
                )
                old_run_id = str(uuid.uuid4())
                playlist_id = str(uuid.uuid4())
                conn.execute(
                    text("insert into event_regime_run (id, status) values (:id, 'ready')"),
                    {"id": old_run_id},
                )
                conn.execute(
                    text(
                        "insert into event_regime_state (playlist_id, last_ready_run_id) values (:playlist_id, :run_id)"
                    ),
                    {"playlist_id": playlist_id, "run_id": old_run_id},
                )
        finally:
            engine.dispose()

    def test_cleanup_is_blocked_until_the_same_playlist_has_a_current_ready_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_url = f"sqlite:///{Path(temp_dir) / 'migration.sqlite3'}"
            self._create_schema(database_url)
            engine = create_engine(database_url)
            try:
                with engine.begin() as conn:
                    blockers = _legacy_event_analysis_drop_blockers(conn)
                    self.assertEqual(len(blockers), 1)
                    self.assertIn("lack a current ready", blockers[0])

                    playlist_id = conn.execute(text("select playlist_id from event_regime_state")).scalar_one()
                    snapshot_id = str(uuid.uuid4())
                    conn.execute(
                        text(
                            "insert into event_map_snapshot (id, playlist_id, status) "
                            "values (:id, :playlist_id, 'ready')"
                        ),
                        {"id": snapshot_id, "playlist_id": playlist_id},
                    )
                    conn.execute(
                        text(
                            "insert into event_map_state (playlist_id, current_snapshot_id) "
                            "values (:playlist_id, :snapshot_id)"
                        ),
                        {"playlist_id": playlist_id, "snapshot_id": snapshot_id},
                    )
                    self.assertEqual(_legacy_event_analysis_drop_blockers(conn), [])
            finally:
                engine.dispose()

    def test_explicit_cleanup_preserves_job_history_and_never_runs_at_schema_startup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_url = f"sqlite:///{Path(temp_dir) / 'migration.sqlite3'}"
            self._create_schema(database_url)
            engine = create_engine(database_url)
            try:
                with engine.begin() as conn:
                    playlist_id = conn.execute(text("select playlist_id from event_regime_state")).scalar_one()
                    snapshot_id = str(uuid.uuid4())
                    job_id = str(uuid.uuid4())
                    conn.execute(
                        text(
                            "insert into event_map_snapshot (id, playlist_id, status) "
                            "values (:id, :playlist_id, 'ready')"
                        ),
                        {"id": snapshot_id, "playlist_id": playlist_id},
                    )
                    conn.execute(
                        text(
                            "insert into event_map_state (playlist_id, current_snapshot_id) "
                            "values (:playlist_id, :snapshot_id)"
                        ),
                        {"playlist_id": playlist_id, "snapshot_id": snapshot_id},
                    )
                    conn.execute(
                        text(
                            "insert into job (id, type, status, execution_token) "
                            "values (:id, 'playlist.build_event_regime_snapshot', 'pending', :execution_token)"
                        ),
                        {"id": job_id, "execution_token": str(uuid.uuid4())},
                    )
            finally:
                engine.dispose()

            dry_run = _drop_legacy_event_analysis(database_url=database_url, yes=False)
            self.assertFalse(dry_run["executed"])
            self.assertEqual(dry_run["pending_jobs"], 1)

            executed = _drop_legacy_event_analysis(database_url=database_url, yes=True)
            self.assertTrue(executed["executed"])
            engine = create_engine(database_url)
            try:
                with engine.begin() as conn:
                    tables = set(inspect(conn).get_table_names())
                    self.assertNotIn("event_regime_run", tables)
                    self.assertNotIn("event_regime_signal", tables)
                    status, execution_token = conn.execute(
                        text("select status, execution_token from job where id = :id"),
                        {"id": job_id},
                    ).one()
                    self.assertEqual(status, "canceled")
                    self.assertIsNone(execution_token)
            finally:
                engine.dispose()

    def test_explicit_cleanup_refuses_to_drop_tables_while_an_old_job_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_url = f"sqlite:///{Path(temp_dir) / 'migration.sqlite3'}"
            self._create_schema(database_url)
            engine = create_engine(database_url)
            try:
                with engine.begin() as conn:
                    playlist_id = conn.execute(text("select playlist_id from event_regime_state")).scalar_one()
                    snapshot_id = str(uuid.uuid4())
                    conn.execute(
                        text(
                            "insert into event_map_snapshot (id, playlist_id, status) "
                            "values (:id, :playlist_id, 'ready')"
                        ),
                        {"id": snapshot_id, "playlist_id": playlist_id},
                    )
                    conn.execute(
                        text(
                            "insert into event_map_state (playlist_id, current_snapshot_id) "
                            "values (:playlist_id, :snapshot_id)"
                        ),
                        {"playlist_id": playlist_id, "snapshot_id": snapshot_id},
                    )
                    conn.execute(
                        text(
                            "insert into job (id, type, status) "
                            "values (:id, 'playlist.build_event_regime_snapshot', 'running')"
                        ),
                        {"id": str(uuid.uuid4())},
                    )
            finally:
                engine.dispose()

            with self.assertRaisesRegex(RuntimeError, "still running"):
                _drop_legacy_event_analysis(database_url=database_url, yes=True)
            engine = create_engine(database_url)
            try:
                self.assertIn("event_regime_run", set(inspect(engine).get_table_names()))
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
