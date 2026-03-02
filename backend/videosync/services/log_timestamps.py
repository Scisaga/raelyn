from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import TextIO


class _TimestampedWriter:
    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._buf = ""

    def write(self, s: str) -> int:  # file-like
        if not s:
            return 0

        self._buf += s
        while True:
            i = self._buf.find("\n")
            if i < 0:
                break
            line = self._buf[:i]
            self._buf = self._buf[i + 1 :]
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            self._stream.write(f"[{ts}] {line}\n")
        return len(s)

    def flush(self) -> None:  # file-like
        if self._buf:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            self._stream.write(f"[{ts}] {self._buf}")
            self._buf = ""
        self._stream.flush()

    def isatty(self) -> bool:  # passthrough for libraries checking tty
        try:
            return bool(self._stream.isatty())
        except Exception:
            return False

    def fileno(self) -> int:  # pragma: no cover
        return self._stream.fileno()

    def __getattr__(self, name: str):  # pragma: no cover
        return getattr(self._stream, name)


def install_if_needed() -> None:
    """
    Prefix timestamps for all stdout/stderr lines when running non-interactively (e.g. redirected to log files).

    Control:
      - Set VIDEOSYNC_LOG_TIMESTAMP=1 to force enable.
      - Set VIDEOSYNC_LOG_TIMESTAMP=0 to force disable.
    """
    flag = (os.getenv("VIDEOSYNC_LOG_TIMESTAMP") or "").strip()
    if flag == "0":
        return
    force = flag == "1"

    try:
        out_is_tty = sys.stdout.isatty()
        err_is_tty = sys.stderr.isatty()
    except Exception:
        out_is_tty = False
        err_is_tty = False

    if not force and out_is_tty and err_is_tty:
        return

    if not isinstance(sys.stdout, _TimestampedWriter):
        sys.stdout = _TimestampedWriter(sys.stdout)  # type: ignore[assignment]
    if not isinstance(sys.stderr, _TimestampedWriter):
        sys.stderr = _TimestampedWriter(sys.stderr)  # type: ignore[assignment]
