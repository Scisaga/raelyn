from __future__ import annotations

import sys
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, select, text

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.jobs.progress import _set_job_lease_deadline
from raelyn.models import Job


class AsrJobLeaseIntegrationTests(unittest.TestCase):
    def test_lease_extension_keeps_running_job_out_of_expired_scan(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        job_id = uuid.uuid4()
        now = datetime(2026, 7, 20, 0, 0, 0)
        initial_lease = now + timedelta(hours=1)
        extended_lease = now + timedelta(seconds=7988 + 300)

        with engine.begin() as conn:
            conn.exec_driver_sql(
                "create table job ("
                "id char(32) primary key, "
                "status varchar not null, "
                "worker_id varchar, "
                "lease_expires_at datetime"
                ")"
            )
            conn.execute(
                text(
                    "insert into job (id, status, worker_id, lease_expires_at) "
                    "values (:id, 'running', :worker_id, :lease_expires_at)"
                ),
                {
                    "id": job_id.hex,
                    "worker_id": "worker-asr-1",
                    "lease_expires_at": initial_lease,
                },
            )

            updated = _set_job_lease_deadline(
                conn,
                job_id=job_id,
                worker_id="worker-asr-1",
                lease_expires_at=extended_lease,
            )
            expired_job_ids = conn.execute(
                select(Job.id).where(
                    Job.status == "running",
                    Job.lease_expires_at < now + timedelta(hours=1, minutes=30),
                )
            ).scalars().all()

        self.assertTrue(updated)
        self.assertNotIn(job_id, expired_job_ids)

    def test_lease_extension_never_shortens_and_checks_worker_ownership(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        job_id = uuid.uuid4()
        now = datetime(2026, 7, 20, 0, 0, 0)
        existing_lease = now + timedelta(hours=3)

        with engine.begin() as conn:
            conn.exec_driver_sql(
                "create table job ("
                "id char(32) primary key, "
                "status varchar not null, "
                "worker_id varchar, "
                "lease_expires_at datetime"
                ")"
            )
            conn.execute(
                text(
                    "insert into job (id, status, worker_id, lease_expires_at) "
                    "values (:id, 'running', :worker_id, :lease_expires_at)"
                ),
                {
                    "id": job_id.hex,
                    "worker_id": "worker-asr-1",
                    "lease_expires_at": existing_lease,
                },
            )

            self.assertTrue(
                _set_job_lease_deadline(
                    conn,
                    job_id=job_id,
                    worker_id="worker-asr-1",
                    lease_expires_at=now + timedelta(hours=2),
                )
            )
            self.assertFalse(
                _set_job_lease_deadline(
                    conn,
                    job_id=job_id,
                    worker_id="another-worker",
                    lease_expires_at=now + timedelta(hours=4),
                )
            )
            stored_lease = conn.execute(
                select(Job.lease_expires_at).where(Job.id == job_id)
            ).scalar_one()

        self.assertEqual(stored_lease, existing_lease)


if __name__ == "__main__":
    unittest.main()
