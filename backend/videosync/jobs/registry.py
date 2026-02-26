from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from videosync.models import Job

JobHandler = Callable[[Session, Job], dict | None]


class JobRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, job_type: str) -> Callable[[JobHandler], JobHandler]:
        def decorator(func: JobHandler) -> JobHandler:
            self._handlers[job_type] = func
            return func

        return decorator

    def get(self, job_type: str) -> JobHandler | None:
        return self._handlers.get(job_type)


registry = JobRegistry()

