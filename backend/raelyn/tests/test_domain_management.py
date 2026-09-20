from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
import uuid

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.models import Asset, Base, Job, Media, Playlist, PlaylistMedia, Video
from raelyn.services.domain_management import (
    attach_domain_source,
    delete_domain,
    detach_domain_source,
    domain_deletion_impact,
)
from raelyn.services.media_sources import resolve_media_url


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs) -> str:
    return "JSON"


@compiles(ARRAY, "sqlite")
def _compile_array_for_sqlite(_type, _compiler, **_kwargs) -> str:
    return "JSON"


class DomainManagementIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.tempdir.name) / 'domain.sqlite'}")

        @event.listens_for(self.engine, "connect")
        def _configure_sqlite(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
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
        self.domain = Playlist(id=uuid.uuid4(), name="宏观观测域")
        self.media = Media(
            id=uuid.uuid4(),
            provider="bilibili",
            provider_media_id="10001",
            url="https://space.bilibili.com/10001",
        )
        self.session.add_all([self.domain, self.media])
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()
        self.tempdir.cleanup()

    def test_existing_source_attachment_is_idempotent(self) -> None:
        first = attach_domain_source(
            self.session,
            domain_id=self.domain.id,
            media_id=self.media.id,
        )
        self.session.commit()
        second = attach_domain_source(
            self.session,
            domain_id=self.domain.id,
            media_id=self.media.id,
        )
        self.session.commit()

        count = self.session.execute(select(func.count()).select_from(PlaylistMedia)).scalar_one()
        self.assertTrue(first.attached)
        self.assertFalse(second.attached)
        self.assertEqual(count, 1)

    def test_url_resolution_and_attachment_share_one_transaction(self) -> None:
        resolution = resolve_media_url(
            self.session,
            url="https://www.youtube.com/@domain-test",
        )
        result = attach_domain_source(
            self.session,
            domain_id=self.domain.id,
            media_id=resolution.media.id,
        )
        self.session.commit()

        relation = self.session.get(
            PlaylistMedia,
            {"playlist_id": self.domain.id, "media_id": resolution.media.id},
        )
        self.assertTrue(resolution.created)
        self.assertTrue(result.attached)
        self.assertIsNotNone(relation)

    def test_transaction_failure_rolls_back_media_and_attachment(self) -> None:
        try:
            resolution = resolve_media_url(
                self.session,
                url="https://www.youtube.com/@rollback-test",
            )
            attach_domain_source(
                self.session,
                domain_id=self.domain.id,
                media_id=resolution.media.id,
            )
            raise RuntimeError("simulate request failure")
        except RuntimeError:
            self.session.rollback()

        media = self.session.execute(
            select(Media).where(Media.provider_media_id == "@rollback-test")
        ).scalar_one_or_none()
        relation_count = self.session.execute(
            select(func.count()).select_from(PlaylistMedia)
        ).scalar_one()
        self.assertIsNone(media)
        self.assertEqual(relation_count, 0)

    def test_detach_keeps_shared_media_video_and_asset(self) -> None:
        video = Video(
            id=uuid.uuid4(),
            provider="bilibili",
            provider_video_id="BV1domain",
            media_id=self.media.id,
            url="https://www.bilibili.com/video/BV1domain",
        )
        asset = Asset(
            id=uuid.uuid4(),
            video_id=video.id,
            type="video",
            format="mp4",
            source="download",
            s3_bucket="test",
            s3_key="domain/test.mp4",
        )
        self.session.add_all([video, asset])
        attach_domain_source(self.session, domain_id=self.domain.id, media_id=self.media.id)
        self.session.commit()

        detached = detach_domain_source(
            self.session,
            domain_id=self.domain.id,
            media_id=self.media.id,
        )
        self.session.commit()

        self.assertTrue(detached)
        self.assertIsNone(
            self.session.get(
                PlaylistMedia,
                {"playlist_id": self.domain.id, "media_id": self.media.id},
            )
        )
        self.assertIsNotNone(self.session.get(Media, self.media.id))
        self.assertIsNotNone(self.session.get(Video, video.id))
        self.assertIsNotNone(self.session.get(Asset, asset.id))

    def test_deletion_impact_and_safe_delete_keep_shared_data(self) -> None:
        second_domain = Playlist(id=uuid.uuid4(), name="删除测试域")
        self.session.add(second_domain)
        self.session.add(PlaylistMedia(playlist_id=second_domain.id, media_id=self.media.id))
        self.session.commit()

        impact = domain_deletion_impact(self.session, second_domain.id)
        self.assertEqual(impact["counts"]["source_links"], 1)
        self.assertEqual(impact["retained_shared_data"]["sources"], 1)

        with self.assertRaises(ValueError):
            delete_domain(self.session, domain_id=second_domain.id, confirm_name="错误名称")
        self.session.rollback()

        deleted_impact = delete_domain(
            self.session,
            domain_id=second_domain.id,
            confirm_name="删除测试域",
        )
        self.session.commit()
        self.assertEqual(deleted_impact["counts"]["source_links"], 1)
        self.assertIsNone(self.session.get(Playlist, second_domain.id))
        self.assertIsNotNone(self.session.get(Media, self.media.id))

    def test_active_domain_job_blocks_delete_without_canceling_it(self) -> None:
        job = Job(
            id=uuid.uuid4(),
            type="playlist.build_event_map_snapshot",
            status="running",
            params={"playlist_id": str(self.domain.id)},
        )
        self.session.add(job)
        self.session.commit()

        with self.assertRaises(RuntimeError):
            delete_domain(
                self.session,
                domain_id=self.domain.id,
                confirm_name=self.domain.name,
            )
        self.session.rollback()

        retained_job = self.session.get(Job, job.id)
        self.assertEqual(retained_job.status, "running")
        self.assertIsNotNone(self.session.get(Playlist, self.domain.id))


if __name__ == "__main__":
    unittest.main()
