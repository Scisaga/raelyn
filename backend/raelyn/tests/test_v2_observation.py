from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from raelyn.models import (
    Asset,
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
    PlaylistMedia,
    StoryReadState,
    Video,
    VideoEventExtractionRun,
    VideoTimeEvidence,
)
from raelyn.services.brief_references import attach_structured_brief_references
from raelyn.api.playlists import _event_map_record_payload, _event_map_record_video_contexts
from raelyn.services.domain_observation_control import (
    enabled_observation_video_ids,
    set_domain_observation_enabled,
)
from raelyn.services.event_map_snapshot import (
    _assign_story_identities,
    _canonical_history_member_rows,
    _relink_legacy_story_identities_for_bootstrap,
    _story_history_evidence_rows,
    _story_has_new_correction,
)
from raelyn.services.event_map_domain import EVENT_MAP_STORY_VERSION
from raelyn.services.event_analysis import event_extraction_spec
from raelyn.services.v2_observation import (
    _story_delta_is_material,
    _story_revision_delta,
    brief_reference_payload,
    canonical_directory,
    canonical_history,
    change_payload,
    domain_bootstrap_directory,
    domain_directory,
    domain_observation,
    event_highlights,
    list_changes,
    object_brief_references,
    story_directory,
    story_edge_support,
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

        @event.listens_for(self.engine, "connect")
        def register_make_timestamptz(dbapi_connection, _connection_record) -> None:
            """SQLite 没有 PostgreSQL 的日期构造函数；测试中保留相同 UTC 语义。"""

            dbapi_connection.create_function(
                "make_timestamptz",
                7,
                lambda year, month, day, hour, minute, second, _timezone_name: datetime(
                    int(year),
                    int(month),
                    int(day),
                    int(hour),
                    int(minute),
                    int(second),
                ).isoformat(sep=" "),
            )

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
            story_algorithm_version=EVENT_MAP_STORY_VERSION,
            finished_at=finished_at,
        )

    def _canonical(
        self,
        *,
        snapshot_id: uuid.UUID,
        canonical_id: uuid.UUID,
        point_index: int,
        title: str,
        occurred_at: datetime,
        summary: str | None = None,
        has_uncertainty: bool = False,
        time_precision: str = "day",
    ) -> EventMapCanonical:
        event_day = occurred_at.date().toordinal() - date(1970, 1, 1).toordinal()
        return EventMapCanonical(
            snapshot_id=snapshot_id,
            canonical_id=canonical_id,
            representative_revision_id=uuid.uuid4(),
            title=title,
            summary=summary or f"{title}摘要",
            event_type="policy",
            event_time_start=occurred_at,
            event_time_end=occurred_at,
            time_precision=time_precision,
            event_start_day=event_day,
            event_end_day=event_day,
            event_type_code=1,
            time_precision_code=1,
            member_count=1,
            has_uncertainty=has_uncertainty,
            uncertainty_flags=["time"] if has_uncertainty else [],
            centroid_checksum=f"centroid-{snapshot_id}-{canonical_id}",
            point_index=point_index,
            x=float(point_index),
            y=0,
            z=0,
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

    def test_event_highlights_returns_one_item_per_event_even_for_same_video(self) -> None:
        self.session.add(EventMapState(playlist_id=self.playlist.id, current_snapshot_id=self.current.id))
        media_avatar_asset = Asset(
            type="image",
            format="webp",
            source="profile",
            variant="avatar",
            s3_bucket="test-assets",
            s3_key="media/focus/avatar.webp",
        )
        self.session.add(media_avatar_asset)
        self.session.flush()
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="focus-channel",
            url="https://example.test/focus-channel",
            name="焦点频道",
            avatar_asset_id=media_avatar_asset.id,
        )
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="focus-video",
            media_id=media.id,
            url="https://example.test/focus-video",
            title="今日市场视频",
            thumbnail_url="https://example.test/focus.jpg",
            duration_sec=180,
            published_at=self.now,
        )
        playable_id = uuid.uuid4()
        secondary_id = uuid.uuid4()
        playable_revision = EventMapRecordRevision(
            id=uuid.uuid4(),
            source_video_id=video.id,
            content_hash="focus-playable",
            embedding_checksum="focus-playable-embedding",
            title="今日市场事件",
            summary="今日市场发生变化",
            event_time_start=self.now,
            event_time_end=self.now,
            time_precision="day",
            event_type="policy",
            evidence_json=[{"evidence_json": {"start_seconds": 42}}],
        )
        secondary_revision = EventMapRecordRevision(
            id=uuid.uuid4(),
            source_video_id=video.id,
            content_hash="focus-secondary",
            embedding_checksum="focus-secondary-embedding",
            title="同视频关联事件",
            summary="应合并到同一视频卡片",
            event_time_start=self.now,
            event_time_end=self.now,
            time_precision="day",
            event_type="policy",
        )
        playable = self._canonical(
            snapshot_id=self.current.id,
            canonical_id=playable_id,
            point_index=4,
            title="今日市场事件",
            occurred_at=self.now,
        )
        playable.representative_revision_id = playable_revision.id
        secondary = self._canonical(
            snapshot_id=self.current.id,
            canonical_id=secondary_id,
            point_index=7,
            title="同视频关联事件",
            occurred_at=self.now,
        )
        secondary.representative_revision_id = secondary_revision.id
        topic = EventMapTopic(
            snapshot_id=self.current.id,
            topic_id=uuid.uuid4(),
            level=0,
            label="市场与资产",
            centroid_vector=b"topic",
            center_x=0,
            center_y=0,
            center_z=0,
            canonical_count=1,
            member_count=1,
        )
        self.session.add_all([
            media,
            PlaylistMedia(playlist_id=self.playlist.id, media_id=media.id),
            video,
            playable_revision,
            secondary_revision,
            playable,
            secondary,
            EventMapCanonicalMember(snapshot_id=self.current.id, canonical_id=playable_id, record_revision_id=playable_revision.id, is_representative=True),
            EventMapCanonicalMember(snapshot_id=self.current.id, canonical_id=secondary_id, record_revision_id=secondary_revision.id, is_representative=True),
            topic,
            EventMapTopicMember(snapshot_id=self.current.id, canonical_id=playable_id, level=0, topic_id=topic.topic_id),
            Asset(video_id=video.id, type="video", format="mp4", source="download", s3_bucket="test-assets", s3_key="videos/focus.mp4"),
            Asset(video_id=video.id, type="thumbnail", format="jpg", source="download", s3_bucket="test-assets", s3_key="videos/focus.jpg"),
        ])
        self.session.commit()

        payload = event_highlights(
            self.session,
            self.playlist.id,
            snapshot_id=self.current.id,
            event_date_start=self.now.date(),
            event_date_end=self.now.date(),
            limit=3,
        )

        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["matched_event_total"], 2)
        self.assertEqual(payload["shown_total"], 2)
        self.assertEqual(payload["playable_event_total"], 2)
        self.assertEqual(payload["video_total"], 1)
        self.assertEqual(payload["max_cards_per_media"], 2)
        self.assertEqual(payload["point_indices"], [4, 7])
        self.assertEqual(len(payload["items"]), 2)
        self.assertEqual(payload["items"][0]["canonical_id"], str(playable_id))
        self.assertEqual(payload["items"][1]["canonical_id"], str(secondary_id))
        self.assertEqual(payload["items"][0]["canonical_count"], 1)
        self.assertEqual(payload["items"][0]["point_indices"], [4])
        self.assertEqual(payload["items"][0]["representative_rank"], 1)
        self.assertEqual(payload["items"][0]["ranking_factors"]["source_count"], 1)
        self.assertEqual(payload["items"][0]["ranking_factors"]["video_count"], 1)
        self.assertEqual(payload["items"][0]["topic"]["label"], "市场与资产")
        self.assertEqual(payload["items"][0]["primary_video"]["video_id"], str(video.id))
        self.assertEqual(payload["items"][0]["primary_video"]["media_id"], str(media.id))
        self.assertEqual(
            payload["items"][0]["primary_video"]["media_avatar_asset"]["id"],
            media_avatar_asset.id,
        )
        self.assertIsNone(
            payload["items"][0]["primary_video"]["media_avatar_asset"]["presigned_url"]
        )
        self.assertEqual(payload["items"][0]["primary_video"]["source_date"], self.now.date().isoformat())
        self.assertEqual(payload["items"][0]["primary_video"]["time_source"], "video.published_at")
        self.assertEqual(payload["items"][0]["primary_video"]["time_status"], "platform_fallback")
        self.assertIsNotNone(payload["items"][0]["primary_video"]["timeline_at"])
        self.assertEqual(payload["items"][0]["primary_video"]["playback_position_seconds"], 42.0)
        self.assertIsNotNone(payload["items"][0]["primary_video"]["thumbnail_asset"])

        member_rows = self.session.execute(
            select(EventMapCanonicalMember, EventMapRecordRevision)
            .join(EventMapRecordRevision, EventMapRecordRevision.id == EventMapCanonicalMember.record_revision_id)
            .where(EventMapCanonicalMember.snapshot_id == self.current.id)
        ).all()
        contexts, videos = _event_map_record_video_contexts(self.session, member_rows)
        playable_member, playable_record = next(
            (member, revision)
            for member, revision in member_rows
            if revision.id == playable_revision.id
        )
        record_payload = _event_map_record_payload(
            playable_record,
            playable_member,
            contexts[video.id],
        )
        self.assertEqual(videos[0]["video_id"], str(video.id))
        self.assertEqual(videos[0]["support_record_count"], 2)
        self.assertEqual(record_payload["source_context"]["media_name"], "焦点频道")
        self.assertEqual(record_payload["playback_position_seconds"], 42.0)

    def test_event_highlights_ranks_cross_media_evidence_before_recency(self) -> None:
        self.session.add(EventMapState(playlist_id=self.playlist.id, current_snapshot_id=self.current.id))
        first_media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="ranking-channel-a",
            url="https://example.test/ranking-channel-a",
            name="信源 A",
        )
        second_media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="ranking-channel-b",
            url="https://example.test/ranking-channel-b",
            name="信源 B",
        )
        first_video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="ranking-video-a",
            media_id=first_media.id,
            url="https://example.test/ranking-video-a",
            title="信源 A 视频",
            published_at=self.now,
        )
        second_video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="ranking-video-b",
            media_id=second_media.id,
            url="https://example.test/ranking-video-b",
            title="信源 B 视频",
            published_at=self.now,
        )
        corroborated_id = uuid.uuid4()
        recent_id = uuid.uuid4()
        corroborated_first = EventMapRecordRevision(
            id=uuid.uuid4(),
            source_video_id=first_video.id,
            content_hash="ranking-corroborated-a",
            embedding_checksum="ranking-corroborated-a-embedding",
            title="较早但跨媒体佐证",
            event_time_start=self.now - timedelta(minutes=20),
            event_time_end=self.now - timedelta(minutes=20),
            time_precision="day",
            event_type="policy",
        )
        corroborated_second = EventMapRecordRevision(
            id=uuid.uuid4(),
            source_video_id=second_video.id,
            content_hash="ranking-corroborated-b",
            embedding_checksum="ranking-corroborated-b-embedding",
            title="较早但跨媒体佐证",
            event_time_start=self.now - timedelta(minutes=20),
            event_time_end=self.now - timedelta(minutes=20),
            time_precision="day",
            event_type="policy",
        )
        recent_revision = EventMapRecordRevision(
            id=uuid.uuid4(),
            source_video_id=first_video.id,
            content_hash="ranking-recent",
            embedding_checksum="ranking-recent-embedding",
            title="更新但单一信源",
            event_time_start=self.now,
            event_time_end=self.now,
            time_precision="day",
            event_type="policy",
        )
        corroborated = self._canonical(
            snapshot_id=self.current.id,
            canonical_id=corroborated_id,
            point_index=10,
            title="较早但跨媒体佐证",
            occurred_at=self.now - timedelta(minutes=20),
        )
        corroborated.representative_revision_id = corroborated_first.id
        corroborated.member_count = 2
        recent = self._canonical(
            snapshot_id=self.current.id,
            canonical_id=recent_id,
            point_index=11,
            title="更新但单一信源",
            occurred_at=self.now,
        )
        recent.representative_revision_id = recent_revision.id
        self.session.add_all([
            first_media,
            second_media,
            PlaylistMedia(playlist_id=self.playlist.id, media_id=first_media.id),
            PlaylistMedia(playlist_id=self.playlist.id, media_id=second_media.id),
            first_video,
            second_video,
            corroborated_first,
            corroborated_second,
            recent_revision,
            corroborated,
            recent,
            EventMapCanonicalMember(snapshot_id=self.current.id, canonical_id=corroborated_id, record_revision_id=corroborated_first.id, is_representative=True),
            EventMapCanonicalMember(snapshot_id=self.current.id, canonical_id=corroborated_id, record_revision_id=corroborated_second.id, is_representative=False),
            EventMapCanonicalMember(snapshot_id=self.current.id, canonical_id=recent_id, record_revision_id=recent_revision.id, is_representative=True),
            Asset(video_id=first_video.id, type="video", format="mp4", source="download", s3_bucket="test-assets", s3_key="videos/ranking-a.mp4"),
            Asset(video_id=second_video.id, type="video", format="mp4", source="download", s3_bucket="test-assets", s3_key="videos/ranking-b.mp4"),
        ])
        self.session.commit()

        payload = event_highlights(
            self.session,
            self.playlist.id,
            snapshot_id=self.current.id,
            event_date_start=self.now.date(),
            event_date_end=self.now.date(),
            limit=10,
        )

        self.assertEqual(payload["playable_event_total"], 2)
        self.assertEqual(
            [item["canonical_id"] for item in payload["items"]],
            [str(corroborated_id), str(recent_id)],
        )
        self.assertEqual(payload["items"][0]["canonical_ids"], [str(corroborated_id)])
        self.assertEqual(payload["items"][0]["canonical_count"], 1)
        self.assertEqual(payload["items"][1]["canonical_ids"], [str(recent_id)])
        self.assertEqual(payload["items"][0]["ranking_factors"]["source_count"], 2)
        self.assertEqual(payload["items"][1]["ranking_factors"]["source_count"], 1)
        self.assertEqual([item["representative_rank"] for item in payload["items"]], [1, 2])

    def test_event_highlights_uses_event_day_not_video_publish_day(self) -> None:
        self.session.add(EventMapState(playlist_id=self.playlist.id, current_snapshot_id=self.current.id))
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="content-time-channel",
            url="https://example.test/content-time-channel",
            name="内容时间频道",
        )
        content_video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="content-time-video",
            media_id=media.id,
            url="https://example.test/content-time-video",
            title="内容日期属于今天",
            published_at=self.now,
        )
        old_video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="old-platform-video",
            media_id=media.id,
            url="https://example.test/old-platform-video",
            title="历史视频",
            published_at=self.now - timedelta(days=2),
        )
        current_id = uuid.uuid4()
        outside_window_id = uuid.uuid4()
        old_video_id = uuid.uuid4()
        current_revision = EventMapRecordRevision(
            id=uuid.uuid4(),
            source_video_id=content_video.id,
            content_hash="content-time-current",
            embedding_checksum="content-time-current-embedding",
            title="窗口内事件",
            event_time_start=self.now,
            event_time_end=self.now,
            time_precision="day",
            event_type="policy",
        )
        outside_window_revision = EventMapRecordRevision(
            id=uuid.uuid4(),
            source_video_id=content_video.id,
            content_hash="content-time-outside-window",
            embedding_checksum="content-time-outside-window-embedding",
            title="窗口外事件",
            event_time_start=self.now - timedelta(days=1),
            event_time_end=self.now - timedelta(days=1),
            time_precision="day",
            event_type="policy",
        )
        old_video_revision = EventMapRecordRevision(
            id=uuid.uuid4(),
            source_video_id=old_video.id,
            content_hash="old-platform-current-event",
            embedding_checksum="old-platform-current-event-embedding",
            title="历史视频中的当前事件",
            event_time_start=self.now,
            event_time_end=self.now,
            time_precision="day",
            event_type="policy",
        )
        current = self._canonical(
            snapshot_id=self.current.id,
            canonical_id=current_id,
            point_index=30,
            title="窗口内事件",
            occurred_at=self.now,
        )
        current.representative_revision_id = current_revision.id
        outside_window = self._canonical(
            snapshot_id=self.current.id,
            canonical_id=outside_window_id,
            point_index=31,
            title="窗口外事件",
            occurred_at=self.now - timedelta(days=1),
        )
        outside_window.representative_revision_id = outside_window_revision.id
        old_video_canonical = self._canonical(
            snapshot_id=self.current.id,
            canonical_id=old_video_id,
            point_index=32,
            title="历史视频中的当前事件",
            occurred_at=self.now,
        )
        old_video_canonical.representative_revision_id = old_video_revision.id
        self.session.add_all([
            media,
            PlaylistMedia(playlist_id=self.playlist.id, media_id=media.id),
            content_video,
            old_video,
            VideoTimeEvidence(
                video_id=content_video.id,
                time_role="content_published_at",
                source="manual_test",
                evidence_key="accepted-content-date",
                date_year=(self.now - timedelta(days=1)).year,
                date_month=(self.now - timedelta(days=1)).month,
                date_day=(self.now - timedelta(days=1)).day,
                precision="day",
                confidence=0.95,
                status="accepted",
            ),
            current_revision,
            outside_window_revision,
            old_video_revision,
            current,
            outside_window,
            old_video_canonical,
            EventMapCanonicalMember(snapshot_id=self.current.id, canonical_id=current_id, record_revision_id=current_revision.id, is_representative=True),
            EventMapCanonicalMember(snapshot_id=self.current.id, canonical_id=outside_window_id, record_revision_id=outside_window_revision.id, is_representative=True),
            EventMapCanonicalMember(snapshot_id=self.current.id, canonical_id=old_video_id, record_revision_id=old_video_revision.id, is_representative=True),
            Asset(video_id=content_video.id, type="video", format="mp4", source="download", s3_bucket="test-assets", s3_key="videos/content-time.mp4"),
            Asset(video_id=old_video.id, type="video", format="mp4", source="download", s3_bucket="test-assets", s3_key="videos/old-platform.mp4"),
        ])
        self.session.commit()

        payload = event_highlights(
            self.session,
            self.playlist.id,
            snapshot_id=self.current.id,
            event_date_start=self.now.date(),
            event_date_end=self.now.date(),
        )

        self.assertEqual(payload["matched_event_total"], 2)
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["playable_event_total"], 2)
        self.assertEqual(payload["point_indices"], [30, 32])
        self.assertEqual(
            {item["canonical_id"] for item in payload["items"]},
            {str(current_id), str(old_video_id)},
        )
        self.assertNotIn(str(outside_window_id), {item["canonical_id"] for item in payload["items"]})
        current_item = next(item for item in payload["items"] if item["canonical_id"] == str(current_id))
        self.assertEqual(current_item["primary_video"]["source_date"], "2026-08-15")
        self.assertTrue(current_item["primary_video"]["content_published_at"].startswith("2026-08-15"))
        self.assertEqual(current_item["primary_video"]["time_source"], "manual_test")
        self.assertEqual(current_item["primary_video"]["time_status"], "accepted")

    def test_event_highlights_keeps_matching_point_without_cross_domain_video_card(self) -> None:
        self.session.add(EventMapState(playlist_id=self.playlist.id, current_snapshot_id=self.current.id))
        outside_media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="outside-domain-channel",
            url="https://example.test/outside-domain-channel",
            name="域外信源",
        )
        outside_video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="outside-domain-video",
            media_id=outside_media.id,
            url="https://example.test/outside-domain-video",
            title="域外关联视频",
            published_at=self.now,
        )
        revision = EventMapRecordRevision(
            id=uuid.uuid4(),
            source_video_id=outside_video.id,
            content_hash="outside-domain-event",
            embedding_checksum="outside-domain-event-embedding",
            title="今日域内事件",
            event_time_start=self.now,
            event_time_end=self.now,
            time_precision="day",
            event_type="policy",
        )
        canonical_id = uuid.uuid4()
        canonical = self._canonical(
            snapshot_id=self.current.id,
            canonical_id=canonical_id,
            point_index=39,
            title="今日域内事件",
            occurred_at=self.now,
        )
        canonical.representative_revision_id = revision.id
        self.session.add_all([
            outside_media,
            outside_video,
            revision,
            canonical,
            EventMapCanonicalMember(
                snapshot_id=self.current.id,
                canonical_id=canonical_id,
                record_revision_id=revision.id,
                is_representative=True,
            ),
            Asset(
                video_id=outside_video.id,
                type="video",
                format="mp4",
                source="download",
                s3_bucket="test-assets",
                s3_key="videos/outside-domain.mp4",
            ),
        ])
        self.session.commit()

        payload = event_highlights(
            self.session,
            self.playlist.id,
            snapshot_id=self.current.id,
            event_date_start=self.now.date(),
            event_date_end=self.now.date(),
        )

        self.assertEqual(payload["matched_event_total"], 1)
        self.assertEqual(payload["playable_event_total"], 0)
        self.assertEqual(payload["point_indices"], [39])
        self.assertEqual(payload["items"], [])

    def test_event_highlights_diversifies_video_within_event_without_reordering_events(self) -> None:
        self.session.add(EventMapState(playlist_id=self.playlist.id, current_snapshot_id=self.current.id))
        media_by_name: dict[str, Media] = {}
        for name in ("信源 A", "信源 B", "信源 C", "信源 D"):
            media = Media(
                id=uuid.uuid4(),
                provider="youtube",
                provider_media_id=f"diversity-{name}",
                url=f"https://example.test/diversity-{name}",
                name=name,
            )
            media_by_name[name] = media
            self.session.add_all([
                media,
                PlaylistMedia(playlist_id=self.playlist.id, media_id=media.id),
            ])

        def add_event(
            *,
            point_index: int,
            title: str,
            media_names: tuple[str, ...],
        ) -> uuid.UUID:
            canonical_id = uuid.uuid4()
            canonical = self._canonical(
                snapshot_id=self.current.id,
                canonical_id=canonical_id,
                point_index=point_index,
                title=title,
                occurred_at=self.now,
            )
            canonical.member_count = len(media_names)
            self.session.add(canonical)
            for index, media_name in enumerate(media_names):
                media = media_by_name[media_name]
                suffix = f"{point_index}-{index}"
                video = Video(
                    id=uuid.uuid4(),
                    provider="youtube",
                    provider_video_id=f"diversity-video-{suffix}",
                    media_id=media.id,
                    url=f"https://example.test/diversity-video-{suffix}",
                    title=f"{media_name} 视频 {suffix}",
                    published_at=self.now,
                )
                revision = EventMapRecordRevision(
                    id=uuid.uuid4(),
                    source_video_id=video.id,
                    content_hash=f"diversity-{suffix}",
                    embedding_checksum=f"diversity-{suffix}-embedding",
                    title=title,
                    event_time_start=self.now,
                    event_time_end=self.now,
                    time_precision="day",
                    event_type="policy",
                )
                if index == 0:
                    canonical.representative_revision_id = revision.id
                self.session.add_all([
                    video,
                    revision,
                    EventMapCanonicalMember(
                        snapshot_id=self.current.id,
                        canonical_id=canonical_id,
                        record_revision_id=revision.id,
                        is_representative=index == 0,
                    ),
                    Asset(
                        video_id=video.id,
                        type="video",
                        format="mp4",
                        source="download",
                        s3_bucket="test-assets",
                        s3_key=f"videos/{suffix}.mp4",
                    ),
                ])
            return canonical_id

        stronger_id = add_event(
            point_index=40,
            title="三信源事件",
            media_names=("信源 A", "信源 C", "信源 D"),
        )
        diversified_id = add_event(
            point_index=41,
            title="双信源事件",
            media_names=("信源 A", "信源 B"),
        )
        self.session.commit()

        payload = event_highlights(
            self.session,
            self.playlist.id,
            snapshot_id=self.current.id,
            event_date_start=self.now.date(),
            event_date_end=self.now.date(),
            limit=2,
        )

        self.assertEqual(
            [item["canonical_id"] for item in payload["items"]],
            [str(stronger_id), str(diversified_id)],
        )
        self.assertEqual(
            [item["primary_video"]["media_name"] for item in payload["items"]],
            ["信源 A", "信源 B"],
        )

    def test_event_highlights_rejects_event_date_range_longer_than_one_week(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not exceed 7 days"):
            event_highlights(
                self.session,
                self.playlist.id,
                event_date_start=self.now.date() - timedelta(days=7),
                event_date_end=self.now.date(),
                window_start=self.now.date() - timedelta(days=365),
                window_end=self.now.date(),
            )

    def test_event_highlights_limits_each_media_to_two_cards(self) -> None:
        self.session.add(EventMapState(playlist_id=self.playlist.id, current_snapshot_id=self.current.id))
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="limit-channel",
            url="https://example.test/limit-channel",
            name="数量测试频道",
        )
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="limit-video",
            media_id=media.id,
            url="https://example.test/limit-video",
            title="同一视频中的多个今日事件",
            published_at=self.now - timedelta(days=3),
        )
        self.session.add_all([
            media,
            PlaylistMedia(playlist_id=self.playlist.id, media_id=media.id),
            video,
            Asset(
                video_id=video.id,
                type="video",
                format="mp4",
                source="download",
                s3_bucket="test-assets",
                s3_key="videos/limit.mp4",
            ),
        ])
        canonical_ids: list[uuid.UUID] = []
        for point_index in range(100, 112):
            canonical_id = uuid.uuid4()
            revision = EventMapRecordRevision(
                id=uuid.uuid4(),
                source_video_id=video.id,
                content_hash=f"limit-{point_index}",
                embedding_checksum=f"limit-{point_index}-embedding",
                title=f"今日事件 {point_index}",
                event_time_start=self.now,
                event_time_end=self.now,
                time_precision="day",
                event_type="policy",
            )
            canonical = self._canonical(
                snapshot_id=self.current.id,
                canonical_id=canonical_id,
                point_index=point_index,
                title=f"今日事件 {point_index}",
                occurred_at=self.now,
            )
            canonical.representative_revision_id = revision.id
            canonical_ids.append(canonical_id)
            self.session.add_all([
                revision,
                canonical,
                EventMapCanonicalMember(
                    snapshot_id=self.current.id,
                    canonical_id=canonical_id,
                    record_revision_id=revision.id,
                    is_representative=True,
                ),
            ])
        self.session.commit()

        payload = event_highlights(
            self.session,
            self.playlist.id,
            snapshot_id=self.current.id,
            event_date_start=self.now.date(),
            event_date_end=self.now.date(),
            limit=10,
        )

        self.assertEqual(payload["matched_event_total"], 12)
        self.assertEqual(payload["total"], 12)
        self.assertEqual(payload["shown_total"], 2)
        self.assertEqual(payload["video_total"], 1)
        self.assertEqual(payload["max_cards_per_media"], 2)
        self.assertEqual(len(payload["point_indices"]), 12)
        self.assertEqual(len({item["canonical_id"] for item in payload["items"]}), 2)
        self.assertEqual(
            {item["primary_video"]["media_id"] for item in payload["items"]},
            {str(media.id)},
        )

    def test_event_highlights_excludes_month_and_year_precision(self) -> None:
        self.session.add(EventMapState(playlist_id=self.playlist.id, current_snapshot_id=self.current.id))
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="precision-channel",
            url="https://example.test/precision-channel",
            name="精度测试频道",
        )
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="precision-video",
            media_id=media.id,
            url="https://example.test/precision-video",
            title="不同精度事件",
            published_at=self.now,
        )
        self.session.add_all([
            media,
            PlaylistMedia(playlist_id=self.playlist.id, media_id=media.id),
            video,
            Asset(
                video_id=video.id,
                type="video",
                format="mp4",
                source="download",
                s3_bucket="test-assets",
                s3_key="videos/precision.mp4",
            ),
        ])
        precise_canonical_id = uuid.uuid4()
        for offset, precision in enumerate(("day", "month", "year"), start=1):
            canonical_id = precise_canonical_id if precision == "day" else uuid.uuid4()
            revision = EventMapRecordRevision(
                id=uuid.uuid4(),
                source_video_id=video.id,
                content_hash=f"precision-{precision}",
                embedding_checksum=f"precision-{precision}-embedding",
                title=f"{precision} 事件",
                event_time_start=self.now,
                event_time_end=self.now,
                time_precision=precision,
                event_type="policy",
            )
            canonical = self._canonical(
                snapshot_id=self.current.id,
                canonical_id=canonical_id,
                point_index=120 + offset,
                title=f"{precision} 事件",
                occurred_at=self.now,
                time_precision=precision,
            )
            canonical.representative_revision_id = revision.id
            self.session.add_all([
                revision,
                canonical,
                EventMapCanonicalMember(
                    snapshot_id=self.current.id,
                    canonical_id=canonical_id,
                    record_revision_id=revision.id,
                    is_representative=True,
                ),
            ])
        self.session.commit()

        payload = event_highlights(
            self.session,
            self.playlist.id,
            snapshot_id=self.current.id,
            event_date_start=self.now.date(),
            event_date_end=self.now.date(),
        )

        self.assertEqual(payload["matched_event_total"], 1)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["excluded_imprecise_total"], 2)
        self.assertEqual(payload["shown_total"], 1)
        self.assertEqual(payload["items"][0]["canonical_id"], str(precise_canonical_id))

    def test_event_highlights_collapses_two_videos_for_one_event(self) -> None:
        self.session.add(EventMapState(playlist_id=self.playlist.id, current_snapshot_id=self.current.id))
        media_a = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="diversity-overlap-a",
            url="https://example.test/diversity-overlap-a",
            name="信源 A",
        )
        media_b = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="diversity-overlap-b",
            url="https://example.test/diversity-overlap-b",
            name="信源 B",
        )
        self.session.add_all([
            media_a,
            media_b,
            PlaylistMedia(playlist_id=self.playlist.id, media_id=media_a.id),
            PlaylistMedia(playlist_id=self.playlist.id, media_id=media_b.id),
        ])

        primary_canonical_id = uuid.uuid4()
        primary_canonical = self._canonical(
            snapshot_id=self.current.id,
            canonical_id=primary_canonical_id,
            point_index=50,
            title="两家媒体共同报道",
            occurred_at=self.now,
        )
        primary_canonical.member_count = 2
        videos: list[Video] = []
        primary_revisions: list[EventMapRecordRevision] = []
        for index, media in enumerate((media_a, media_b), start=1):
            video = Video(
                id=uuid.uuid4(),
                provider="youtube",
                provider_video_id=f"diversity-overlap-{index}",
                media_id=media.id,
                url=f"https://example.test/diversity-overlap-{index}",
                title=f"{media.name} 共同报道",
                published_at=self.now,
            )
            revision = EventMapRecordRevision(
                id=uuid.uuid4(),
                source_video_id=video.id,
                content_hash=f"diversity-overlap-{index}",
                embedding_checksum=f"diversity-overlap-{index}-embedding",
                title="两家媒体共同报道",
                event_time_start=self.now,
                event_time_end=self.now,
                time_precision="day",
                event_type="policy",
            )
            videos.append(video)
            primary_revisions.append(revision)
            self.session.add_all([
                video,
                revision,
                EventMapCanonicalMember(
                    snapshot_id=self.current.id,
                    canonical_id=primary_canonical_id,
                    record_revision_id=revision.id,
                    is_representative=index == 1,
                ),
                Asset(
                    video_id=video.id,
                    type="video",
                    format="mp4",
                    source="download",
                    s3_bucket="test-assets",
                    s3_key=f"videos/diversity-overlap-{index}.mp4",
                ),
            ])
        primary_canonical.representative_revision_id = primary_revisions[0].id
        self.session.add(primary_canonical)

        for offset in (1, 2):
            video = Video(
                id=uuid.uuid4(),
                provider="youtube",
                provider_video_id=f"diversity-a-extra-{offset}",
                media_id=media_a.id,
                url=f"https://example.test/diversity-a-extra-{offset}",
                title=f"信源 A 独有报道 {offset}",
                published_at=self.now,
            )
            revision = EventMapRecordRevision(
                id=uuid.uuid4(),
                source_video_id=video.id,
                content_hash=f"diversity-a-extra-{offset}",
                embedding_checksum=f"diversity-a-extra-{offset}-embedding",
                title=f"信源 A 独有事件 {offset}",
                event_time_start=self.now - timedelta(minutes=offset),
                event_time_end=self.now - timedelta(minutes=offset),
                time_precision="day",
                event_type="policy",
            )
            canonical_id = uuid.uuid4()
            canonical = self._canonical(
                snapshot_id=self.current.id,
                canonical_id=canonical_id,
                point_index=50 + offset,
                title=f"信源 A 独有事件 {offset}",
                occurred_at=self.now - timedelta(minutes=offset),
            )
            canonical.representative_revision_id = revision.id
            self.session.add_all([
                video,
                revision,
                canonical,
                EventMapCanonicalMember(
                    snapshot_id=self.current.id,
                    canonical_id=canonical_id,
                    record_revision_id=revision.id,
                    is_representative=True,
                ),
                Asset(
                    video_id=video.id,
                    type="video",
                    format="mp4",
                    source="download",
                    s3_bucket="test-assets",
                    s3_key=f"videos/diversity-a-extra-{offset}.mp4",
                ),
            ])
        self.session.commit()

        payload = event_highlights(
            self.session,
            self.playlist.id,
            snapshot_id=self.current.id,
            event_date_start=self.now.date(),
            event_date_end=self.now.date(),
            limit=3,
        )

        self.assertEqual(
            [item["primary_video"]["media_name"] for item in payload["items"]],
            ["信源 A", "信源 A"],
        )
        self.assertEqual(len({item["canonical_id"] for item in payload["items"]}), 2)

    def test_domain_directory_returns_cover_assets_and_nulls_without_images(self) -> None:
        avatar_asset = Asset(
            id=uuid.uuid4(),
            type="image",
            format="webp",
            source="upload",
            s3_bucket="test-assets",
            s3_key="domains/macro/avatar.webp",
        )
        background_asset = Asset(
            id=uuid.uuid4(),
            type="image",
            format="jpeg",
            source="upload",
            s3_bucket="test-assets",
            s3_key="domains/macro/background.jpg",
        )
        media_avatar_asset = Asset(
            id=uuid.uuid4(),
            type="image",
            format="webp",
            source="profile",
            s3_bucket="test-assets",
            s3_key="media/channel/avatar.webp",
        )
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="channel-1",
            url="https://example.test/channel-1",
            name="财经频道",
            avatar_asset_id=media_avatar_asset.id,
        )
        videos = [
            Video(
                id=uuid.uuid4(),
                provider="youtube",
                provider_video_id=f"video-{index}",
                media_id=media.id,
                url=f"https://example.test/video-{index}",
            )
            for index in range(2)
        ]
        self.playlist.avatar_asset_id = avatar_asset.id
        self.playlist.background_asset_id = background_asset.id
        domain_without_images = Playlist(id=uuid.uuid4(), name="无图片观测域")
        self.session.add_all(
            [
                avatar_asset,
                background_asset,
                media_avatar_asset,
                media,
                PlaylistMedia(playlist_id=self.playlist.id, media_id=media.id),
                *videos,
                domain_without_images,
            ]
        )
        self.session.commit()

        directory = {
            item["id"]: item
            for item in domain_directory(self.session)
        }

        domain_with_images = directory[str(self.playlist.id)]
        self.assertEqual(domain_with_images["avatar_asset"].id, avatar_asset.id)
        self.assertEqual(domain_with_images["avatar_asset"].type, "image")
        self.assertEqual(domain_with_images["avatar_asset"].format, "webp")
        self.assertIsNone(domain_with_images["avatar_asset"].presigned_url)
        self.assertEqual(domain_with_images["background_asset"].id, background_asset.id)
        self.assertEqual(domain_with_images["background_asset"].format, "jpeg")
        self.assertIsNone(domain_with_images["background_asset"].presigned_url)
        self.assertIsNone(directory[str(domain_without_images.id)]["avatar_asset"])
        self.assertIsNone(directory[str(domain_without_images.id)]["background_asset"])
        self.assertTrue(domain_with_images["observation_enabled"])
        self.assertEqual(domain_with_images["brief_granularity"], "day")
        self.assertEqual(domain_with_images["media_count"], 1)
        self.assertEqual(domain_with_images["video_count"], 2)
        self.assertEqual(len(domain_with_images["media_preview"]), 1)
        self.assertEqual(domain_with_images["media_preview"][0]["id"], str(media.id))
        self.assertEqual(domain_with_images["media_preview"][0]["name"], "财经频道")
        self.assertEqual(domain_with_images["media_preview"][0]["avatar_asset"].id, media_avatar_asset.id)
        self.assertIsNone(domain_with_images["media_preview"][0]["avatar_asset"].presigned_url)
        self.assertEqual(directory[str(domain_without_images.id)]["media_preview"], [])
        self.assertEqual(directory[str(domain_without_images.id)]["video_count"], 0)

    def test_domain_directory_samples_preview_with_exact_point_indices(self) -> None:
        self.current.canonical_count = 192
        canonicals = [
            self._canonical(
                snapshot_id=self.current.id,
                canonical_id=uuid.uuid4(),
                point_index=point_index,
                title=f"事件 {point_index}",
                occurred_at=self.now,
            )
            for point_index in range(192)
        ]
        for point_index, canonical in enumerate(canonicals):
            canonical.event_type_code = point_index
        self.session.add_all(
            [
                EventMapState(playlist_id=self.playlist.id, current_snapshot_id=self.current.id),
                *canonicals,
            ]
        )
        self.session.commit()

        directory = domain_directory(self.session)
        current = next(item for item in directory if item["id"] == str(self.playlist.id))

        self.assertEqual(
            [point["event_type_code"] for point in current["preview_points"]],
            list(range(0, 192, 2)),
        )

    def test_domain_bootstrap_directory_only_returns_initial_selection_fields(self) -> None:
        self.session.add(
            EventMapState(
                playlist_id=self.playlist.id,
                current_snapshot_id=self.current.id,
            )
        )
        self.session.commit()

        directory = domain_bootstrap_directory(self.session)
        current = next(item for item in directory if item["id"] == str(self.playlist.id))

        self.assertEqual(current["name"], self.playlist.name)
        self.assertEqual(current["snapshot"]["id"], str(self.current.id))
        self.assertEqual(current["snapshot"]["status"], "ready")
        self.assertNotIn("bounds", current["snapshot"])
        self.assertNotIn("monthly_distribution", current["snapshot"])
        self.assertNotIn("preview_points", current)
        self.assertNotIn("media_preview", current)
        self.assertNotIn("unobserved_change_count", current)

    def test_domain_observation_exposes_brief_configuration_for_settings_page(self) -> None:
        self.playlist.brief_granularity = "week"
        self.playlist.brief_prompt = "只保留实质变化"
        self.session.commit()

        payload = domain_observation(self.session, self.playlist.id)

        self.assertEqual(payload["domain"]["brief_granularity"], "week")
        self.assertEqual(payload["domain"]["brief_prompt"], "只保留实质变化")

    def test_domain_observation_separates_current_event_extraction_results_from_legacy_results(self) -> None:
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="coverage-channel",
            url="https://example.test/coverage-channel",
        )
        videos = [
            Video(
                id=uuid.uuid4(),
                provider="youtube",
                provider_video_id=f"coverage-video-{index}",
                media_id=media.id,
                url=f"https://example.test/coverage-video-{index}",
            )
            for index in range(6)
        ]
        transcripts = [
            Asset(
                id=uuid.uuid4(),
                video_id=video.id,
                type="transcript",
                format="txt",
                source="asr",
                s3_bucket="test-assets",
                s3_key=f"transcripts/{video.id}.txt",
            )
            for video in videos[:5]
        ]
        spec = event_extraction_spec(self.session)
        runs = [
            VideoEventExtractionRun(
                video_id=videos[0].id,
                transcript_asset_id=transcripts[0].id,
                source_hash="current-with-events",
                prompt_version=spec.prompt_version,
                extraction_model=spec.model,
                status="succeeded",
                event_count=2,
            ),
            VideoEventExtractionRun(
                video_id=videos[1].id,
                transcript_asset_id=transcripts[1].id,
                source_hash="current-zero-events",
                prompt_version=spec.prompt_version,
                extraction_model=spec.model,
                status="succeeded",
                event_count=0,
            ),
            VideoEventExtractionRun(
                video_id=videos[2].id,
                transcript_asset_id=transcripts[2].id,
                source_hash="legacy-only",
                prompt_version="llm_event_v2:test",
                extraction_model=spec.model,
                status="succeeded",
                event_count=1,
            ),
            VideoEventExtractionRun(
                video_id=videos[3].id,
                transcript_asset_id=transcripts[3].id,
                source_hash="current-failed",
                prompt_version=spec.prompt_version,
                extraction_model=spec.model,
                status="failed",
                event_count=0,
                error_message="协议响应无效",
            ),
        ]
        self.session.add_all(
            [
                media,
                PlaylistMedia(playlist_id=self.playlist.id, media_id=media.id),
                *videos,
                *transcripts,
                *runs,
            ]
        )
        self.session.commit()

        payload = domain_observation(self.session, self.playlist.id)

        self.assertEqual(payload["coverage"]["source_processing"]["numerator"], 5)
        self.assertEqual(payload["coverage"]["source_processing"]["denominator"], 6)
        extraction = payload["coverage"]["event_extraction"]
        self.assertEqual(extraction["numerator"], 2)
        self.assertEqual(extraction["denominator"], 5)
        self.assertEqual(extraction["details"]["prompt_version"], spec.prompt_version)
        self.assertEqual(extraction["details"]["model"], spec.model)
        self.assertEqual(extraction["details"]["with_events"], 1)
        self.assertEqual(extraction["details"]["zero_events"], 1)
        self.assertEqual(extraction["details"]["legacy_spec_only"], 1)
        self.assertEqual(extraction["details"]["current_spec_failed"], 1)
        self.assertEqual(extraction["details"]["no_successful_result"], 1)
        self.assertEqual(extraction["details"]["missing_transcript"], 1)

    def test_disabled_domain_only_archives_until_a_shared_domain_enables_observation(self) -> None:
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="channel-1",
            url="https://example.test/channel-1",
        )
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="video-1",
            media_id=media.id,
            url="https://example.test/video-1",
        )
        shared_domain = Playlist(id=uuid.uuid4(), name="共享观测域", observation_enabled=True)
        self.playlist.observation_enabled = False
        self.session.add_all(
            [
                media,
                video,
                shared_domain,
                PlaylistMedia(playlist_id=self.playlist.id, media_id=media.id),
                PlaylistMedia(playlist_id=shared_domain.id, media_id=media.id),
            ]
        )
        self.session.commit()

        self.assertEqual(enabled_observation_video_ids(self.session, [video.id]), {video.id})

        shared_domain.observation_enabled = False
        self.session.commit()
        self.assertEqual(enabled_observation_video_ids(self.session, [video.id]), set())

    def test_reenabling_observation_enqueues_backfill_and_field_refresh(self) -> None:
        self.playlist.observation_enabled = False
        self.session.commit()
        backfill_id = uuid.uuid4()
        dirty_id = uuid.uuid4()

        with patch(
            "raelyn.services.domain_observation_control.enqueue_job",
            side_effect=[backfill_id, dirty_id],
        ) as enqueue:
            result = set_domain_observation_enabled(
                self.session,
                playlist_id=self.playlist.id,
                enabled=True,
            )

        self.assertTrue(result["observation_enabled"])
        self.assertEqual(result["backfill_job_id"], str(backfill_id))
        self.assertEqual(result["dirty_job_id"], str(dirty_id))
        self.assertEqual(
            [call.kwargs["type_"] for call in enqueue.call_args_list],
            ["playlist.backfill_events", "playlist.mark_event_map_dirty"],
        )

    def test_story_directory_returns_stable_pages_and_total(self) -> None:
        self.current.story_count = 3
        stable_ids = [
            uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        ]
        identities = [
            EventMapStoryIdentity(
                id=stable_ids[index],
                playlist_id=self.playlist.id,
                status="active",
                stable_title=f"故事 {index}",
                created_snapshot_id=self.current.id,
                last_material_snapshot_id=self.current.id,
                last_material_changed_at=self.now + timedelta(days=min(index, 1)),
            )
            for index in range(3)
        ]
        stories = [
            EventMapStory(
                snapshot_id=self.current.id,
                story_id=uuid.uuid4(),
                story_identity_id=identity.id,
                title=f"故事 {index}",
                summary=f"故事 {index} 摘要",
                event_time_start=self.now + timedelta(days=index),
                event_time_end=self.now + timedelta(days=index),
                canonical_count=index + 2,
            )
            for index, identity in enumerate(identities)
        ]
        self.session.add_all(
            [
                EventMapState(playlist_id=self.playlist.id, current_snapshot_id=self.current.id),
                *identities,
                *stories,
            ]
        )
        self.session.commit()

        first = story_directory(self.session, self.playlist.id, limit=2, offset=0)
        second = story_directory(self.session, self.playlist.id, limit=2, offset=2)

        self.assertEqual(first["total"], 3)
        self.assertEqual(first["limit"], 2)
        self.assertEqual(first["offset"], 0)
        self.assertTrue(first["has_more"])
        self.assertEqual([item["title"] for item in first["items"]], ["故事 2", "故事 1"])
        self.assertEqual([item["title"] for item in second["items"]], ["故事 0"])
        self.assertFalse(second["has_more"])

    def test_story_directory_attention_scopes_search_and_unread_are_material(self) -> None:
        story_specs = [
            ("货币政策路径", "议息会议", "加息落地", "established", self.now - timedelta(days=3)),
            ("供应链恢复", "工厂停产", "工厂恢复", "established", self.now + timedelta(minutes=20)),
            ("芯片线索", "市场传闻", "厂商回应", "emerging", self.now + timedelta(minutes=30)),
        ]
        identities: list[EventMapStoryIdentity] = []
        stories: list[EventMapStory] = []
        canonical_rows: list[EventMapCanonical] = []
        members: list[EventMapStoryMember] = []
        changes: list[EventMapChange] = []
        for story_index, (stable_title, old_title, latest_title, maturity, formed_at) in enumerate(story_specs):
            identity_id = uuid.uuid4()
            story_id = uuid.uuid4()
            old_id = uuid.uuid4()
            latest_id = uuid.uuid4()
            identities.append(
                EventMapStoryIdentity(
                    id=identity_id,
                    playlist_id=self.playlist.id,
                    stable_title=stable_title,
                    status="active",
                    created_snapshot_id=self.current.id,
                    last_material_snapshot_id=self.current.id,
                    last_material_changed_at=formed_at,
                    created_at=formed_at,
                )
            )
            stories.append(
                EventMapStory(
                    snapshot_id=self.current.id,
                    story_id=story_id,
                    story_identity_id=identity_id,
                    title=f"{old_title} → {latest_title}",
                    summary=f"{latest_title}成为最新进展。",
                    maturity=maturity,
                    event_time_start=self.now - timedelta(days=2),
                    event_time_end=self.now + timedelta(days=story_index),
                    canonical_count=2,
                )
            )
            canonical_rows.extend(
                [
                    self._canonical(
                        snapshot_id=self.current.id,
                        canonical_id=old_id,
                        point_index=story_index * 2,
                        title=old_title,
                        occurred_at=self.now - timedelta(days=2),
                    ),
                    self._canonical(
                        snapshot_id=self.current.id,
                        canonical_id=latest_id,
                        point_index=story_index * 2 + 1,
                        title=latest_title,
                        occurred_at=self.now + timedelta(days=story_index),
                    ),
                ]
            )
            members.extend(
                [
                    EventMapStoryMember(
                        snapshot_id=self.current.id,
                        story_id=story_id,
                        position=0,
                        canonical_id=old_id,
                    ),
                    EventMapStoryMember(
                        snapshot_id=self.current.id,
                        story_id=story_id,
                        position=1,
                        canonical_id=latest_id,
                    ),
                ]
            )
            changes.append(
                EventMapChange(
                    id=uuid.uuid4(),
                    playlist_id=self.playlist.id,
                    from_snapshot_id=None,
                    to_snapshot_id=self.current.id,
                    object_type="story",
                    object_id=identity_id,
                    change_type="story_added",
                    occurred_at=formed_at,
                    observed_at=formed_at,
                    after_revision={"title": stable_title},
                )
            )
        self.session.add_all(
            [
                EventMapState(playlist_id=self.playlist.id, current_snapshot_id=self.current.id),
                *identities,
                *stories,
                *canonical_rows,
                *members,
                *changes,
            ]
        )
        self.session.commit()

        first_visit = story_directory(
            self.session,
            self.playlist.id,
            scope="attention",
            limit=1,
        )
        self.assertTrue(first_visit["baseline_suggestion"])
        self.assertEqual(first_visit["total"], 1)
        self.assertFalse(first_visit["has_more"])
        self.assertFalse(first_visit["items"][0]["unread"])
        self.assertEqual(first_visit["items"][0]["stable_title"], "供应链恢复")
        next_baseline_page = story_directory(
            self.session,
            self.playlist.id,
            scope="attention",
            limit=1,
            offset=1,
        )
        self.assertEqual(next_baseline_page["items"], [])

        # 只保存位置或关注不等于已经读过；首访推荐仍保留，同时关注的初步线索
        # 也应进入“值得阅读”。
        self.session.add(
            StoryReadState(
                story_identity_id=identities[2].id,
                followed=True,
                last_position=1,
            )
        )
        self.session.commit()
        position_only = story_directory(
            self.session,
            self.playlist.id,
            scope="attention",
        )
        self.assertTrue(position_only["baseline_suggestion"])
        self.assertEqual(
            {item["stable_title"] for item in position_only["items"]},
            {"货币政策路径", "供应链恢复", "芯片线索"},
        )
        followed_suggestion = next(
            item for item in position_only["items"] if item["stable_title"] == "芯片线索"
        )
        self.assertTrue(followed_suggestion["unread"])
        self.assertFalse(followed_suggestion["recommended"])
        self.assertFalse(followed_suggestion["baseline_suggestion"])

        old_progress_search = story_directory(
            self.session,
            self.playlist.id,
            scope="all",
            query="议息会议",
        )
        latest_progress_search = story_directory(
            self.session,
            self.playlist.id,
            scope="all",
            query="加息落地",
        )
        stable_title_search = story_directory(
            self.session,
            self.playlist.id,
            scope="all",
            query="货币政策",
        )
        self.assertEqual(old_progress_search["items"], [])
        self.assertEqual(len(latest_progress_search["items"]), 1)
        self.assertEqual(len(stable_title_search["items"]), 1)

        self.session.add(
            DomainObservationCursor(
                playlist_id=self.playlist.id,
                observed_at=self.now,
                snapshot_id=self.current.id,
            )
        )
        identities[0].last_material_changed_at = self.now + timedelta(minutes=10)
        self.session.add(
            EventMapChange(
                id=uuid.uuid4(),
                playlist_id=self.playlist.id,
                from_snapshot_id=self.parent.id,
                to_snapshot_id=self.current.id,
                object_type="story",
                object_id=identities[0].id,
                change_type="story_members_changed",
                occurred_at=self.now + timedelta(minutes=10),
                observed_at=self.now + timedelta(minutes=10),
                before_revision={"member_ids": ["old"]},
                after_revision={"member_ids": ["old", "new"]},
            )
        )
        self.session.commit()
        attention = story_directory(self.session, self.playlist.id, scope="attention")
        self.assertFalse(attention["baseline_suggestion"])
        self.assertEqual(
            {item["stable_title"] for item in attention["items"]},
            {"货币政策路径", "供应链恢复", "芯片线索"},
        )
        self.assertTrue(all(item["unread"] for item in attention["items"]))
        self.assertEqual(
            [item["stable_title"] for item in story_directory(self.session, self.playlist.id, scope="followed")["items"]],
            ["芯片线索"],
        )
        self.assertEqual(
            {item["stable_title"] for item in story_directory(self.session, self.playlist.id, scope="established")["items"]},
            {"货币政策路径", "供应链恢复"},
        )
        self.assertEqual(
            [item["stable_title"] for item in story_directory(self.session, self.playlist.id, scope="emerging")["items"]],
            ["芯片线索"],
        )

        followed_state = self.session.get(StoryReadState, identities[2].id)
        followed_state.last_read_at = self.now + timedelta(hours=1)
        followed_state.last_read_snapshot_id = self.current.id
        self.session.commit()
        after_read = story_directory(self.session, self.playlist.id, scope="attention")
        self.assertEqual(
            {item["stable_title"] for item in after_read["items"]},
            {"货币政策路径", "供应链恢复"},
        )
        identities[2].last_material_changed_at = self.now + timedelta(hours=2)
        self.session.commit()
        after_material_update = story_directory(self.session, self.playlist.id, scope="attention")
        self.assertEqual(
            {item["stable_title"] for item in after_material_update["items"]},
            {"货币政策路径", "供应链恢复", "芯片线索"},
        )

    def test_story_revision_delta_ignores_wording_and_scores_but_tracks_order_and_edge_support(self) -> None:
        identity_id = uuid.uuid4()
        member_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        support_ids = [uuid.uuid4(), uuid.uuid4()]
        edge_ids = [uuid.uuid4(), uuid.uuid4()]

        def edge(index: int, evidence_id: uuid.UUID, score: float) -> dict[str, object]:
            return {
                "edge_id": str(edge_ids[index]),
                "source_canonical_id": str(member_ids[index]),
                "target_canonical_id": str(member_ids[2]),
                "relation_type": "continuation",
                "direction": "forward",
                "status": "automatic",
                "score": score,
                "evidence_revision_ids": [str(evidence_id)],
                "evidence": {
                    "cosine": score,
                    "claim_overlap": score / 2,
                    "supporting_revision_ids": [str(evidence_id)],
                    "gap_days": index + 1,
                },
            }

        previous = EventMapStoryHistoryRevision(
            id=uuid.uuid4(),
            playlist_id=self.playlist.id,
            story_identity_id=identity_id,
            snapshot_id=self.parent.id,
            story_id=uuid.uuid4(),
            title="旧措辞",
            summary="旧摘要",
            quality_score=0.7,
            member_ids=[str(value) for value in member_ids],
            edges=[edge(0, support_ids[0], 0.8), edge(1, support_ids[1], 0.81)],
            evidence_revision_ids=[str(value) for value in support_ids],
            method_version="story-v2",
            observed_at=self.parent.finished_at,
        )
        score_only = EventMapStoryHistoryRevision(
            id=uuid.uuid4(),
            playlist_id=self.playlist.id,
            story_identity_id=identity_id,
            snapshot_id=self.current.id,
            story_id=uuid.uuid4(),
            title="新措辞",
            summary="新摘要",
            quality_score=0.95,
            member_ids=[str(value) for value in member_ids],
            edges=[edge(0, support_ids[0], 0.91), edge(1, support_ids[1], 0.92)],
            evidence_revision_ids=[str(value) for value in support_ids],
            method_version="story-v2",
            observed_at=self.current.finished_at,
        )
        score_delta = _story_revision_delta(previous, score_only)
        self.assertTrue(score_delta["title_changed"])
        self.assertTrue(score_delta["summary_changed"])
        self.assertTrue(score_delta["quality_score_changed"])
        self.assertFalse(score_delta["relation_details_changed"])
        self.assertFalse(_story_delta_is_material(score_delta))

        support_reassigned = EventMapStoryHistoryRevision(
            id=uuid.uuid4(),
            playlist_id=self.playlist.id,
            story_identity_id=identity_id,
            snapshot_id=uuid.uuid4(),
            story_id=uuid.uuid4(),
            title=score_only.title,
            summary=score_only.summary,
            quality_score=score_only.quality_score,
            member_ids=[str(value) for value in member_ids],
            edges=[edge(0, support_ids[1], 0.91), edge(1, support_ids[0], 0.92)],
            evidence_revision_ids=[str(value) for value in support_ids],
            method_version="story-v2",
            observed_at=self.current.finished_at + timedelta(minutes=1),
        )
        support_delta = _story_revision_delta(score_only, support_reassigned)
        self.assertTrue(support_delta["support_assignments_changed"])
        self.assertTrue(_story_delta_is_material(support_delta))

        reordered = EventMapStoryHistoryRevision(
            id=uuid.uuid4(),
            playlist_id=self.playlist.id,
            story_identity_id=identity_id,
            snapshot_id=uuid.uuid4(),
            story_id=uuid.uuid4(),
            title=support_reassigned.title,
            member_ids=[str(value) for value in reversed(member_ids)],
            edges=support_reassigned.edges,
            evidence_revision_ids=[str(value) for value in support_ids],
            method_version="story-v2",
            observed_at=self.current.finished_at + timedelta(minutes=2),
        )
        order_delta = _story_revision_delta(support_reassigned, reordered)
        self.assertTrue(order_delta["member_order_changed"])
        self.assertTrue(_story_delta_is_material(order_delta))

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
                stable_title="利率路径",
                created_snapshot_id=self.parent.id,
                last_material_snapshot_id=self.parent.id,
                last_material_changed_at=self.parent.finished_at,
            )
        )
        self.session.add(
            EventMapStory(
                snapshot_id=self.parent.id,
                story_id=parent_story_id,
                story_identity_id=stable_story_id,
                title="利率路径",
                anchor_key="institution:美联储",
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

        stories = [
            SimpleNamespace(
                story_index=0,
                member_group_indices=[0, 1, 2, 3],
                anchor_key="institution:美联储",
            )
        ]
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
        opened = story_history(self.session, self.playlist.id, stable_story_id)
        self.assertFalse(opened["followed"])
        self.assertFalse(opened["unread"])
        self.assertEqual(self.session.execute(select(func.count()).select_from(StoryReadState)).scalar_one(), 0)
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
        self.assertFalse(payload["unread"])
        self.assertEqual(payload["last_read_snapshot_id"], str(self.current.id))
        self.assertTrue(payload["revisions"][0]["delta"]["baseline"])
        self.assertEqual(payload["revisions"][1]["member_ids"], [str(value) for value in member_ids])
        self.assertEqual(payload["revisions"][1]["delta"]["members_added"], [str(member_ids[2])])
        self.assertEqual(payload["revisions"][1]["delta"]["evidence_added"], [str(new_evidence_id)])
        self.assertTrue(payload["revisions"][1]["delta"]["summary_changed"])
        self.assertTrue(payload["revisions"][1]["delta"]["time_range_changed"])
        self.assertFalse(payload["revisions"][1]["delta"]["title_changed"])
        self.assertEqual(payload["current_trajectory"]["snapshot_id"], str(self.current.id))
        historical = story_history(
            self.session,
            self.playlist.id,
            stable_story_id,
            snapshot_id=self.parent.id,
        )
        self.assertEqual(historical["current_trajectory"]["snapshot_id"], str(self.parent.id))
        with self.assertRaisesRegex(ValueError, "historical story revision"):
            update_story_read_state(
                self.session,
                playlist_id=self.playlist.id,
                identity_id=stable_story_id,
                followed=None,
                snapshot_id=self.parent.id,
                position=None,
                mark_read=True,
            )
        with self.assertRaisesRegex(LookupError, "story not found in domain snapshot"):
            story_history(
                self.session,
                self.playlist.id,
                stable_story_id,
                snapshot_id=uuid.uuid4(),
            )
        self.assertEqual(self.session.execute(select(func.count()).select_from(StoryReadState)).scalar_one(), 1)

    def test_story_detail_keeps_selected_revision_and_humanizes_branches_and_removed_events(self) -> None:
        middle = self._snapshot(
            parent_id=self.parent.id,
            finished_at=self.now + timedelta(minutes=30),
        )
        identity_id = uuid.uuid4()
        story_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
        canonical_ids = [uuid.uuid4() for _ in range(4)]
        event_titles = ["政策会议", "加息决定", "通胀预测", "市场重新定价"]
        event_times = [
            self.now - timedelta(days=10),
            self.now - timedelta(days=5),
            self.now - timedelta(days=2),
            self.now,
        ]

        def raw_edge(
            edge_id: uuid.UUID,
            source_index: int,
            target_index: int,
            relation_type: str,
        ) -> dict[str, object]:
            return {
                "edge_id": str(edge_id),
                "source_canonical_id": str(canonical_ids[source_index]),
                "target_canonical_id": str(canonical_ids[target_index]),
                "relation_type": relation_type,
                "status": "automatic",
                "evidence_revision_ids": [],
                "evidence": {
                    "gap_days": (event_times[target_index] - event_times[source_index]).days,
                    "claim_bridge": {
                        "source_claim": f"{event_titles[source_index]}命题",
                        "target_claim": f"{event_titles[target_index]}命题",
                    },
                    "source_excerpts": [f"{event_titles[source_index]}摘录"],
                    "target_excerpts": [f"{event_titles[target_index]}摘录"],
                },
            }

        edge_ab = raw_edge(uuid.uuid4(), 0, 1, "continuation")
        edge_ac = raw_edge(uuid.uuid4(), 0, 2, "causes")
        edge_cd = raw_edge(uuid.uuid4(), 2, 3, "response")
        edge_ad = raw_edge(uuid.uuid4(), 0, 3, "corrects")
        correction_evidence_id = uuid.uuid4()
        edge_ad["evidence_revision_ids"] = [str(correction_evidence_id)]
        revisions = [
            EventMapStoryHistoryRevision(
                id=uuid.uuid4(),
                playlist_id=self.playlist.id,
                story_identity_id=identity_id,
                snapshot_id=self.parent.id,
                story_id=story_ids[0],
                title="政策会议 → 通胀预测",
                summary="最初识别。",
                maturity="emerging",
                quality_score=0.7,
                event_time_start=event_times[0],
                event_time_end=event_times[2],
                member_ids=[str(value) for value in canonical_ids[:3]],
                edges=[edge_ab, edge_ac],
                evidence_revision_ids=[],
                method_version="story-v2",
                observed_at=self.parent.finished_at,
            ),
            EventMapStoryHistoryRevision(
                id=uuid.uuid4(),
                playlist_id=self.playlist.id,
                story_identity_id=identity_id,
                snapshot_id=middle.id,
                story_id=story_ids[1],
                title="政策会议后出现通胀预测",
                summary="仅改写措辞并重算质量分。",
                maturity="emerging",
                quality_score=0.91,
                event_time_start=event_times[0],
                event_time_end=event_times[2],
                member_ids=[str(value) for value in canonical_ids[:3]],
                edges=[edge_ab, edge_ac],
                evidence_revision_ids=[],
                method_version="story-v2",
                observed_at=middle.finished_at,
            ),
            EventMapStoryHistoryRevision(
                id=uuid.uuid4(),
                playlist_id=self.playlist.id,
                story_identity_id=identity_id,
                snapshot_id=self.current.id,
                story_id=story_ids[2],
                title="政策会议 → 市场重新定价",
                summary="新事件形成分支并纠正旧判断。",
                maturity="established",
                quality_score=0.88,
                event_time_start=event_times[0],
                event_time_end=event_times[3],
                member_ids=[str(canonical_ids[index]) for index in (0, 2, 3)],
                edges=[edge_ac, edge_cd, edge_ad],
                evidence_revision_ids=[str(correction_evidence_id)],
                method_version="story-v2",
                observed_at=self.current.finished_at,
            ),
        ]
        archived_rows = []
        for snapshot, observed_at in (
            (self.parent, self.parent.finished_at),
            (middle, middle.finished_at),
        ):
            for point_index in range(3):
                archived_rows.append(
                    EventMapCanonicalHistoryRevision(
                        id=uuid.uuid4(),
                        playlist_id=self.playlist.id,
                        canonical_id=canonical_ids[point_index],
                        snapshot_id=snapshot.id,
                        revision={
                            "canonical_id": str(canonical_ids[point_index]),
                            "title": event_titles[point_index],
                            "summary": f"{event_titles[point_index]}摘要",
                            "event_type": "policy",
                            "event_time_start": event_times[point_index].isoformat(),
                            "event_time_end": event_times[point_index].isoformat(),
                            "time_precision": "day",
                            "member_count": 1,
                            "point_index": point_index,
                            "uncertainty_flags": ["time"] if point_index == 1 else [],
                        },
                        occurred_at=event_times[point_index],
                        observed_at=observed_at,
                    )
                )
        self.session.add_all(
            [
                middle,
                EventMapState(playlist_id=self.playlist.id, current_snapshot_id=self.current.id),
                EventMapStoryIdentity(
                    id=identity_id,
                    playlist_id=self.playlist.id,
                    stable_title="美联储利率路径",
                    created_snapshot_id=self.parent.id,
                    last_material_snapshot_id=self.current.id,
                    last_material_changed_at=self.current.finished_at,
                ),
                *[
                    EventMapCanonicalIdentity(
                        id=canonical_id,
                        playlist_id=self.playlist.id,
                        created_snapshot_id=self.parent.id,
                    )
                    for canonical_id in canonical_ids
                ],
                *[
                    self._canonical(
                        snapshot_id=self.current.id,
                        canonical_id=canonical_ids[point_index],
                        point_index=point_index,
                        title=event_titles[point_index],
                        occurred_at=event_times[point_index],
                    )
                    for point_index in (0, 2, 3)
                ],
                *archived_rows,
                *revisions,
                StoryReadState(
                    story_identity_id=identity_id,
                    last_read_snapshot_id=middle.id,
                    last_read_at=middle.finished_at,
                    last_position=1,
                ),
            ]
        )
        self.session.commit()

        current_payload = story_history(self.session, self.playlist.id, identity_id)
        self.assertEqual(current_payload["stable_title"], "美联储利率路径")
        self.assertEqual(current_payload["selected_revision"]["title"], "政策会议 → 市场重新定价")
        self.assertEqual(
            [item["title"] for item in current_payload["trajectory"]["nodes"]],
            ["政策会议", "通胀预测", "市场重新定价"],
        )
        self.assertEqual(current_payload["relation_count"], 3)
        incoming_to_latest = [
            edge
            for edge in current_payload["trajectory"]["edges"]
            if edge["target_title"] == "市场重新定价"
        ]
        self.assertEqual({edge["source_title"] for edge in incoming_to_latest}, {"政策会议", "通胀预测"})
        self.assertEqual(
            {edge["relation_label"] for edge in incoming_to_latest},
            {"事实纠正", "事件响应"},
        )
        self.assertTrue(current_payload["reading_update"]["has_material_changes"])
        self.assertTrue(current_payload["unread"])
        self.assertEqual(current_payload["reading_update"]["events_removed"][0]["title"], "加息决定")
        self.assertEqual(current_payload["reading_update"]["events_added"][0]["title"], "市场重新定价")
        self.assertIn("story_correction_added", current_payload["reading_update"]["change_types"])
        self.assertEqual(
            current_payload["reading_update"]["maturity_change"],
            {"before": "emerging", "after": "established"},
        )
        self.assertEqual(len(current_payload["material_history"]), 2)
        self.assertEqual(current_payload["material_history"][0]["change_types"], ["story_added"])
        self.assertEqual(current_payload["audit_summary"]["unchanged_review_count"], 1)

        historical = story_history(
            self.session,
            self.playlist.id,
            identity_id,
            snapshot_id=self.parent.id,
        )
        self.assertTrue(historical["is_historical"])
        self.assertFalse(historical["can_mark_read"])
        self.assertFalse(historical["unread"])
        self.assertEqual(historical["selected_revision"]["title"], "政策会议 → 通胀预测")
        self.assertEqual(
            [item["title"] for item in historical["trajectory"]["nodes"]],
            ["政策会议", "加息决定", "通胀预测"],
        )
        self.assertEqual(len(historical["trajectory"]["edges"]), 2)
        self.assertFalse(historical["reading_update"]["has_material_changes"])
        self.assertEqual(len(historical["material_history"]), 1)
        self.assertEqual(historical["audit_summary"]["recognition_count"], 1)
        self.assertEqual(historical["last_material_snapshot_id"], str(self.parent.id))

    def test_story_detail_explains_restored_material_changes_and_keeps_tied_member_order(self) -> None:
        middle = self._snapshot(
            parent_id=self.parent.id,
            finished_at=self.now + timedelta(minutes=20),
        )
        self.current.parent_snapshot_id = middle.id
        final = self._snapshot(
            parent_id=self.current.id,
            finished_at=self.now + timedelta(hours=2),
        )
        identity_id = uuid.uuid4()
        canonical_ids = [uuid.uuid4() for _ in range(3)]
        titles = ["同日事件甲", "同日事件乙", "短暂加入的事件"]
        occurred_at = self.now - timedelta(days=1)

        revisions = [
            EventMapStoryHistoryRevision(
                id=uuid.uuid4(),
                playlist_id=self.playlist.id,
                story_identity_id=identity_id,
                snapshot_id=self.parent.id,
                story_id=uuid.uuid4(),
                title="同日事件甲与乙",
                maturity="emerging",
                member_ids=[str(canonical_ids[0]), str(canonical_ids[1])],
                edges=[],
                evidence_revision_ids=[],
                method_version="story-v2",
                observed_at=self.parent.finished_at,
            ),
            EventMapStoryHistoryRevision(
                id=uuid.uuid4(),
                playlist_id=self.playlist.id,
                story_identity_id=identity_id,
                snapshot_id=middle.id,
                story_id=uuid.uuid4(),
                title="同日事件加入一条短暂线索",
                maturity="established",
                member_ids=[str(value) for value in canonical_ids],
                edges=[],
                evidence_revision_ids=[],
                method_version="story-v2",
                observed_at=middle.finished_at,
            ),
            EventMapStoryHistoryRevision(
                id=uuid.uuid4(),
                playlist_id=self.playlist.id,
                story_identity_id=identity_id,
                snapshot_id=self.current.id,
                story_id=uuid.uuid4(),
                title="同日事件甲与乙",
                maturity="emerging",
                member_ids=[str(canonical_ids[0]), str(canonical_ids[1])],
                edges=[],
                evidence_revision_ids=[],
                method_version="story-v2",
                observed_at=self.current.finished_at,
            ),
        ]
        canonical_rows = [
            self._canonical(
                snapshot_id=snapshot.id,
                canonical_id=canonical_id,
                point_index=point_index,
                title=titles[point_index],
                occurred_at=occurred_at,
            )
            for snapshot in (self.parent, middle, self.current)
            for point_index, canonical_id in enumerate(canonical_ids)
        ]
        self.session.add_all(
            [
                middle,
                final,
                EventMapState(playlist_id=self.playlist.id, current_snapshot_id=self.current.id),
                EventMapStoryIdentity(
                    id=identity_id,
                    playlist_id=self.playlist.id,
                    stable_title="同日事件的稳定故事",
                    created_snapshot_id=self.parent.id,
                    last_material_snapshot_id=self.current.id,
                    last_material_changed_at=self.current.finished_at,
                ),
                *[
                    EventMapCanonicalIdentity(
                        id=canonical_id,
                        playlist_id=self.playlist.id,
                        created_snapshot_id=self.parent.id,
                    )
                    for canonical_id in canonical_ids
                ],
                *canonical_rows,
                *revisions,
                StoryReadState(
                    story_identity_id=identity_id,
                    last_read_snapshot_id=self.parent.id,
                    last_read_at=self.parent.finished_at,
                ),
            ]
        )
        self.session.commit()

        restored = story_history(self.session, self.playlist.id, identity_id)
        self.assertTrue(restored["unread"])
        self.assertTrue(restored["reading_update"]["has_material_changes"])
        self.assertTrue(restored["reading_update"]["returned_to_previous_state"])
        self.assertEqual(restored["reading_update"]["intervening_material_change_count"], 2)
        self.assertEqual(restored["reading_update"]["events_added"], [])
        self.assertEqual(restored["reading_update"]["events_removed"], [])
        self.assertIn("story_members_changed", restored["reading_update"]["change_types"])
        self.assertIn("story_maturity_changed", restored["reading_update"]["change_types"])
        # 当前快照中第三个 canonical 仍然存在，但它已经不属于这条故事，不能混进航迹。
        self.assertEqual(
            [item["title"] for item in restored["trajectory"]["nodes"]],
            ["同日事件甲", "同日事件乙"],
        )

        final_revision = EventMapStoryHistoryRevision(
            id=uuid.uuid4(),
            playlist_id=self.playlist.id,
            story_identity_id=identity_id,
            snapshot_id=final.id,
            story_id=uuid.uuid4(),
            title="同日事件顺序调整",
            maturity="emerging",
            member_ids=[str(canonical_ids[1]), str(canonical_ids[0])],
            edges=[],
            evidence_revision_ids=[],
            method_version="story-v2",
            observed_at=final.finished_at,
        )
        self.session.add(final_revision)
        self.session.add_all(
            [
                self._canonical(
                    snapshot_id=final.id,
                    canonical_id=canonical_id,
                    point_index=point_index,
                    title=titles[point_index],
                    occurred_at=occurred_at,
                )
                for point_index, canonical_id in enumerate(canonical_ids)
            ]
        )
        state = self.session.get(EventMapState, self.playlist.id)
        state.current_snapshot_id = final.id
        identity = self.session.get(EventMapStoryIdentity, identity_id)
        identity.last_material_snapshot_id = final.id
        identity.last_material_changed_at = final.finished_at
        self.session.commit()

        reordered = story_history(self.session, self.playlist.id, identity_id)
        self.assertFalse(reordered["reading_update"]["returned_to_previous_state"])
        self.assertTrue(reordered["reading_update"]["member_order_changed"])
        self.assertEqual(
            [item["title"] for item in reordered["trajectory"]["nodes"]],
            ["同日事件乙", "同日事件甲"],
        )

    def test_story_identity_is_not_reused_across_algorithm_versions(self) -> None:
        stable_story_id = uuid.uuid4()
        parent_story_id = uuid.uuid4()
        member_ids = [uuid.uuid4(), uuid.uuid4()]
        self.parent.story_algorithm_version = "event_map_story_continuation_v1"
        self.session.add_all(
            [
                EventMapStoryIdentity(
                    id=stable_story_id,
                    playlist_id=self.playlist.id,
                    status="active",
                    stable_title="旧算法故事",
                    created_snapshot_id=self.parent.id,
                    last_material_snapshot_id=self.parent.id,
                    last_material_changed_at=self.parent.finished_at,
                ),
                EventMapStory(
                    snapshot_id=self.parent.id,
                    story_id=parent_story_id,
                    story_identity_id=stable_story_id,
                    title="旧算法故事",
                    anchor_key="institution:美联储",
                ),
            ]
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

        identities, states = _assign_story_identities(
            self.session,
            playlist_id=self.playlist.id,
            snapshot_id=self.current.id,
            parent_snapshot_id=self.parent.id,
            stories=[
                SimpleNamespace(
                    story_index=0,
                    member_group_indices=[0, 1],
                    anchor_key="institution:美联储",
                )
            ],
            canonical_ids=member_ids,
        )

        self.assertNotEqual(identities, [stable_story_id])
        self.assertEqual(states, ["new"])

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
                    stable_title="政策路径",
                    created_snapshot_id=self.parent.id,
                    last_material_snapshot_id=self.parent.id,
                    last_material_changed_at=self.parent.finished_at,
                ),
                EventMapStoryIdentity(
                    id=current_story_id,
                    playlist_id=self.playlist.id,
                    stable_title="政策路径更新",
                    created_snapshot_id=self.current.id,
                    last_material_snapshot_id=self.current.id,
                    last_material_changed_at=self.current.finished_at,
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

    def test_story_edge_support_is_snapshot_scoped_named_and_domain_authorized(self) -> None:
        media = Media(
            id=uuid.uuid4(),
            provider="youtube",
            provider_media_id="story-support-channel",
            url="https://example.test/channel/support",
            name="政策观察台",
        )
        video = Video(
            id=uuid.uuid4(),
            provider="youtube",
            provider_video_id="story-support-video",
            media_id=media.id,
            url="https://example.test/video/support",
            title="政策发布会原始视频",
        )
        support_revision = EventMapRecordRevision(
            id=uuid.uuid4(),
            source_video_id=video.id,
            content_hash="story-support-content",
            embedding_checksum="story-support-embedding",
            title="官员宣布政策调整",
            summary="官员确认政策调整并解释原因。",
            event_time_start=self.now,
            event_time_end=self.now,
            time_precision="day",
            event_type="policy",
            evidence_json=[
                {
                    "evidence_text": "政策将从今日起调整。",
                    "source_kind": "description",
                    "source_label": "视频简介",
                    "verified": True,
                }
            ],
        )
        identity_id = uuid.uuid4()
        story_id = uuid.uuid4()
        source_id = uuid.uuid4()
        target_id = uuid.uuid4()
        edge_id = uuid.uuid4()
        history_id = uuid.uuid4()
        raw_edge = {
            "edge_id": str(edge_id),
            "source_canonical_id": str(source_id),
            "target_canonical_id": str(target_id),
            "relation_type": "causes",
            "status": "automatic",
            "evidence_revision_ids": [str(support_revision.id)],
            "evidence": {
                "gap_days": 2,
                "claim_bridge": {
                    "source_claim": "政策已经发布",
                    "target_claim": "市场随后调整",
                },
                "source_excerpts": ["政策从今日起调整"],
                "target_excerpts": ["市场价格重新定价"],
            },
        }
        self.session.add_all(
            [
                media,
                video,
                PlaylistMedia(playlist_id=self.playlist.id, media_id=media.id),
                support_revision,
                EventMapState(playlist_id=self.playlist.id, current_snapshot_id=self.current.id),
                EventMapStoryIdentity(
                    id=identity_id,
                    playlist_id=self.playlist.id,
                    stable_title="政策发布后的市场响应",
                    created_snapshot_id=self.current.id,
                    last_material_snapshot_id=self.current.id,
                    last_material_changed_at=self.current.finished_at,
                ),
                EventMapCanonicalIdentity(
                    id=source_id,
                    playlist_id=self.playlist.id,
                    created_snapshot_id=self.current.id,
                ),
                EventMapCanonicalIdentity(
                    id=target_id,
                    playlist_id=self.playlist.id,
                    created_snapshot_id=self.current.id,
                ),
                self._canonical(
                    snapshot_id=self.current.id,
                    canonical_id=source_id,
                    point_index=0,
                    title="政策正式发布",
                    occurred_at=self.now - timedelta(days=2),
                ),
                self._canonical(
                    snapshot_id=self.current.id,
                    canonical_id=target_id,
                    point_index=1,
                    title="市场重新定价",
                    occurred_at=self.now,
                ),
                EventMapStoryHistoryRevision(
                    id=history_id,
                    playlist_id=self.playlist.id,
                    story_identity_id=identity_id,
                    snapshot_id=self.current.id,
                    story_id=story_id,
                    title="政策正式发布 → 市场重新定价",
                    member_ids=[str(source_id), str(target_id)],
                    edges=[raw_edge],
                    evidence_revision_ids=[str(support_revision.id)],
                    method_version="story-v2",
                    observed_at=self.current.finished_at,
                ),
                EventMapStoryHistoryEvidence(
                    history_revision_id=history_id,
                    edge_id=edge_id,
                    record_revision_id=support_revision.id,
                    playlist_id=self.playlist.id,
                    story_identity_id=identity_id,
                    snapshot_id=self.current.id,
                    source_canonical_id=source_id,
                    target_canonical_id=target_id,
                    relation_type="causes",
                ),
            ]
        )
        self.session.commit()

        payload = story_edge_support(
            self.session,
            self.playlist.id,
            identity_id,
            edge_id,
            snapshot_id=self.current.id,
        )
        self.assertEqual(payload["support_granularity"], "record_revision")
        self.assertEqual(payload["edge"]["source_title"], "政策正式发布")
        self.assertEqual(payload["edge"]["target_title"], "市场重新定价")
        self.assertEqual(payload["edge"]["relation_label"], "因果承接")
        self.assertEqual(payload["edge"]["explanation"]["source_claim"], "政策已经发布")
        self.assertEqual(len(payload["records"]), 1)
        self.assertEqual(payload["records"][0]["media_name"], "政策观察台")
        self.assertEqual(payload["records"][0]["excerpt"], "政策将从今日起调整。")
        self.assertTrue(payload["records"][0]["verified"])
        self.assertEqual(payload["records"][0]["source_version_status"], "not_applicable")
        with self.assertRaisesRegex(ValueError, "snapshot_id is required"):
            story_edge_support(
                self.session,
                self.playlist.id,
                identity_id,
                edge_id,
            )
        with self.assertRaisesRegex(LookupError, "domain snapshot"):
            story_edge_support(
                self.session,
                self.playlist.id,
                identity_id,
                edge_id,
                snapshot_id=self.parent.id,
            )
        with self.assertRaisesRegex(LookupError, "relation not found"):
            story_edge_support(
                self.session,
                self.playlist.id,
                identity_id,
                uuid.uuid4(),
                snapshot_id=self.current.id,
            )
        other_playlist = Playlist(id=uuid.uuid4(), name="无权观测域")
        self.session.add(other_playlist)
        self.session.commit()
        with self.assertRaisesRegex(LookupError, "story not found"):
            story_edge_support(
                self.session,
                other_playlist.id,
                identity_id,
                edge_id,
                snapshot_id=self.current.id,
            )
        association = self.session.get(PlaylistMedia, (self.playlist.id, media.id))
        self.session.delete(association)
        self.session.commit()
        with self.assertRaisesRegex(LookupError, "does not belong to domain"):
            story_edge_support(
                self.session,
                self.playlist.id,
                identity_id,
                edge_id,
                snapshot_id=self.current.id,
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
                    stable_title="政策路径",
                    created_snapshot_id=self.current.id,
                    last_material_snapshot_id=self.current.id,
                    last_material_changed_at=self.current.finished_at,
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
        story_reference = next(row for row in references if row.object_type == "story")
        self.assertEqual(story_reference.snapshot_id, self.current.id)
        self.assertEqual(story_reference.context["source_canonical_id"], str(canonical_id))
        self.assertEqual(story_reference.context["target_canonical_id"], str(target_id))
        self.assertEqual(story_reference.context["focus_canonical_id"], str(target_id))
        self.assertIn(f"/stories?domain_id={self.playlist.id}", story_reference.context["web_url"])
        self.assertIn(f"focus_canonical_id={target_id}", story_reference.context["web_url"])
        self.assertNotIn("snapshot_id=", story_reference.context["web_url"])
        story_reference.context = {
            **story_reference.context,
            "web_url": (
                f"/field?domain_id={self.playlist.id}&mode=story&story_id={story_identity_id}"
                f"&snapshot_id={self.current.id}"
            ),
        }
        story_payload = brief_reference_payload(
            story_reference,
            self.playlist.id,
            brief=brief,
        )
        self.assertIn(f"/stories?domain_id={self.playlist.id}", story_payload["web_url"])
        self.assertIn(f"focus_canonical_id={target_id}", story_payload["web_url"])
        self.assertNotIn("snapshot_id=", story_payload["web_url"])
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
