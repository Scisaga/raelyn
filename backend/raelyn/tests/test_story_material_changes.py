from __future__ import annotations

from copy import deepcopy
import unittest

from raelyn.services.event_map_snapshot import _story_has_material_update


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

    def test_first_formation_updates_unread_time(self):
        self.assertTrue(_story_has_material_update(None, self._story_payload()))

    def test_rewording_and_scores_do_not_create_unread_updates(self):
        before = self._story_payload()
        after = deepcopy(before)
        after.update(title="另一种标题", summary="另一种摘要", quality_score=0.97)
        after["edges"][0]["score"] = 0.96
        after["edges"][0]["evidence"].update(cosine=0.99, claim_overlap=0.35)
        self.assertFalse(_story_has_material_update(before, after))

    def test_event_order_relation_basis_evidence_and_maturity_update_unread_time(self):
        before = self._story_payload()
        variants = [deepcopy(before) for _ in range(4)]
        variants[0]["member_ids"].reverse()
        variants[1]["edges"][0]["evidence"]["claim_bridge"]["target_claim"] = "供应已经中断"
        variants[2]["edges"][0]["evidence_revision_ids"].append("record-c")
        variants[3]["maturity"] = "established"
        for after in variants:
            with self.subTest(after=after):
                self.assertTrue(_story_has_material_update(before, after))


if __name__ == "__main__":
    unittest.main()
