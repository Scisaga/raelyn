from __future__ import annotations

import sys
import unittest
from pathlib import Path


_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.services.embeddings import EmbeddingError
from raelyn.services.event_embedding_backfill import (
    _initial_result,
    validate_embedding_batch,
)


class EventEmbeddingBackfillTests(unittest.TestCase):
    def test_validate_embedding_batch_requires_exact_count(self) -> None:
        with self.assertRaisesRegex(EmbeddingError, "count"):
            validate_embedding_batch(
                [[1.0, 2.0]],
                expected_count=2,
                model="test",
                dim=2,
            )

    def test_validate_embedding_batch_checks_each_vector(self) -> None:
        with self.assertRaisesRegex(EmbeddingError, "all zero"):
            validate_embedding_batch(
                [[1.0, 2.0], [0.0, 0.0]],
                expected_count=2,
                model="test",
                dim=2,
            )

    def test_initial_result_counts_existing_target_vectors(self) -> None:
        result = _initial_result(100, 7)

        self.assertEqual(result["accepted_total"], 100)
        self.assertEqual(result["ready_before"], 7)
        self.assertEqual(result["remaining"], 93)
        self.assertEqual(result["converted"], 0)
        self.assertEqual(result["inserted"], 0)


if __name__ == "__main__":
    unittest.main()
