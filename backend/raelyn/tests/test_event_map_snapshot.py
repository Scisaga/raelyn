from __future__ import annotations

from contextlib import nullcontext
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
import uuid
from unittest.mock import MagicMock, patch

import numpy as np
from sqlalchemy.dialects import postgresql


_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.models import EventMapSnapshot, Playlist
from raelyn.services.event_map_domain import (
    EventMapCanonicalGroup,
    EventMapEntityRef,
    EventMapMemberDecision,
    EventMapRecord,
)
from raelyn.services.event_map_snapshot import (
    _DiskRowBuffer,
    PreviousEventMapSnapshot,
    _anchored_coordinates,
    _assign_canonical_identities,
    _build_snapshot_rows,
    _checkpoint,
    _create_or_resume_snapshot,
    _event_map_build_lock,
    _embedding_vector_checksum,
    _finalize_failed_snapshot,
    _frozen_event_map_state,
    _build_event_map_snapshot_locked,
    _monthly_distribution,
    _skipped_reason_counts,
    _stage_canonical_centroids,
    StagedEventMapInputs,
    FrozenEventMapState,
)
from raelyn.jobs.reschedule import JobTerminalFailure


def _record(index: int, vector_index: int | None = None) -> EventMapRecord:
    return EventMapRecord(
        event_id=uuid.UUID(int=index + 1),
        revision_id=uuid.UUID(int=100 + index),
        vector_index=index if vector_index is None else vector_index,
        title=f"事件{index}",
        summary="",
        event_type="policy",
        direction="neutral",
        start_day=index + 1,
        end_day=index + 1,
        time_precision="day",
    )


class EventMapSnapshotTests(unittest.TestCase):
    def test_frozen_state_includes_active_dirty_job_from_the_same_reader(self) -> None:
        playlist_id = uuid.uuid4()
        snapshot_id = uuid.uuid4()
        dirty_job_id = uuid.uuid4()
        reader = MagicMock()
        reader.execute.side_effect = [
            SimpleNamespace(one_or_none=lambda: (7, snapshot_id)),
            SimpleNamespace(scalar_one_or_none=lambda: dirty_job_id),
        ]

        frozen = _frozen_event_map_state(reader, playlist_id=playlist_id)

        self.assertEqual(frozen.input_generation, 7)
        self.assertEqual(frozen.parent_snapshot_id, snapshot_id)
        self.assertEqual(frozen.active_dirty_job_id, dirty_job_id)
        self.assertEqual(reader.execute.call_count, 2)
        dirty_statement = str(
            reader.execute.call_args_list[1]
            .args[0]
            .compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        ).lower()
        self.assertIn("playlist.mark_event_map_dirty", dirty_statement)
        self.assertIn("pending", dirty_statement)
        self.assertIn("running", dirty_statement)

    def test_active_dirty_job_supersedes_build_before_snapshot_staging(self) -> None:
        playlist_id = uuid.uuid4()
        dirty_job_id = uuid.uuid4()
        playlist = Playlist(id=playlist_id, name="p")
        session = MagicMock()
        session.get.return_value = playlist
        reader = MagicMock()

        with patch(
            "raelyn.services.event_map_snapshot.embedding_spec",
            return_value=SimpleNamespace(model="test-model", dim=8),
        ):
            with patch(
                "raelyn.services.event_map_snapshot.shutil.disk_usage",
                return_value=SimpleNamespace(free=6 * 1024 * 1024 * 1024),
            ):
                with patch(
                    "raelyn.services.event_map_snapshot._event_map_snapshot_reader",
                    return_value=nullcontext(reader),
                ):
                    with patch(
                        "raelyn.services.event_map_snapshot._frozen_event_map_state",
                        return_value=FrozenEventMapState(
                            input_generation=7,
                            parent_snapshot_id=None,
                            active_dirty_job_id=dirty_job_id,
                        ),
                    ):
                        with patch(
                            "raelyn.services.event_map_snapshot._create_or_resume_snapshot"
                        ) as create_snapshot:
                            result = _build_event_map_snapshot_locked(
                                session,
                                playlist_id=playlist_id,
                                job=None,
                            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["outcome"], "superseded_by_active_dirty")
        self.assertEqual(result["active_dirty_job_id"], str(dirty_job_id))
        self.assertEqual(result["frozen_generation"], 7)
        create_snapshot.assert_not_called()

    def test_ready_snapshot_for_same_execution_is_reused_without_restaging(self) -> None:
        playlist_id = uuid.uuid4()
        snapshot = EventMapSnapshot(id=uuid.uuid4(), status="ready")
        playlist = Playlist(id=playlist_id, name="p")
        session = MagicMock()
        session.get.return_value = playlist
        reader = MagicMock()

        with patch(
            "raelyn.services.event_map_snapshot.embedding_spec",
            return_value=SimpleNamespace(model="test-model", dim=8),
        ):
            with patch(
                "raelyn.services.event_map_snapshot.shutil.disk_usage",
                return_value=SimpleNamespace(free=6 * 1024 * 1024 * 1024),
            ):
                with patch(
                    "raelyn.services.event_map_snapshot._event_map_snapshot_reader",
                    return_value=nullcontext(reader),
                ):
                    with patch(
                        "raelyn.services.event_map_snapshot._frozen_event_map_state",
                        return_value=FrozenEventMapState(
                            input_generation=9,
                            parent_snapshot_id=None,
                            active_dirty_job_id=None,
                        ),
                    ):
                        with patch(
                            "raelyn.services.event_map_snapshot._create_or_resume_snapshot",
                            return_value=(snapshot, 9, None),
                        ):
                            with patch(
                                "raelyn.services.event_map_snapshot._stage_inputs"
                            ) as stage_inputs:
                                result = _build_event_map_snapshot_locked(
                                    session,
                                    playlist_id=playlist_id,
                                    job=None,
                                )

        self.assertTrue(result["ok"])
        self.assertTrue(result["cached"])
        self.assertEqual(result["outcome"], "reused_execution_snapshot")
        self.assertEqual(result["snapshot_id"], str(snapshot.id))
        self.assertEqual(result["frozen_generation"], 9)
        stage_inputs.assert_not_called()

    def test_snapshot_output_rows_are_spooled_and_read_in_fixed_batches(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "rows.bin"
            rows = _DiskRowBuffer(path)
            for index in range(5001):
                rows.append({"point_index": index, "payload": "x" * 64})

            batches = list(rows.iter_batches(2000))

            self.assertEqual(len(rows), 5001)
            self.assertEqual([len(batch) for batch in batches], [2000, 2000, 1001])
            self.assertEqual(batches[-1][-1]["point_index"], 5000)
            rows.discard()
            self.assertFalse(path.exists())

    def test_snapshot_rows_stream_entity_index_from_frozen_record(self) -> None:
        record = EventMapRecord(
            event_id=uuid.uuid4(),
            revision_id=uuid.uuid4(),
            vector_index=0,
            title="美国公布政策",
            summary="",
            event_type="policy",
            direction="neutral",
            start_day=10,
            end_day=10,
            time_precision="day",
            entities=(EventMapEntityRef("country", "United States", "United States", "actor", 1.0),),
        )
        decision = EventMapMemberDecision(0, "singleton", None, None, ("no_unambiguous_match",))
        group = EventMapCanonicalGroup([0], 0, {0: decision})
        snapshot = EventMapSnapshot(id=uuid.uuid4())
        staged = StagedEventMapInputs(
            projection=SimpleNamespace(categories=[{"value": "policy", "code": 0}]),
            records=[record],
            revisions_path=Path("unused"),
            input_fingerprint="input",
            embedding_checksum="embedding",
            skipped_reason_counts={},
        )

        with TemporaryDirectory() as directory:
            rows = _build_snapshot_rows(
                directory=Path(directory),
                snapshot=snapshot,
                playlist_id=uuid.uuid4(),
                staged=staged,
                groups=[group],
                canonical_ids=[uuid.uuid4()],
                identity_states=["new"],
                lineage_rows=[],
                topics=[],
                topic_by_group=[],
                stories=[],
                coordinates=np.asarray([[1.0, 2.0, 3.0]], dtype=np.float32),
                canonical_vectors=np.asarray([[1.0, 0.0]], dtype=np.float32),
                checkpoint=lambda _value: None,
            )
            entity_batches = list(rows["entity"].iter_batches(10))

            self.assertEqual(len(entity_batches), 1)
            self.assertEqual(entity_batches[0][0]["entity_type"], "country")
            self.assertEqual(entity_batches[0][0]["normalized_key"], "unitedstates")
            self.assertEqual(entity_batches[0][0]["record_count"], 1)
            for buffer in rows.values():
                buffer.discard()

    def test_snapshot_rows_contain_only_active_object_buffers(self) -> None:
        record = _record(0)
        decision = EventMapMemberDecision(0, "singleton", None, None, ("no_unambiguous_match",))
        group = EventMapCanonicalGroup([0], 0, {0: decision})
        snapshot = EventMapSnapshot(id=uuid.uuid4())
        staged = StagedEventMapInputs(
            projection=SimpleNamespace(categories=[{"value": "policy", "code": 0}]),
            records=[record],
            revisions_path=Path("unused"),
            input_fingerprint="input",
            embedding_checksum="embedding",
            skipped_reason_counts={},
        )
        topics = []

        with TemporaryDirectory() as directory:
            rows = _build_snapshot_rows(
                directory=Path(directory),
                snapshot=snapshot,
                playlist_id=uuid.uuid4(),
                staged=staged,
                groups=[group],
                canonical_ids=[uuid.uuid4()],
                identity_states=["new"],
                lineage_rows=[],
                topics=topics,
                topic_by_group=[],
                stories=[],
                coordinates=np.asarray([[1.0, 2.0, 3.0]], dtype=np.float32),
                canonical_vectors=np.asarray([[1.0, 0.0]], dtype=np.float32),
                checkpoint=lambda _value: None,
            )
            self.assertEqual(
                set(rows),
                {"identity", "canonical", "member", "lineage", "topic", "topic_member", "story_identity", "story", "story_member", "story_edge", "anchor", "entity"},
            )
            for buffer in rows.values():
                buffer.discard()

    def test_vector_checksum_changes_when_embedding_changes_with_the_same_text(self) -> None:
        first = _embedding_vector_checksum([1.0, 0.0], 2)
        second = _embedding_vector_checksum([0.0, 1.0], 2)
        self.assertNotEqual(first, second)
        with self.assertRaisesRegex(RuntimeError, "NaN"):
            _embedding_vector_checksum([float("nan"), 1.0], 2)

    def test_skipped_reason_buckets_are_mutually_exclusive(self) -> None:
        reasons = _skipped_reason_counts(total=161_986, ready=161_022, eligible=161_012, failed=4)
        self.assertEqual(
            reasons,
            {"missing_event_time": 10, "embedding_failed": 4, "embedding_not_ready": 960},
        )
        self.assertEqual(sum(reasons.values()), 161_986 - 161_012)

    def test_checkpoint_rejects_a_job_that_lost_its_worker_lease(self) -> None:
        execution_token = uuid.uuid4()
        job = SimpleNamespace(
            id=uuid.uuid4(),
            worker_id="analysis-worker",
            execution_token=execution_token,
        )
        with patch("raelyn.services.event_map_snapshot.raise_if_job_cancel_requested"):
            with patch(
                "raelyn.services.event_map_snapshot.set_job_progress",
                return_value=False,
            ) as set_progress:
                with self.assertRaises(JobTerminalFailure):
                    _checkpoint(
                        MagicMock(),
                        job,
                        100,
                        peak=[0],
                        claimed_worker_id="original-worker",
                        claimed_execution_token=execution_token,
                    )
        self.assertEqual(set_progress.call_args.kwargs["worker_id"], "original-worker")
        self.assertEqual(set_progress.call_args.kwargs["execution_token"], execution_token)
        self.assertEqual(set_progress.call_args.kwargs["current"], 100)
        self.assertEqual(set_progress.call_args.kwargs["total"], 10000)

    def test_stale_execution_cannot_mark_snapshot_or_state_failed(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None
        job = SimpleNamespace(id=uuid.uuid4())

        finalized = _finalize_failed_snapshot(
            session,
            snapshot_id=uuid.uuid4(),
            playlist_id=uuid.uuid4(),
            job=job,
            claimed_worker_id="old-worker",
            claimed_execution_token=uuid.uuid4(),
            status="failed",
            message="old execution failed late",
        )

        self.assertFalse(finalized)
        session.get.assert_not_called()
        session.flush.assert_not_called()
        self.assertEqual(session.rollback.call_count, 2)

    def test_stale_execution_cannot_create_or_resume_staging_snapshot(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None

        with self.assertRaisesRegex(JobTerminalFailure, "ownership changed before staging"):
            _create_or_resume_snapshot(
                session,
                playlist_id=uuid.uuid4(),
                input_generation=7,
                parent_snapshot_id=None,
                job=SimpleNamespace(id=uuid.uuid4(), attempt=2),
                worker_id="old-worker",
                execution_token=uuid.uuid4(),
                model="test-model",
                dimension=8,
            )

        session.get.assert_not_called()
        session.add.assert_not_called()
        session.rollback.assert_called_once()

    def test_build_lock_uses_one_dedicated_connection_across_session_commits(self) -> None:
        session = MagicMock()
        engine = MagicMock()
        engine.dialect.name = "postgresql"
        connection = MagicMock()
        engine.connect.return_value.__enter__.return_value = connection
        session.get_bind.return_value = engine
        connection.execute.side_effect = [
            SimpleNamespace(scalar_one=lambda: True),
            SimpleNamespace(),
        ]

        with _event_map_build_lock(session) as acquired:
            self.assertTrue(acquired)
            session.commit()

        self.assertEqual(connection.execute.call_count, 2)
        self.assertIn("pg_try_advisory_lock", str(connection.execute.call_args_list[0].args[0]))
        self.assertIn("pg_advisory_unlock", str(connection.execute.call_args_list[1].args[0]))
        connection.commit.assert_called_once()

    def test_identity_is_retained_only_for_a_dominant_one_to_one_overlap(self) -> None:
        playlist_id = uuid.uuid4()
        snapshot_id = uuid.uuid4()
        previous_id = uuid.uuid4()
        records = [_record(0), _record(1), _record(2)]
        groups = [
            SimpleNamespace(member_indices=[0, 1]),
            SimpleNamespace(member_indices=[2]),
        ]
        previous = PreviousEventMapSnapshot(
            snapshot=None,
            event_to_canonical={records[0].event_id: previous_id, records[1].event_id: previous_id},
            canonical_member_counts={previous_id: 2},
            canonical_coordinates={previous_id: (1.0, 2.0, 3.0)},
            revision_embeddings={},
            anchor_ids=[],
            anchor_vectors=np.empty((0, 2), dtype=np.float32),
            anchor_coordinates=np.empty((0, 3), dtype=np.float32),
        )

        canonical_ids, states, lineage = _assign_canonical_identities(
            playlist_id=playlist_id,
            snapshot_id=snapshot_id,
            records=records,
            groups=groups,
            previous=previous,
        )

        self.assertEqual(canonical_ids[0], previous_id)
        self.assertEqual(states, ["retained", "new"])
        self.assertEqual(lineage, [])

    def test_split_creates_new_canonical_ids_for_every_successor(self) -> None:
        playlist_id = uuid.uuid4()
        snapshot_id = uuid.uuid4()
        previous_id = uuid.uuid4()
        records = [_record(0), _record(1)]
        previous = PreviousEventMapSnapshot(
            snapshot=None,
            event_to_canonical={record.event_id: previous_id for record in records},
            canonical_member_counts={previous_id: 2},
            canonical_coordinates={},
            revision_embeddings={},
            anchor_ids=[],
            anchor_vectors=np.empty((0, 2), dtype=np.float32),
            anchor_coordinates=np.empty((0, 3), dtype=np.float32),
        )

        canonical_ids, states, lineage = _assign_canonical_identities(
            playlist_id=playlist_id,
            snapshot_id=snapshot_id,
            records=records,
            groups=[SimpleNamespace(member_indices=[0]), SimpleNamespace(member_indices=[1])],
            previous=previous,
        )

        self.assertEqual(states, ["new", "new"])
        self.assertNotIn(previous_id, canonical_ids)
        self.assertEqual({row["relation_type"] for row in lineage}, {"split"})

    def test_merge_creates_a_new_canonical_id(self) -> None:
        playlist_id = uuid.uuid4()
        snapshot_id = uuid.uuid4()
        left_id = uuid.uuid4()
        right_id = uuid.uuid4()
        records = [_record(0), _record(1)]
        previous = PreviousEventMapSnapshot(
            snapshot=None,
            event_to_canonical={records[0].event_id: left_id, records[1].event_id: right_id},
            canonical_member_counts={left_id: 1, right_id: 1},
            canonical_coordinates={},
            revision_embeddings={},
            anchor_ids=[],
            anchor_vectors=np.empty((0, 2), dtype=np.float32),
            anchor_coordinates=np.empty((0, 3), dtype=np.float32),
        )

        canonical_ids, states, lineage = _assign_canonical_identities(
            playlist_id=playlist_id,
            snapshot_id=snapshot_id,
            records=records,
            groups=[SimpleNamespace(member_indices=[0, 1])],
            previous=previous,
        )

        self.assertEqual(states, ["new"])
        self.assertNotIn(canonical_ids[0], {left_id, right_id})
        self.assertEqual(len(lineage), 2)
        self.assertEqual({row["relation_type"] for row in lineage}, {"merge"})

    def test_anchored_layout_keeps_retained_coordinate_bit_identical(self) -> None:
        old_id = uuid.uuid4()
        new_id = uuid.uuid4()
        previous = PreviousEventMapSnapshot(
            snapshot=None,
            event_to_canonical={},
            canonical_member_counts={},
            canonical_coordinates={old_id: (3.25, -7.5, 1.75)},
            revision_embeddings={},
            anchor_ids=[old_id, uuid.uuid4()],
            anchor_vectors=np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
            anchor_coordinates=np.asarray([[3.25, -7.5, 1.75], [-2.0, 4.0, -1.0]], dtype=np.float32),
        )
        canonical_vectors = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)

        coordinates = _anchored_coordinates(
            canonical_ids=[old_id, new_id],
            previous=previous,
            canonical_vectors=canonical_vectors,
        )

        self.assertEqual(coordinates[0].tobytes(), np.asarray([3.25, -7.5, 1.75], dtype=np.float32).tobytes())
        self.assertTrue(np.isfinite(coordinates[1]).all())
        self.assertGreater(float(np.linalg.norm(coordinates[1] - coordinates[0])), 0.0)

    def test_representative_change_does_not_change_centroid_or_anchor_input(self) -> None:
        records = [_record(0), _record(1)]
        raw_vectors = np.asarray([[2.0, 0.0], [0.0, 4.0]], dtype=np.float32)
        canonical_id = uuid.uuid4()
        previous = PreviousEventMapSnapshot(
            snapshot=None,
            event_to_canonical={},
            canonical_member_counts={},
            canonical_coordinates={},
            revision_embeddings={},
            anchor_ids=[uuid.uuid4(), uuid.uuid4()],
            anchor_vectors=np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
            anchor_coordinates=np.asarray([[1.0, 0.0, 0.25], [0.0, 1.0, -0.25]], dtype=np.float32),
        )

        with TemporaryDirectory() as raw_directory:
            root = Path(raw_directory)
            first_root = root / "first"
            second_root = root / "second"
            first_root.mkdir()
            second_root.mkdir()
            first = _stage_canonical_centroids(
                first_root,
                [SimpleNamespace(member_indices=[0, 1], representative_index=0)],
                records,
                raw_vectors,
                [canonical_id],
                10,
                lambda _processed: None,
            )
            second = _stage_canonical_centroids(
                second_root,
                [SimpleNamespace(member_indices=[0, 1], representative_index=1)],
                records,
                raw_vectors,
                [canonical_id],
                10,
                lambda _processed: None,
            )
            first_centroid = np.memmap(
                first.vectors_path,
                dtype=np.float32,
                mode="r",
                shape=(1, 2),
            ).copy()
            second_centroid = np.memmap(
                second.vectors_path,
                dtype=np.float32,
                mode="r",
                shape=(1, 2),
            ).copy()

        self.assertEqual(first_centroid.tobytes(), second_centroid.tobytes())
        first_coordinates = _anchored_coordinates(
            canonical_ids=[canonical_id],
            previous=previous,
            canonical_vectors=first_centroid,
        )
        second_coordinates = _anchored_coordinates(
            canonical_ids=[canonical_id],
            previous=previous,
            canonical_vectors=second_centroid,
        )
        self.assertEqual(first_coordinates.tobytes(), second_coordinates.tobytes())

    def test_monthly_distribution_treats_coarse_time_as_an_interval(self) -> None:
        january = date(2024, 1, 1).toordinal() - date(1970, 1, 1).toordinal()
        december = date(2024, 12, 31).toordinal() - date(1970, 1, 1).toordinal()

        values = _monthly_distribution(
            [{"event_start_day": january, "event_end_day": december, "member_count": 3}]
        )

        self.assertEqual(len(values), 12)
        self.assertEqual(values[0], {"month": "2024-01", "canonical_count": 1, "record_count": 3})
        self.assertEqual(values[-1], {"month": "2024-12", "canonical_count": 1, "record_count": 3})


if __name__ == "__main__":
    unittest.main()
