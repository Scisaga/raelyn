from __future__ import annotations

import os
import json
from typing import Any

from sqlalchemy.orm import Session

from raelyn.models import Job, JobEvent


def job_log(session: Session, job: Job, message: str, *, level: str = "info", data: dict[str, Any] | None = None) -> None:
    event = JobEvent(job_id=job.id, level=level, message=message, data=data)
    session.add(event)
    session.flush([event])

    # Also echo to stdout so operators can see worker activity in log files.
    # Default behavior:
    # - Always echo non-info levels.
    # - Always echo media.* jobs (sync/profile) even at info level.
    # - Set RAELYN_JOB_LOG_STDOUT=1 to echo all job events.
    # - Back-compat: also accepts VIDEOSYNC_JOB_LOG_STDOUT.
    try:
        flag = (os.getenv("RAELYN_JOB_LOG_STDOUT") or os.getenv("VIDEOSYNC_JOB_LOG_STDOUT") or "").strip()
        echo_all = flag == "1"
        should_echo = echo_all or (level != "info") or str(getattr(job, "type", "")).startswith("media.")
        if not should_echo:
            return

        prefix = f"[job:{level}]"
        job_type = getattr(job, "type", "")
        job_id = getattr(job, "id", "")
        extra = ""
        if data:
            # Keep it single-line; avoid huge payloads.
            try:
                extra = " data=" + json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            except Exception:
                extra = " data=<unserializable>"
        print(f"{prefix} id={job_id} type={job_type} msg={message}{extra}", flush=True)
    except Exception:
        # Never break job execution due to logging.
        return
