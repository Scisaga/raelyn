from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
import uuid

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from raelyn.models import EventMapSnapshot, EventMapState
from raelyn.services.event_map_retention import prune_event_map_snapshots


class EventMapRetentionTests(unittest.TestCase):
    def test_prune_keeps_current_previous_ready_and_running_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            engine = create_engine(f"sqlite:///{Path(raw) / 'retention.sqlite'}")
            now = datetime(2026, 7, 23, tzinfo=timezone.utc)
            playlist_id = uuid.uuid4()
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "create table event_map_snapshot ("
                        "id varchar(32) primary key, playlist_id varchar(32) not null, "
                        "status varchar not null, finished_at datetime, created_at datetime not null)"
                    )
                )
                connection.execute(
                    text(
                        "create table event_map_state ("
                        "playlist_id varchar(32) primary key, current_snapshot_id varchar(32))"
                    )
                )
            with Session(engine) as session:
                ready: list[uuid.UUID] = []
                for index in range(4):
                    snapshot_id = uuid.uuid4()
                    ready.append(snapshot_id)
                    session.execute(
                        text(
                            "insert into event_map_snapshot "
                            "(id, playlist_id, status, finished_at, created_at) "
                            "values (:id, :playlist_id, 'ready', :finished_at, :created_at)"
                        ),
                        {
                            "id": snapshot_id.hex,
                            "playlist_id": playlist_id.hex,
                            "finished_at": now + timedelta(minutes=index),
                            "created_at": now + timedelta(minutes=index),
                        },
                    )
                failed_id = uuid.uuid4()
                running_id = uuid.uuid4()
                session.execute(
                    text(
                        "insert into event_map_snapshot "
                        "(id, playlist_id, status, finished_at, created_at) values "
                        "(:failed_id, :playlist_id, 'failed', :now, :now), "
                        "(:running_id, :playlist_id, 'running', null, :now)"
                    ),
                    {
                        "failed_id": failed_id.hex,
                        "running_id": running_id.hex,
                        "playlist_id": playlist_id.hex,
                        "now": now,
                    },
                )
                session.execute(
                    text(
                        "insert into event_map_state (playlist_id, current_snapshot_id) "
                        "values (:playlist_id, :current_snapshot_id)"
                    ),
                    {"playlist_id": playlist_id.hex, "current_snapshot_id": ready[-1].hex},
                )
                session.commit()

                result = prune_event_map_snapshots(
                    session,
                    playlist_id=playlist_id,
                    keep_ready=2,
                    batch_size=20,
                )
                session.commit()

                remaining = session.execute(
                    select(EventMapSnapshot.id, EventMapSnapshot.status)
                    .where(EventMapSnapshot.playlist_id == playlist_id)
                ).all()
                remaining_ids = {snapshot_id for snapshot_id, _status in remaining}
                self.assertEqual(result["deleted_count"], 3)
                self.assertEqual(result["remaining_count"], 0)
                self.assertEqual(remaining_ids, {ready[-1], ready[-2], running_id})
                current_snapshot_id = session.execute(
                    select(EventMapState.current_snapshot_id).where(EventMapState.playlist_id == playlist_id)
                ).scalar_one()
                self.assertEqual(current_snapshot_id, ready[-1])
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
