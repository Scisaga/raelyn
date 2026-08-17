from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import uuid

from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from raelyn.models import (
    Base,
    Brief,
    BriefReference,
    DomainObservationCursor,
    EventMapCanonical,
    EventMapCanonicalMember,
    EventMapCanonicalHistoryMember,
    EventMapCanonicalHistoryRevision,
    EventMapCanonicalIdentity,
    EventMapChange,
    EventMapEntityIndex,
    EventMapRecordRevision,
    EventMapSnapshot,
    EventMapState,
    EventMapStory,
    EventMapStoryHistoryEvidence,
    EventMapStoryHistoryRevision,
    EventMapStoryIdentity,
    EventMapStoryMember,
    EventMapTopic,
    EventMapTopicMember,
    Media,
    Playlist,
    StoryReadState,
    Video,
)
from raelyn.services.brief_references import attach_structured_brief_references
from raelyn.services.event_map_snapshot import (
    _assign_story_identities,
    _canonical_history_member_rows,
    _relink_legacy_story_identities_for_bootstrap,
    _story_history_evidence_rows,
    _story_has_new_correction,
)
from raelyn.services.v2_observation import (
    canonical_directory,
    canonical_history,
    change_payload,
    domain_directory,
    list_changes,
    object_brief_references,
    story_history,
    topic_detail,
    update_observation_cursor,
    update_story_read_state,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs) -> str:
    """让隔离的 SQLite 集成测试按 JSON 列创建 PostgreSQL JSONB 字段。"""

    return "JSON"


@compiles(ARRAY, "sqlite")
def _compile_array_for_sqlite(_type, _compiler, **_kwargs) -> str:
    """这些测试不写入 ARRAY 字段，只需让完整元数据可以在 SQLite 建表。"""

    return "JSON"


class V2ObservationIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.tempdir.name) / 'v2.sqlite'}")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.now = datetime(2026, 8, 16, 1, 0, tzinfo=timezone.utc)
        self.playlist = Playlist(id=uuid.uuid4(), name="宏观观测域")
        self.parent = self._snapshot(parent_id=None, finished_at=self.now)
        self.current = self._snapshot(parent_id=self.parent.id, finished_at=self.now + timedelta(hours=1))
        self.session.add_all([self.playlist, self.parent, self.current])
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()
        self.tempdir.cleanup()

    def _snapshot(self, *, parent_id: uuid.UUID | None, finished_at: datetime) -> EventMapSnapshot:
        return EventMapSnapshot(
            id=uuid.uuid4(),
            playlist_id=self.playlist.id,
            status="ready",
            parent_snapshot_id=parent_id,
            embedding_model="test",
            embedding_dim=3,
            input_fingerprint=uuid.uuid4().hex,
            build_key=uuid.uuid4().hex,
            finished_at=finished_at,
        )

    def test_observation_cursor_repeat_submit_is_idempotent(self) -> None:
        values = {
            "snapshot_id": self.current.id,
            "observed_at": self.current.finished_at,
            "event_time_start": self.now - timedelta(days=30),
            "event_time_end": self.now,
            "view_mode": "replay",
            "filter_state": {"event_type": "policy"},
        }

        first = update_observation_cursor(
            self.session,
            playlist_id=self.playlist.id,
            values=values,
        )
        self.session.commit()
        second = update_observation_cursor(
            self.session,
            playlist_id=self.playlist.id,
            values=values,
        )
        self.session.commit()

        count = self.session.execute(select(func.count()).select_from(DomainObservationCursor)).scalar_one()
        self.assertEqual(count, 1)
        self.assertEqual(first.playlist_id, second.playlist_id)
        self.assertEqual(second.snapshot_id, self.current.id)
        self.assertEqual(second.view_mode, "replay")
        self.assertEqual(second.filter_state, {"event_type": "policy"})

    def test_canonical_directory_reuses_typed_entity_filter(self) -> None:
        self.session.add(EventMapState(playlist_id=self.playlist.id, current_snapshot_id=self.current.id))
        canonicals = []
        entities = []
        for point_index, (title, entity_key) in enumerate((("苹果发布产品", "apple"), ("谷歌发布模型", "google"))):
            canonical_id = uuid.uuid4()
            canonicals.append(
                EventMapCanonical(
                    snapshot_id=self.current.id,
                    canonical_id=canonical_id,
                    representative_revision_id=uuid.uuid4(),
                    title=title,
                    summary=f"{title}摘要",
                    event_type="equity",
                    event_time_start=self.now,
                    event_time_end=self.now,
                    time_precision="day",
                    event_start_day=1,
                    event_end_day=1,
                    event_type_code=1,
                    time_precision_code=1,
                    centroid_checksum=f"centroid-{point_index}",
                    point_index=point_index,
                    x=float(point_index),
                    y=0,
                    z=0,
                )
            )
            entities.append(
                EventMapEntityIndex(
                    snapshot_id=self.current.id,
                    canonical_id=canonical_id,
                    entity_type="company",
                    normalized_key=entity_key,
                    name=entity_key.title(),
                    point_index=point_index,
                    record_count=1,
                )
            )
        self.session.add_all([*canonicals, *entities])
        self.session.commit()

        payload = canonical_directory(
            self.session,
            self.playlist.id,
            normalized_key="apple",
            entity_type="company",
        )

        self.assertEqual([item["title"] for item in payload["items"]], ["苹果发布产品"])

    def test_change_cursor_has_stable_order_and_time_basis(self) -> None:
        object_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        changes = []
        for index, object_id in enumerate(object_ids):
            changes.append(
                EventMapChange(
                    id=uuid.uuid5(uuid.NAMESPACE_URL, f"change-{index}"),
                    playlist_id=self.playlist.id,
                    from_snapshot_id=self.parent.id,
                    to_snapshot_id=self.current.id,
                    object_type="canonical",
                    object_id=object_id,
                    change_type="canonical_added",
                    occurred_at=self.now - timedelta(days=index),
                    observed_at=self.now + timedelta(minutes=index),
                    after_revision={"title": f"事件 {index}"},
                )
            )
        self.session.add_all(changes)
        self.session.commit()

        first_page = list_changes(self.session, playlist_id=self.playlist.id, limit=2)
        second_page = list_changes(
            self.session,
            playlist_id=self.playlist.id,
            cursor_id=uuid.UUID(first_page["next_cursor"]),
            limit=2,
        )
        repeated = list_changes(self.session, playlist_id=self.playlist.id, limit=2)

        self.assertEqual(first_page, repeated)
        self.assertEqual(len(first_page["items"]), 2)
        self.assertEqual(len(second_page["items"]), 1)
        self.assertEqual(first_page["items"][0]["time_basis"]["occurred_at"], "event_occurrence")
        self.assertEqual(first_page["items"][0]["time_basis"]["observed_at"], "system_cognition")

    def test_retired_change_deep_link_uses_the_last_snapshot_containing_the_object(self) -> None:
        canonical_id = uuid.uuid4()
        change = EventMapChange(
            id=uuid.uuid4(),
            playlist_id=self.playlist.id,
            from_snapshot_id=self.parent.id,
            to_snapshot_id=self.current.id,
            object_type="canonical",
            object_id=canonical_id,
            change_type="canonical_retired",
            observed_at=self.now,
            before_revision={"title": "已退休事件"},
            after_revision=None,
        )

        payload = change_payload(change)

        self.assertIn(f"snapshot_id={self.parent.id}", payload["web_url"])

    def test_story_identity_is_conservatively_reused_and_read_state_persists(self) -> None:
        stable_story_id = uuid.uuid4()
        parent_story_id = uuid.uuid4()
        member_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        self.session.add(
            EventMapStoryIdentity(
                id=stable_story_id,
                playlist_id=self.playlist.id,
                status="active",
                created_snapshot_id=self.parent.id,
            )
        )
        self.session.add(
            EventMapStory(
                snapshot_id=self.parent.id,
                story_id=parent_story_id,
                story_identity_id=stable_story_id,
                title="利率路径",
            )
        )
        for position, canonical_id in enumerate(member_ids):
            self.session.add(
                EventMapStoryMember(
                    snapshot_id=self.parent.id,
                    story_id=parent_story_id,
                    position=position,
                    canonical_id=canonical_id,
                )
            )
        self.session.commit()

        stories = [SimpleNamespace(story_index=0, member_group_indices=[0, 1, 2, 3])]
        identities, states = _assign_story_identities(
            self.session,
            playlist_id=self.playlist.id,
            snapshot_id=self.current.id,
            parent_snapshot_id=self.parent.id,
            stories=stories,
            canonical_ids=[*member_ids, uuid.uuid4()],
        )
        self.assertEqual(identities, [stable_story_id])
        self.assertEqual(states, ["retained"])

        retained_evidence_id = uuid.uuid4()
        new_evidence_id = uuid.uuid4()
        self.session.add(
            EventMapStoryHistoryRevision(
                id=uuid.uuid4(),
                playlist_id=self.playlist.id,
                story_identity_id=stable_story_id,
                snapshot_id=self.parent.id,
                story_id=parent_story_id,
                title="利率路径",
                member_ids=[str(value) for value in member_ids[:2]],
                edges=[],
                evidence_revision_ids=[str(retained_evidence_id)],
                method_version="story-v1",
                observed_at=self.parent.finished_at,
            )
        )
        revision = EventMapStoryHistoryRevision(
            id=uuid.uuid4(),
            playlist_id=self.playlist.id,
            story_identity_id=stable_story_id,
            snapshot_id=self.current.id,
            story_id=uuid.uuid4(),
            title="利率路径",
            summary="冻结成员形成的有证据进展。",
            event_time_start=self.now - timedelta(days=10),
            event_time_end=self.now,
            member_ids=[str(value) for value in member_ids],
            edges=[],
            evidence_revision_ids=[str(retained_evidence_id), str(new_evidence_id)],
            method_version="story-v1",
            observed_at=self.current.finished_at,
        )
        self.session.add(revision)
        self.session.add(EventMapState(playlist_id=self.playlist.id, current_snapshot_id=self.current.id))
        self.session.commit()
        state = update_story_read_state(
            self.session,
            playlist_id=self.playlist.id,
            identity_id=stable_story_id,
            followed=True,
            snapshot_id=self.current.id,
            position=2,
            mark_read=True,
        )
        self.session.commit()
        payload = story_history(self.session, self.playlist.id, stable_story_id)

        self.assertTrue(state.followed)
        self.assertEqual(state.last_position, 2)
        self.assertTrue(payload["followed"])
        self.assertEqual(payload["last_read_snapshot_id"], str(self.current.id))
        self.assertTrue(payload["revisions"][0]["delta"]["baseline"])
        self.assertEqual(payload["revisions"][1]["member_ids"], [str(value) for value in member_ids])
        self.assertEqual(payload["revisions"][1]["delta"]["members_added"], [str(member_ids[2])])
        self.assertEqual(payload["revisions"][1]["delta"]["evidence_added"], [str(new_evidence_id)])
        self.assertEqual(payload["current_trajectory"]["snapshot_id"], str(self.current.id))
        historical = story_history(
            self.session,
            self.playlist.id,
            stable_story_id,
            snapshot_id=self.parent.id,
        )
        self.assertEqual(historical["current_trajectory"]["snapshot_id"], str(self.parent.id))
        with self.assertRaisesRegex(LookupError, "domain snapshot not found"):
            story_history(
                self.session,
                self.playlist.id,
                stable_story_id,
                snapshot_id=uuid.uuid4(),
            )
        self.assertEqual(self.session.execute(select(func.count()).select_from(StoryReadState)).scalar_one(), 1)

    def test_canonical_history_survives_without_snapshot_foreign_key(self) -> None:
        canonical_id = uuid.uuid4()
        self.session.add(
            EventMapCanonicalIdentity(
                id=canonical_id,
                playlist_id=self.playlist.id,
                status="active",
                created_snapshot_id=self.parent.id,
            )
        )
        self.session.add(
            EventMapCanonicalHistoryRevision(
                id=uuid.uuid4(),
                playlist_id=self.playlist.id,
                canonical_id=canonical_id,
                snapshot_id=self.parent.id,
                revision={"title": "政策会议", "member_revision_ids": [], "evidence_revision_ids": []},
                occurred_at=self.now - timedelta(days=2),
                observed_at=self.parent.finished_at,
            )
        )
        self.session.commit()

        payload = canonical_history(self.session, self.playlist.id, canonical_id)

        self.assertEqual(payload["canonical_id"], str(canonical_id))
        self.assertEqual(payload["revisions"][0]["revision"]["title"], "政策会议")

    def test_legacy_story_ids_are_conservatively_relinked_before_history_backfill(self) -> None:
        stable_identity_id = uuid.uuid4()
        current_story_id = uuid.uuid4()
        parent_story_id = uuid.uuid4()
        member_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        self.session.add_all(
            [
                EventMapStoryIdentity(
                    id=stable_identity_id,
                    playlist_id=self.playlist.id,
                    created_snapshot_id=self.parent.id,
                ),
                EventMapStoryIdentity(
                    id=current_story_id,
                    playlist_id=self.playlist.id,
                    created_snapshot_id=self.current.id,
                ),
                EventMapStory(
                    snapshot_id=self.parent.id,
                    story_id=parent_story_id,
                    story_identity_id=stable_identity_id,
                    title="政策路径",
                ),
                EventMapStory(
                    snapshot_id=self.current.id,
                    story_id=current_story_id,
                    story_identity_id=current_story_id,
                    title="政策路径更新",
                ),
            ]
        )
        for position, canonical_id in enumerate(member_ids):
            self.session.add_all(
                [
                    EventMapStoryMember(
                        snapshot_id=self.parent.id,
                        story_id=parent_story_id,
                        position=position,
                        canonical_id=canonical_id,
                    ),
                    EventMapStoryMember(
                        snapshot_id=self.current.id,
                        story_id=current_story_id,
                        position=position,
                        canonical_id=canonical_id,
                    ),
                ]
            )
        self.session.commit()

        linked = _relink_legacy_story_identities_for_bootstrap(
            self.session,
            snapshot_id=self.current.id,
            parent_snapshot_id=self.parent.id,
        )
        self.session.commit()

        current_story = self.session.get(EventMapStory, (self.current.id, current_story_id))
        self.assertEqual(linked, 1)
        self.assertEqual(current_story.story_identity_id, stable_identity_id)

    def test_hot_review_flag_and_history_membership_are_relational(self) -> None:
        canonical_id = uuid.uuid4()
        member_id = uuid.uuid4()
        evidence_id = uuid.uuid4()
        rows = _canonical_history_member_rows(
            playlist_id=self.playlist.id,
            snapshot_id=self.current.id,
            payloads={
                canonical_id: {
                    "member_revision_ids": [str(member_id), str(evidence_id)],
                    "evidence_revision_ids": [str(evidence_id)],
                }
            },
        )

        self.assertEqual(len(rows), 2)
        self.assertFalse(next(row for row in rows if row["record_revision_id"] == member_id)["is_evidence"])
        self.assertTrue(next(row for row in rows if row["record_revision_id"] == evidence_id)["is_evidence"])
        self.assertIn("has_uncertainty", EventMapCanonical.__table__.columns)
        self.assertIn(
            "event_map_canonical_history_member_record_idx",
            {index.name for index in EventMapCanonicalHistoryMember.__table__.indexes},
        )

        story_id = uuid.uuid4()
        story_edge_id = uuid.uuid4()
        story_rows = _story_history_evidence_rows(
            playlist_id=self.playlist.id,
            snapshot_id=self.current.id,
            payloads={
                story_id: {
                    "edges": [
                        {
                            "edge_id": str(story_edge_id),
                            "source_canonical_id": str(uuid.uuid4()),
                            "target_canonical_id": str(uuid.uuid4()),
                            "relation_type": "continues",
                            "evidence_revision_ids": [str(evidence_id)],
                        }
                    ]
                }
            },
        )
        self.assertEqual(story_rows[0]["edge_id"], story_edge_id)
        self.assertEqual(story_rows[0]["record_revision_id"], evidence_id)
        self.assertIn(
            "event_map_story_history_evidence_record_idx",
            {index.name for index in EventMapStoryHistoryEvidence.__table__.indexes},
        )
        self.assertIn(
            "event_map_topic_member_topic_idx",
            {index.name for index in EventMapTopicMember.__table__.indexes},
        )

    def test_correction_change_requires_a_new_explicit_evidence_edge(self) -> None:
        source_id = uuid.uuid4()
        target_id = uuid.uuid4()
        correction = {
            "source_canonical_id": str(source_id),
            "target_canonical_id": str(target_id),
            "relation_type": "corrects",
            "evidence_revision_ids": [str(uuid.uuid4())],
        }

        self.assertTrue(_story_has_new_correction({"edges": []}, {"edges": [correction]}))
        self.assertFalse(_story_has_new_correction({"edges": [correction]}, {"edges": [correction]}))
        self.assertFalse(
            _story_has_new_correction(
                {"edges": []},
                {"edges": [{**correction, "evidence_revision_ids": []}]},
            )
        )

    def test_structured_brief_links_canonical_story_evidence_and_source(self) -> None:
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="channel-v2",
            url="https://example.test/channel",
            name="测试信源",
        )
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="video-v2",
            media_id=media.id,
            url="https://example.test/video",
            title="政策发布会",
        )
        canonical_id = uuid.uuid4()
        revision = EventMapRecordRevision(
            id=uuid.uuid4(),
            source_video_id=video.id,
            content_hash="content-v2",
            embedding_checksum="embedding-v2",
            title="政策发布",
            summary="政策正式发布。",
            event_time_start=self.now,
            event_time_end=self.now,
            time_precision="day",
            event_type="policy",
            evidence_json=[{"evidence_text": "正式发布", "verified": True}],
        )
        canonical = EventMapCanonical(
            snapshot_id=self.current.id,
            canonical_id=canonical_id,
            representative_revision_id=revision.id,
            title="政策发布",
            summary="政策正式发布。",
            event_type="policy",
            event_time_start=self.now,
            event_time_end=self.now,
            time_precision="day",
            event_start_day=1,
            event_end_day=1,
            event_type_code=1,
            time_precision_code=1,
            centroid_checksum="centroid-v2",
            point_index=0,
            x=0,
            y=0,
            z=0,
        )
        story_identity_id = uuid.uuid4()
        story_id = uuid.uuid4()
        story_history_id = uuid.uuid4()
        edge_id = uuid.uuid4()
        target_id = uuid.uuid4()
        topic_id = uuid.uuid4()
        brief = Brief(
            id=uuid.uuid4(),
            playlist_id=self.playlist.id,
            granularity="day",
            period_start=self.now.date(),
        )
        self.current.canonical_count = 1
        self.session.add_all(
            [
                media,
                video,
                EventMapState(playlist_id=self.playlist.id, current_snapshot_id=self.current.id),
                EventMapCanonicalIdentity(
                    id=canonical_id,
                    playlist_id=self.playlist.id,
                    created_snapshot_id=self.current.id,
                ),
                revision,
                canonical,
                EventMapCanonicalMember(
                    snapshot_id=self.current.id,
                    canonical_id=canonical_id,
                    record_revision_id=revision.id,
                    is_representative=True,
                ),
                EventMapTopic(
                    snapshot_id=self.current.id,
                    topic_id=topic_id,
                    level=0,
                    label="宏观政策",
                    top_terms=["政策"],
                    centroid_vector=b"vector",
                    anchor_canonical_id=canonical_id,
                    center_x=0,
                    center_y=0,
                    center_z=0,
                    radius=1,
                    canonical_count=1,
                    member_count=1,
                ),
                EventMapTopicMember(
                    snapshot_id=self.current.id,
                    canonical_id=canonical_id,
                    level=0,
                    topic_id=topic_id,
                ),
                EventMapStoryIdentity(
                    id=story_identity_id,
                    playlist_id=self.playlist.id,
                    created_snapshot_id=self.current.id,
                ),
                EventMapStory(
                    snapshot_id=self.current.id,
                    story_id=story_id,
                    story_identity_id=story_identity_id,
                    title="政策路径",
                    event_time_start=self.now,
                    event_time_end=self.now,
                ),
                EventMapStoryHistoryRevision(
                    id=story_history_id,
                    playlist_id=self.playlist.id,
                    story_identity_id=story_identity_id,
                    snapshot_id=self.current.id,
                    story_id=story_id,
                    title="政策路径",
                    member_ids=[str(canonical_id), str(target_id)],
                    edges=[],
                    evidence_revision_ids=[str(revision.id)],
                    method_version="story-v1",
                    observed_at=self.current.finished_at,
                ),
                EventMapStoryHistoryEvidence(
                    history_revision_id=story_history_id,
                    edge_id=edge_id,
                    record_revision_id=revision.id,
                    playlist_id=self.playlist.id,
                    story_identity_id=story_identity_id,
                    snapshot_id=self.current.id,
                    source_canonical_id=canonical_id,
                    target_canonical_id=target_id,
                    relation_type="continuation",
                ),
                brief,
            ]
        )
        self.session.commit()

        markdown, count = attach_structured_brief_references(
            self.session,
            brief=brief,
            videos=[video],
            markdown="# 简报\n\n政策发生变化。",
        )
        self.session.commit()
        references = self.session.execute(
            select(BriefReference).where(BriefReference.brief_id == brief.id)
        ).scalars().all()

        self.assertEqual(count, 4)
        self.assertEqual({row.object_type for row in references}, {"canonical", "story", "evidence", "source"})
        self.assertIn('<a id="field-ref-4"></a>', markdown)
        self.assertEqual(brief.snapshot_id, self.current.id)
        directory = domain_directory(self.session)
        self.assertEqual(directory[0]["preview_points"][0]["event_type_code"], 1)
        topic = topic_detail(self.session, self.playlist.id, topic_id)
        self.assertEqual(topic["representatives"][0]["canonical_id"], str(canonical_id))
        topic_references = object_brief_references(
            self.session,
            playlist_id=self.playlist.id,
            object_type="topic",
            object_id=topic_id,
        )
        self.assertEqual(len(topic_references), 1)
        self.assertEqual(topic_references[0]["object_type"], "canonical")


if __name__ == "__main__":
    unittest.main()
