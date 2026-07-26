from __future__ import annotations

import os
from collections.abc import MutableMapping


DEFAULT_ANALYSIS_CPU_THREADS = 2
ANALYSIS_JOB_TYPES = (
    "playlist.mark_event_map_dirty",
    "playlist.build_event_map_snapshot",
    "playlist.prune_event_map_snapshots",
)
_NON_ANALYSIS_WORKER_ROLES = frozenset(
    {
        "download_youtube",
        "download_bilibili",
        "audio",
        "process",
        "asr",
        "sync",
        "embedding",
        "ai",
    }
)
_NUMERIC_THREAD_ENV_NAMES = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
)


def _parse_worker_types(value: str | None) -> set[str]:
    return {item.strip() for item in str(value or "").split(",") if item.strip()}


def _can_claim_analysis_job(environ: MutableMapping[str, str]) -> bool:
    worker_types = _parse_worker_types(environ.get("WORKER_TYPES"))
    if worker_types:
        return bool(worker_types.intersection(ANALYSIS_JOB_TYPES))

    role = str(environ.get("WORKER_ROLE") or "").strip().lower()
    # 空角色、all 和未知角色都会进入 all-types 模式，因此也可能领取 analysis 任务。
    return role not in _NON_ANALYSIS_WORKER_ROLES


def configure_worker_runtime_env(
    environ: MutableMapping[str, str] | None = None,
) -> int | None:
    target = os.environ if environ is None else environ
    if not _can_claim_analysis_job(target):
        return None

    raw_threads = str(target.get("ANALYSIS_CPU_THREADS") or DEFAULT_ANALYSIS_CPU_THREADS).strip()
    if not raw_threads.isascii() or not raw_threads.isdigit() or int(raw_threads) < 1:
        raise RuntimeError(
            "ANALYSIS_CPU_THREADS must be a positive integer, "
            f"got: {raw_threads or '<empty>'}"
        )

    threads = int(raw_threads)
    normalized = str(threads)
    target["ANALYSIS_CPU_THREADS"] = normalized
    for name in _NUMERIC_THREAD_ENV_NAMES:
        target[name] = normalized
    return threads
