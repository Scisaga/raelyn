from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
import uuid
from unittest.mock import patch

import numpy as np
from sqlalchemy import event, func, select


_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.config import settings
from raelyn.api.playlists import get_playlist_event_map_manifest
from raelyn.db import engine, session_scope
from raelyn.models import EventMapSnapshot, EventMapState, EventMapTopic, Playlist
from raelyn.services.embeddings import embedding_spec
from raelyn.services.event_map_domain import (
    build_event_map_stories,
    build_event_map_topics,
    canonicalize_event_map_records,
)
from raelyn.services.event_map_projection import (
    compute_event_map_layout,
    compute_event_map_neighbors,
    compute_event_map_reduction,
)
from raelyn.services.event_map_snapshot import (
    _event_map_snapshot_reader,
    _stage_canonical_centroids,
    _stage_inputs,
)


@unittest.skipUnless(
    os.getenv("RAELYN_RUN_REAL_EVENT_MAP_TESTS") == "1",
    "设置 RAELYN_RUN_REAL_EVENT_MAP_TESTS=1 后使用真实上游事件与 embedding 运行",
)
class EventMapRealIntegrationTests(unittest.TestCase):
    def test_compact_manifest_uses_materialized_window_and_projected_topic_geometry(self) -> None:
        with session_scope() as session:
            candidate = session.execute(
                select(
                    EventMapState.playlist_id,
                    EventMapSnapshot.id,
                    EventMapSnapshot.monthly_distribution,
                )
                .join(EventMapSnapshot, EventMapSnapshot.id == EventMapState.current_snapshot_id)
                .where(
                    EventMapSnapshot.status == "ready",
                    EventMapSnapshot.layout_algorithm_version == "event_map_projection_v2",
                    EventMapSnapshot.projection_method == "incremental_pca50_umap3_cosine",
                )
                .order_by(EventMapSnapshot.finished_at.desc().nullslast(), EventMapSnapshot.id.desc())
                .limit(1)
            ).first()
            topic_count = (
                session.execute(
                    select(func.count(EventMapTopic.topic_id)).where(
                        EventMapTopic.snapshot_id == candidate[1]
                    )
                ).scalar_one()
                if candidate is not None
                else 0
            )
        if candidate is None:
            self.skipTest("没有可复用的 ready v2 事件星域快照；请先完成一次真实星域构建")

        playlist_id, snapshot_id, monthly_distribution = candidate
        statements: list[str] = []

        def capture_statement(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
            statements.append(str(statement))

        event.listen(engine, "before_cursor_execute", capture_statement)
        try:
            with patch(
                "raelyn.api.playlists._active_playlist_event_backfill_job",
                side_effect=AssertionError("compact manifest 不应扫描完整 backfill 任务树"),
            ):
                with patch(
                    "raelyn.api.playlists._playlist_event_backfill_job_out",
                    side_effect=AssertionError("compact manifest 不应汇总 backfill 子任务"),
                ):
                    with patch(
                        "raelyn.api.playlists._event_map_topics",
                        side_effect=AssertionError("compact manifest 不应读取完整 topic ORM 实体"),
                    ):
                        payload = get_playlist_event_map_manifest(
                            playlist_id,
                            compact=True,
                            snapshot_id=snapshot_id,
                        )
        finally:
            event.remove(engine, "before_cursor_execute", capture_statement)

        self.assertEqual(payload["snapshot_id"], str(snapshot_id))
        self.assertEqual(payload["monthly_distribution"], monthly_distribution or [])
        self.assertEqual(len(payload["topic_geometry"]), int(topic_count or 0))
        topic_statements = [
            statement.lower()
            for statement in statements
            if "from event_map_topic" in statement.lower()
        ]
        self.assertEqual(len(topic_statements), 1)
        topic_projection = topic_statements[0].split("from event_map_topic", 1)[0]
        self.assertNotIn("centroid_vector", topic_projection)
        self.assertNotIn("top_terms", topic_projection)
        self.assertNotIn("anchor_canonical_id", topic_projection)

    def test_small_real_playlist_builds_all_six_object_inputs_without_writes(self) -> None:
        with session_scope() as session, TemporaryDirectory(prefix="event-map-readonly-check-") as raw:
            root = Path(raw)
            spec = embedding_spec()
            staged = None
            playlist_ids = list(session.execute(select(Playlist.id).order_by(Playlist.id)).scalars())
            for playlist_id in playlist_ids:
                with _event_map_snapshot_reader(session) as reader:
                    candidate = _stage_inputs(
                        reader,
                        playlist_id=playlist_id,
                        model=spec.model,
                        dimension=spec.dim,
                        directory=root,
                        batch_size=200,
                        checkpoint=lambda *_args: None,
                    )
                if 3 <= len(candidate.records) <= 1000:
                    staged = candidate
                    break
                for path in root.iterdir():
                    path.unlink()
            if staged is None:
                self.skipTest("没有包含 3–1000 条 eligible 真实事件的播放列表")

            reduction = compute_event_map_reduction(
                staged.projection,
                batch_size=200,
                checkpoint=lambda _processed: None,
                cancel_check=lambda: None,
                max_rss_bytes=settings.analysis_max_rss_bytes,
                min_available_memory_bytes=1,
            )
            neighbors = compute_event_map_neighbors(
                reduction,
                directory=root,
                neighbor_count=32,
                cancel_check=lambda: None,
                max_rss_bytes=settings.analysis_max_rss_bytes,
                min_available_memory_bytes=1,
            )
            raw_vectors = np.memmap(
                staged.projection.vectors_path,
                dtype=np.float32,
                mode="r",
                shape=(len(staged.records), spec.dim),
            )
            indices = np.memmap(
                neighbors.indices_path,
                dtype=np.int32,
                mode="r",
                shape=(neighbors.count, neighbors.neighbor_count),
            )
            groups = canonicalize_event_map_records(staged.records, raw_vectors, indices)
            canonical_staging = _stage_canonical_centroids(
                root,
                groups,
                staged.records,
                raw_vectors,
                [uuid.UUID(int=index + 1) for index in range(len(groups))],
                200,
                lambda _processed: None,
            )
            canonical_reduction = compute_event_map_reduction(
                canonical_staging,
                batch_size=200,
                checkpoint=lambda _processed: None,
                cancel_check=lambda: None,
                max_rss_bytes=settings.analysis_max_rss_bytes,
                min_available_memory_bytes=1,
            )
            canonical_reduced = np.memmap(
                canonical_reduction.reduced_path,
                dtype=np.float32,
                mode="r",
                shape=(len(groups), canonical_reduction.dimension),
            )
            layout = compute_event_map_layout(
                reduced_path=canonical_reduction.reduced_path,
                count=len(groups),
                reduced_dim=canonical_reduction.dimension,
                coordinates_path=root / "canonical.float32",
                cancel_check=lambda: None,
                max_rss_bytes=settings.analysis_max_rss_bytes,
                min_available_memory_bytes=1,
            )
            coordinates = np.memmap(
                layout.coordinates_path,
                dtype=np.float32,
                mode="r",
                shape=(len(groups), 3),
            ).copy()
            topics, topic_by_group = build_event_map_topics(
                groups,
                staged.records,
                canonical_reduced,
            )
            stories = build_event_map_stories(groups, staged.records, raw_vectors)

            self.assertEqual(sum(len(group.member_indices) for group in groups), len(staged.records))
            self.assertTrue(topics)
            self.assertTrue(np.isfinite(coordinates).all())
            self.assertIsInstance(stories, list)
            self.assertLessEqual(
                max(
                    reduction.peak_rss_bytes,
                    neighbors.peak_rss_bytes,
                    canonical_reduction.peak_rss_bytes,
                    layout.peak_rss_bytes,
                ),
                settings.analysis_max_rss_bytes,
            )
            session.rollback()


if __name__ == "__main__":
    unittest.main()
