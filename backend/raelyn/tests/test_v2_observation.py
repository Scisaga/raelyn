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
    EventMapCanonicalIdentity,
    EventMapEntityIndex,
    EventMapRecordRevision,
    EventMapSnapshot,
    EventMapState,
    EventMapStory,
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
)
from raelyn.services.event_map_domain import EVENT_MAP_STORY_VERSION
from raelyn.services.event_analysis import event_extraction_spec
from raelyn.services.v2_observation import (
    brief_reference_payload,
    canonical_directory,
    domain_bootstrap_directory,
    domain_directory,
    domain_observation,
    event_highlights,
    object_brief_references,
    story_directory,
    story_edge_support,
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




if __name__ == "__main__":
    unittest.main()
