from __future__ import annotations

from pathlib import Path
import sys
import unittest
import uuid

import numpy as np


_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.services.event_map_domain import (
    EventMapCanonicalGroup,
    EventMapEntityRef,
    EventMapRecord,
    build_event_map_stories,
    build_event_map_topics,
    canonicalize_event_map_records,
    enrich_event_map_type_categories,
    event_map_canonical_centroid,
    event_map_semantic_family,
    normalize_event_map_entity,
)


def _record(
    index: int,
    *,
    title: str,
    start_day: int = 10,
    end_day: int = 10,
    precision: str = "day",
    event_type: str = "policy",
    direction: str = "neutral",
    entity: str = "美国",
) -> EventMapRecord:
    return EventMapRecord(
        event_id=uuid.UUID(int=index + 1),
        revision_id=uuid.UUID(int=1000 + index),
        vector_index=index,
        title=title,
        summary=title,
        event_type=event_type,
        direction=direction,
        start_day=start_day,
        end_day=end_day,
        time_precision=precision,
        entities=(EventMapEntityRef("country", entity, entity, "actor", 1.0),),
    )


class EventMapDomainTests(unittest.TestCase):
    def test_event_types_are_grouped_into_stable_semantic_families(self) -> None:
        self.assertEqual(event_map_semantic_family("monetary_policy")["code"], "macro_policy")
        self.assertEqual(event_map_semantic_family("mergers_acquisitions")["code"], "corporate")
        self.assertEqual(event_map_semantic_family("cybersecurity")["code"], "technology")
        self.assertEqual(event_map_semantic_family("unknown_new_type")["code"], "other")

        categories = enrich_event_map_type_categories(
            [{"code": 7, "value": "geopolitical", "label": "geopolitical"}]
        )
        self.assertEqual(categories[0]["semantic_family"], "geopolitical")
        self.assertEqual(categories[0]["semantic_family_label"], "地缘、法律与监管")
        self.assertEqual(categories[0]["semantic_color"], "#fb7185")

    def test_canonical_centroid_is_independent_of_representative(self) -> None:
        records = [
            _record(0, title="同一事件"),
            _record(1, title="同一事件"),
        ]
        vectors = np.asarray([[2.0, 0.0], [0.0, 4.0]], dtype=np.float32)
        decisions = {}
        first = event_map_canonical_centroid(
            EventMapCanonicalGroup([0, 1], 0, decisions),
            records,
            vectors,
        )
        second = event_map_canonical_centroid(
            EventMapCanonicalGroup([0, 1], 1, decisions),
            records,
            vectors,
        )

        expected = np.asarray([1.0, 1.0], dtype=np.float32)
        expected /= np.linalg.norm(expected)
        self.assertEqual(first.tobytes(), second.tobytes())
        np.testing.assert_array_equal(first, expected)

    def test_cross_language_entity_aliases_share_a_canonical_key(self) -> None:
        self.assertEqual(
            normalize_event_map_entity("country", "美国"),
            normalize_event_map_entity("country", "United States"),
        )
        self.assertEqual(
            normalize_event_map_entity("institution", "美联储"),
            normalize_event_map_entity("institution", "Federal Reserve"),
        )

    def test_exact_duplicate_is_merged_but_same_topic_different_day_is_not(self) -> None:
        records = [
            _record(0, title="美国宣布新的出口管制"),
            _record(1, title="美国宣布新的出口管制"),
            _record(2, title="美国扩大出口管制", start_day=11, end_day=11),
        ]
        vectors = np.asarray([[1.0, 0.0], [1.0, 0.0], [0.999, 0.001]], dtype=np.float32)
        neighbors = np.asarray([[0, 1, 2], [1, 0, 2], [2, 1, 0]], dtype=np.int32)

        groups = canonicalize_event_map_records(records, vectors, neighbors)

        self.assertEqual(sorted(len(group.member_indices) for group in groups), [1, 2])
        merged = next(group for group in groups if len(group.member_indices) == 2)
        self.assertEqual({decision.rule for decision in merged.decisions.values()}, {"exact"})

    def test_fuzzy_requires_mutual_unambiguous_match(self) -> None:
        records = [
            _record(0, title="美国宣布出口限制"),
            _record(1, title="US announced export restrictions", entity="United States"),
            _record(2, title="美国宣布另一项出口限制"),
        ]
        vectors = np.asarray(
            [
                [1.0, 0.0, 0.0],
                [0.995, 0.10, 0.0],
                [0.995, -0.10, 0.0],
            ],
            dtype=np.float32,
        )
        neighbors = np.asarray([[0, 1, 2], [1, 0, 2], [2, 1, 0]], dtype=np.int32)

        groups = canonicalize_event_map_records(records, vectors, neighbors)

        self.assertEqual(len(groups), 3)
        self.assertTrue(any("ambiguous_fuzzy_candidates" in group.uncertainty_flags for group in groups))

    def test_compact_fuzzy_candidates_preserve_duplicate_runner_up_ambiguity(self) -> None:
        records = [
            _record(0, title="美国宣布出口限制"),
            _record(1, title="US announced export restrictions", entity="United States"),
        ]
        vectors = np.asarray([[1.0, 0.0], [0.999, 0.001]], dtype=np.float32)
        # 原候选列表会把重复邻居视为同分 runner-up；紧凑 top-2 必须保持该语义。
        neighbors = np.asarray([[1, 1], [0, 0]], dtype=np.int32)

        groups = canonicalize_event_map_records(records, vectors, neighbors)

        self.assertEqual(len(groups), 2)
        self.assertTrue(all("ambiguous_fuzzy_candidates" in group.uncertainty_flags for group in groups))

    def test_canonical_checkpoint_is_batched_monotonic_and_does_not_change_output(self) -> None:
        records = [
            _record(0, title="美国宣布出口限制"),
            _record(1, title="US announced export restrictions", entity="United States"),
            _record(2, title="中国公布通胀", entity="中国", event_type="macro"),
        ]
        vectors = np.asarray(
            [[1.0, 0.0], [0.999, 0.001], [0.0, 1.0]],
            dtype=np.float32,
        )
        neighbors = np.asarray([[0, 1, 2], [1, 0, 2], [2, 0, 1]], dtype=np.int32)
        baseline = canonicalize_event_map_records(records, vectors, neighbors)
        checkpoints: list[tuple[int, int]] = []

        observed = canonicalize_event_map_records(
            records,
            vectors,
            neighbors,
            checkpoint=lambda processed, total: checkpoints.append((processed, total)),
            checkpoint_interval=2,
        )

        def signature(groups: list[EventMapCanonicalGroup]) -> list[tuple]:
            return [
                (
                    tuple(group.member_indices),
                    group.representative_index,
                    tuple(
                        (index, decision.rule, decision.score, decision.runner_up_score, decision.reasons)
                        for index, decision in sorted(group.decisions.items())
                    ),
                    tuple(sorted(group.uncertainty_flags)),
                )
                for group in groups
            ]

        self.assertEqual(signature(observed), signature(baseline))
        self.assertTrue(checkpoints)
        self.assertEqual(checkpoints[-1][0], checkpoints[-1][1])
        self.assertTrue(all(total == checkpoints[-1][1] for _, total in checkpoints))
        self.assertEqual([processed for processed, _ in checkpoints], sorted(processed for processed, _ in checkpoints))

    def test_year_precision_never_fuzzy_merges(self) -> None:
        records = [
            _record(0, title="年度政策调整甲", precision="year", start_day=0, end_day=365),
            _record(1, title="年度政策调整乙", precision="year", start_day=0, end_day=365),
        ]
        vectors = np.asarray([[1.0, 0.0], [0.999, 0.001]], dtype=np.float32)
        neighbors = np.asarray([[0, 1], [1, 0]], dtype=np.int32)
        self.assertEqual(len(canonicalize_event_map_records(records, vectors, neighbors)), 2)

    def test_chinese_adjacent_numeric_claims_block_conflicting_merges(self) -> None:
        records = [
            _record(0, title="美联储加息25bp，利率由5%降至4%", entity="美联储"),
            _record(1, title="美联储加息50bp，利率由5%降至3%", entity="Federal Reserve"),
        ]
        self.assertIn("25bp", records[0].numeric_signature)
        self.assertIn("50bp", records[1].numeric_signature)
        vectors = np.asarray([[1.0, 0.0], [0.9999, 0.0001]], dtype=np.float32)
        neighbors = np.asarray([[0, 1], [1, 0]], dtype=np.int32)

        groups = canonicalize_event_map_records(records, vectors, neighbors)

        self.assertEqual(len(groups), 2)

    def test_topics_and_story_are_deterministic(self) -> None:
        records = [
            _record(0, title="美国宣布并购", event_type="company_action"),
            _record(1, title="美国批准并购", start_day=20, event_type="company_action"),
            _record(2, title="中国公布通胀", start_day=30, event_type="macro", entity="中国"),
        ]
        vectors = np.asarray([[1.0, 0.0], [0.99, 0.01], [0.0, 1.0]], dtype=np.float32)
        groups = canonicalize_event_map_records(records, vectors)
        topics, topic_by_group = build_event_map_topics(groups, records, vectors)
        stories = build_event_map_stories(groups, records, vectors)

        self.assertEqual(len(topics), 6)
        self.assertEqual(len(topic_by_group), 3)
        self.assertTrue(all(topic.parent_topic_index is None for topic in topics[:3]))
        self.assertTrue(all(topic.parent_topic_index is not None for topic in topics[3:]))
        self.assertTrue(all(index >= 3 for index in topic_by_group))
        self.assertEqual(len(stories), 1)
        self.assertEqual(stories[0].edges[0].relation_type, "continuation")


if __name__ == "__main__":
    unittest.main()
