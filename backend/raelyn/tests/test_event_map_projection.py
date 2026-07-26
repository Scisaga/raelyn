from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sys
import unittest
import uuid

import numpy as np

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.services.event_map_projection import (
    EVENT_MAP_PRECISION_CODES,
    _event_time_days,
    compute_event_map_neighbors,
    compute_event_map_projection,
    compute_event_map_reduction,
    stage_event_map_projection,
)


def _rows(count: int, dimension: int):
    event_types = ("policy", "company_action", "macro")
    for index in range(count):
        yield SimpleNamespace(
            event_id=uuid.UUID(int=index + 1),
            vector=[float(((index + 3) * (column + 5)) % 17) / 17.0 for column in range(dimension)],
            event_time_start=datetime(2026, (index % 12) + 1, 1, tzinfo=timezone.utc),
            event_time_end=None,
            time_precision="month" if index % 2 else "day",
            event_type=event_types[index % len(event_types)],
        )


class EventMapProjectionTests(unittest.TestCase):
    def test_event_time_precision_expands_month_and_year_without_using_available_at(self) -> None:
        month_start, month_end = _event_time_days(
            datetime(2024, 2, 29, 18, tzinfo=timezone.utc),
            None,
            "month",
        )
        year_start, year_end = _event_time_days(
            datetime(2024, 11, 30, 18, tzinfo=timezone.utc),
            None,
            "year",
        )
        day_start, day_end = _event_time_days(
            datetime(2024, 2, 29, 18, tzinfo=timezone.utc),
            None,
            "second",
        )

        self.assertEqual(month_end - month_start, 28)
        self.assertEqual(year_end - year_start, 365)
        self.assertEqual(day_start, day_end)
        self.assertEqual(EVENT_MAP_PRECISION_CODES["month"], 2)

    def test_staging_streams_vectors_to_float32_memmap_and_keeps_stable_point_order(self) -> None:
        checkpoints: list[int] = []
        with TemporaryDirectory() as directory:
            staging = stage_event_map_projection(
                _rows(7, 4),
                directory=Path(directory),
                expected_count=7,
                embedding_dim=4,
                batch_size=3,
                checkpoint=checkpoints.append,
            )
            vectors = np.memmap(staging.vectors_path, dtype=np.float32, mode="r", shape=(7, 4))

            self.assertEqual(staging.count, 7)
            self.assertEqual(staging.event_ids, [uuid.UUID(int=index + 1) for index in range(7)])
            self.assertEqual(vectors.dtype, np.float32)
            self.assertEqual(checkpoints, [3, 6, 7])
            self.assertEqual([item["value"] for item in staging.categories], ["company_action", "macro", "policy"])

    def test_incremental_pca_and_seeded_umap_are_deterministic(self) -> None:
        coordinates: list[np.ndarray] = []
        for _ in range(2):
            with TemporaryDirectory() as directory:
                staging = stage_event_map_projection(
                    _rows(24, 6),
                    directory=Path(directory),
                    expected_count=24,
                    embedding_dim=6,
                    batch_size=8,
                    checkpoint=lambda _processed: None,
                )
                result = compute_event_map_projection(
                    staging,
                    batch_size=8,
                    checkpoint=lambda _processed: None,
                    cancel_check=lambda: None,
                    max_rss_bytes=12 * 1024 * 1024 * 1024,
                    min_available_memory_bytes=1,
                )
                current = np.memmap(result.coordinates_path, dtype=np.float32, mode="r", shape=(24, 3)).copy()
                reduced = np.memmap(staging.reduced_path, dtype=np.float32, mode="r", shape=(24, 6))
                np.testing.assert_allclose(np.linalg.norm(reduced, axis=1), np.ones(24), atol=1e-5)
                self.assertEqual(result.count, 24)
                self.assertLess(result.bounds["min_x"], result.bounds["max_x"])
                self.assertLess(result.bounds["min_z"], result.bounds["max_z"])
                self.assertGreater(result.peak_rss_bytes, 0)
                coordinates.append(current)

        np.testing.assert_allclose(coordinates[0], coordinates[1], rtol=0, atol=1e-6)

    def test_cancel_during_umap_terminates_child_and_cleans_up(self) -> None:
        class Cancelled(Exception):
            pass

        calls = 0

        def cancel_check() -> None:
            nonlocal calls
            calls += 1
            if calls >= 6:
                raise Cancelled("cancel requested")

        with TemporaryDirectory() as directory:
            staging = stage_event_map_projection(
                _rows(24, 6),
                directory=Path(directory),
                expected_count=24,
                embedding_dim=6,
                batch_size=8,
                checkpoint=lambda _processed: None,
            )
            with self.assertRaisesRegex(Cancelled, "cancel requested"):
                compute_event_map_projection(
                    staging,
                    batch_size=8,
                    checkpoint=lambda _processed: None,
                    cancel_check=cancel_check,
                    max_rss_bytes=12 * 1024 * 1024 * 1024,
                    min_available_memory_bytes=1,
                )
            self.assertGreaterEqual(calls, 6)

    def test_umap_child_is_terminated_when_combined_rss_exceeds_limit(self) -> None:
        with TemporaryDirectory() as directory:
            staging = stage_event_map_projection(
                _rows(24, 6),
                directory=Path(directory),
                expected_count=24,
                embedding_dim=6,
                batch_size=8,
                checkpoint=lambda _processed: None,
            )
            with self.assertRaisesRegex(RuntimeError, "RSS .* exceeds configured maximum"):
                compute_event_map_projection(
                    staging,
                    batch_size=8,
                    checkpoint=lambda _processed: None,
                    cancel_check=lambda: None,
                    max_rss_bytes=1,
                    min_available_memory_bytes=1,
                )

    def test_neighbor_graph_keeps_self_and_nearest_row_in_stable_order(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory)
            staging = stage_event_map_projection(
                _rows(2, 4),
                directory=path,
                expected_count=2,
                embedding_dim=4,
                batch_size=2,
                checkpoint=lambda _processed: None,
            )
            reduction = compute_event_map_reduction(
                staging,
                batch_size=2,
                checkpoint=lambda _processed: None,
                cancel_check=lambda: None,
                max_rss_bytes=12 * 1024 * 1024 * 1024,
                min_available_memory_bytes=1,
            )
            result = compute_event_map_neighbors(
                reduction,
                directory=path,
                neighbor_count=2,
                cancel_check=lambda: None,
                max_rss_bytes=12 * 1024 * 1024 * 1024,
                min_available_memory_bytes=1,
            )
            indices = np.memmap(result.indices_path, dtype=np.int32, mode="r", shape=(2, 2))

            self.assertEqual(result.neighbor_count, 2)
            self.assertEqual(indices[:, 0].tolist(), [0, 1])


if __name__ == "__main__":
    unittest.main()
