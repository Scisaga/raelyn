from __future__ import annotations

import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path


def tmp_root() -> Path:
    root = Path("tmp").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


@contextmanager
def job_workdir(job_id: uuid.UUID) -> Path:
    path = tmp_root() / f"job-{job_id}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)

