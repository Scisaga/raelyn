"""审计满盘故障、重试受影响业务任务，并恢复手动暂停的 worker。

示例：PYTHONPATH=backend ./.venv/bin/python -m raelyn.tools.recover_disk_full_jobs \
    --since 2026-10-05T00:00:00+08:00 --audit-dir /mnt/data1/raelyn-maintenance
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from sqlalchemy import select, text

from raelyn.api.jobs import retry_job
from raelyn.api.workers import resume_worker_role
from raelyn.db import session_scope
from raelyn.jobs.enqueue import enqueue_job
from raelyn.models import Job, Media
from raelyn.scheduler import _is_sync_due
from raelyn.services.provider_pause import get_provider_pause
from raelyn.services.worker_role_pause import get_worker_role_pauses
from raelyn.timeutil import utcnow


FAULT_PATTERN = (
    "no space left|diskfull|disk full|errno 28|could not extend file|temporary free space|"
    "no usable temporary directory|database system is in recovery|database system is starting|"
    "database system is shutting|server closed the connection unexpectedly"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, type=datetime.fromisoformat)
    parser.add_argument("--audit-dir", required=True, type=Path)
    args = parser.parse_args()
    audit_dir = args.audit_dir / ("disk-recovery-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    audit_dir.mkdir(parents=True, mode=0o700)
    with session_scope() as session:
        failed = session.execute(text("""
select id, type, dedupe_key, params, finished_at, error_message
from job where status = 'failed' and coalesce(finished_at, started_at, created_at) >= :since
  and lower(coalesce(error_message,'') || ' ' || coalesce(error_stack,'')) ~ :pattern
order by finished_at desc nulls last, id desc
"""), {"since": args.since, "pattern": FAULT_PATTERN}).mappings().all()
        groups = {}
        for row in failed:
            key = row["dedupe_key"] or f'{row["type"]}:{json.dumps(row["params"], sort_keys=True)}'
            groups.setdefault(key, row)
        recovery = []
        for row in groups.values():
            scope = [Job.type == row["type"]]
            if row["dedupe_key"]:
                scope.append(Job.dedupe_key == row["dedupe_key"])
            else:
                scope.append(Job.params == row["params"])
            later_success = session.scalar(select(Job.id).where(
                *scope, Job.status == "succeeded", Job.finished_at > row["finished_at"],
            ).limit(1))
            active = session.scalar(select(Job.id).where(
                *scope, Job.status.in_(("pending", "running")),
            ).limit(1))
            original = session.get(Job, row["id"])
            assert original is not None
            recovery.append({"job_id": str(row["id"]), "type": row["type"],
                "action": "already_recovered" if later_success else "already_queued" if active else "retry",
                "existing_job_id": str(later_success or active) if later_success or active else None,
                "original_error": original.error_message, "original_stack": original.error_stack})
        roles = list(get_worker_role_pauses(session))
    report = {"since": args.since.isoformat(), "failure_count": len(failed),
        "failure_types": dict(Counter(row["type"] for row in failed)),
        "failure_ids": [str(row["id"]) for row in failed], "recovery": recovery, "roles": roles}
    audit_file = audit_dir / "audit.json"
    audit_file.write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2))
    audit_file.chmod(0o600)
    for item in recovery:
        if item["action"] == "retry":
            retry_job(uuid.UUID(item["job_id"]))

    scheduled = []
    with session_scope() as session:
        now = utcnow()
        for media in session.scalars(select(Media).where(Media.monitor_enabled.is_(True))):
            if not _is_sync_due(media, now) or get_provider_pause(session, media.provider).get("paused"):
                continue
            pending = session.scalar(select(Job.id).where(
                Job.type == "media.sync_videos", Job.status.in_(("pending", "running")),
                Job.params["media_id"].as_string() == str(media.id),
            ).limit(1))
            if pending:
                continue
            job = enqueue_job(session, type_="media.sync_videos", params={"media_id": str(media.id)}, priority=1)
            scheduled.append(str(job))
    for role in roles:
        resume_worker_role(role)
    report["scheduled_scans"] = scheduled
    report["completed_at"] = utcnow().isoformat()
    audit_file.write_text(json.dumps(report, ensure_ascii=False, default=str, indent=2))
    print({"审计失败记录": len(failed), "按业务去重后重试": sum(r["action"] == "retry" for r in recovery),
        "已有成功覆盖": sum(r["action"] == "already_recovered" for r in recovery),
        "已有任务执行或等待": sum(r["action"] == "already_queued" for r in recovery),
        "新增补扫": len(scheduled), "已启用worker角色": roles, "审计文件": str(audit_file)})


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print({"恢复失败类型": type(exc).__name__})
        raise SystemExit(1)
