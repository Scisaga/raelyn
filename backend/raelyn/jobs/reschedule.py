from __future__ import annotations


class JobReschedule(Exception):
    def __init__(self, *, delay_seconds: int, reason: str) -> None:
        self.delay_seconds = max(0, int(delay_seconds or 0))
        self.reason = str(reason or "").strip() or "rescheduled"
        super().__init__(self.reason)

