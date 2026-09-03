from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Callable
import unittest
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from raelyn.models import (
    Base,
    EventMapChange,
    EventMapSnapshot,
    EventMapStory,
    EventMapStoryIdentity,
    Playlist,
)
from raelyn.services.event_map_snapshot import (
    STORY_MATERIAL_CHANGE_TYPES,
    _persist_v2_history_and_changes,
    _story_change_types,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs) -> str:
    return "JSON"


@compiles(ARRAY, "sqlite")
def _compile_array_for_sqlite(_type, _compiler, **_kwargs) -> str:
    return "JSON"


class StoryMaterialChangeTests(unittest.TestCase):
    @staticmethod
    def _story_payload() -> dict:
        return {
            "title": "稳定故事的当前进展",
            "summary": "当前进展摘要",
            "maturity": "emerging",
            "quality_score": 0.82,
            "member_ids": ["event-a", "event-b"],
            "edges": [
                {
                    "source_canonical_id": "event-a",
                    "target_canonical_id": "event-b",
                    "relation_type": "causes",
                    "direction": "forward",
                    "status": "automatic",
                    "score": 0.84,
                    "evidence_revision_ids": ["record-a", "record-b"],
                    "evidence": {
                        "gap_days": 2,
                        "claim_bridge": {
                            "source_claim": "政策开始收紧",
                            "target_claim": "供应受到影响",
                        },
                        "source_excerpts": ["政策开始收紧"],
                        "target_excerpts": ["供应受到影响"],
                        "cosine": 0.91,
                        "claim_overlap": 0.27,
                        "supporting_revision_ids": ["record-a", "record-b"],
                    },
                }
            ],
        }

    def test_first_formation_is_material(self) -> None:
        self.assertEqual(_story_change_types(None, self._story_payload()), ["story_added"])

    def test_copy_and_score_only_changes_do_not_become_material(self) -> None:
        before = self._story_payload()
        after = deepcopy(before)
        after["title"] = "同一进展的另一种标题"
        after["summary"] = "同一事实的另一种摘要"
        after["quality_score"] = 0.97
        after["edges"][0]["score"] = 0.93
        after["edges"][0]["evidence"]["cosine"] = 0.96
        after["edges"][0]["evidence"]["claim_overlap"] = 0.34

        changes = _story_change_types(before, after)

        self.assertEqual(changes, ["story_summary_changed"])
        self.assertTrue(STORY_MATERIAL_CHANGE_TYPES.isdisjoint(changes))

    def test_member_order_relation_basis_support_and_maturity_are_material(self) -> None:
        cases: tuple[tuple[str, Callable[[dict], None], str], ...] = (
            (
                "member_order",
                lambda payload: payload.update(member_ids=["event-b", "event-a"]),
                "story_members_changed",
            ),
            (
                "relation_basis",
                lambda payload: payload["edges"][0]["evidence"]["claim_bridge"].update(
                    target_claim="供应已经中断"
                ),
                "story_relations_changed",
            ),
            (
                "support_record",
                lambda payload: payload["edges"][0]["evidence_revision_ids"].append(
                    "record-c"
                ),
                "story_evidence_changed",
            ),
            (
                "maturity",
                lambda payload: payload.update(maturity="established"),
                "story_maturity_changed",
            ),
        )
        for name, mutate, expected in cases:
            with self.subTest(name=name):
                before = self._story_payload()
                after = deepcopy(before)
                mutate(after)
                changes = _story_change_types(before, after)
                self.assertIn(expected, changes)
                self.assertIn(expected, STORY_MATERIAL_CHANGE_TYPES)

    def test_new_supported_correction_is_named_separately(self) -> None:
        before = self._story_payload()
        after = deepcopy(before)
        after["edges"].append(
            {
                "source_canonical_id": "event-b",
                "target_canonical_id": "event-a",
                "relation_type": "corrects",
                "direction": "forward",
                "status": "automatic",
                "score": 0.9,
                "evidence_revision_ids": ["record-c"],
                "evidence": {"source_excerpts": ["此前数据需要更正"]},
            }
        )

        changes = _story_change_types(before, after)

        self.assertIn("story_relations_changed", changes)
        self.assertIn("story_correction_added", changes)
        self.assertIn("story_evidence_changed", changes)

    def test_identity_cursor_moves_only_for_material_changes(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        playlist_id = uuid.uuid4()
        identity_id = uuid.uuid4()
        snapshot_ids = [uuid.uuid4() for _ in range(3)]
        observed = [
            datetime(2026, 8, day, 12, 0, tzinfo=timezone.utc)
            for day in (1, 2, 3)
        ]
        try:
            with Session(engine) as session:
                session.add(Playlist(id=playlist_id, name="故事测试"))
                for index, snapshot_id in enumerate(snapshot_ids):
                    session.add(
                        EventMapSnapshot(
                            id=snapshot_id,
                            playlist_id=playlist_id,
                            parent_snapshot_id=snapshot_ids[index - 1] if index else None,
                            status="ready",
                            embedding_model="test",
                            embedding_dim=3,
                        )
                    )
                session.add(
                    EventMapStoryIdentity(
                        id=identity_id,
                        playlist_id=playlist_id,
                        stable_title="冻结标题",
                        created_snapshot_id=snapshot_ids[0],
                        last_material_snapshot_id=snapshot_ids[0],
                        last_material_changed_at=observed[0],
                    )
                )
                session.add(
                    EventMapStory(
                        snapshot_id=snapshot_ids[0],
                        story_id=uuid.uuid4(),
                        story_identity_id=identity_id,
                        title="首次进展",
                        summary="首次形成",
                        maturity="emerging",
                    )
                )
                session.flush()
                _persist_v2_history_and_changes(
                    session,
                    playlist_id=playlist_id,
                    snapshot_id=snapshot_ids[0],
                    parent_snapshot_id=None,
                    observed_at=observed[0],
                    layout_continuity="rebased",
                )

                session.add(
                    EventMapStory(
                        snapshot_id=snapshot_ids[1],
                        story_id=uuid.uuid4(),
                        story_identity_id=identity_id,
                        title="只是换一种标题",
                        summary="只是换一种摘要",
                        maturity="emerging",
                        quality_score=0.99,
                    )
                )
                session.flush()
                _persist_v2_history_and_changes(
                    session,
                    playlist_id=playlist_id,
                    snapshot_id=snapshot_ids[1],
                    parent_snapshot_id=snapshot_ids[0],
                    observed_at=observed[1],
                    layout_continuity="anchored",
                )
                session.expire_all()
                identity = session.get(EventMapStoryIdentity, identity_id)
                self.assertEqual(identity.last_material_snapshot_id, snapshot_ids[0])
                self.assertEqual(
                    identity.last_material_changed_at,
                    observed[0].replace(tzinfo=None),
                )

                session.add(
                    EventMapStory(
                        snapshot_id=snapshot_ids[2],
                        story_id=uuid.uuid4(),
                        story_identity_id=identity_id,
                        title="成熟故事进展",
                        summary="事实结构不变但成熟度已确认",
                        maturity="established",
                    )
                )
                session.flush()
                _persist_v2_history_and_changes(
                    session,
                    playlist_id=playlist_id,
                    snapshot_id=snapshot_ids[2],
                    parent_snapshot_id=snapshot_ids[1],
                    observed_at=observed[2],
                    layout_continuity="anchored",
                )
                session.expire_all()
                identity = session.get(EventMapStoryIdentity, identity_id)
                self.assertEqual(identity.stable_title, "冻结标题")
                self.assertEqual(identity.last_material_snapshot_id, snapshot_ids[2])
                self.assertEqual(
                    identity.last_material_changed_at,
                    observed[2].replace(tzinfo=None),
                )
                change_types = set(
                    session.execute(
                        select(EventMapChange.change_type).where(
                            EventMapChange.object_id == identity_id
                        )
                    ).scalars()
                )
                self.assertIn("story_added", change_types)
                self.assertIn("story_summary_changed", change_types)
                self.assertIn("story_maturity_changed", change_types)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
