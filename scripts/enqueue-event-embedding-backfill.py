#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if VENV_PYTHON.exists() and Path(sys.executable).absolute() != VENV_PYTHON.absolute():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import func, select

from raelyn.config import settings
from raelyn.db import session_scope
from raelyn.jobs.enqueue import enqueue_job
from raelyn.models import MarketEvent, MarketEventEmbedding, PlaylistMedia, Video


SOURCE_MODEL = "Qwen/Qwen3-Embedding-8B"
TARGET_MODEL = "Qwen/Qwen3-Embedding-4B"
EMBEDDING_DIM = 1024
BATCH_SIZE = 64


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将全部 accepted 事件的 8B embedding 原位迁移到 4B。默认只读预检。",
    )
    parser.add_argument("--yes", action="store_true", help="实际入队；未传此参数时仅执行只读 dry-run。")
    parser.add_argument("--priority", type=int, default=10, help="Job 优先级，默认 10。")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    configured_model = str(settings.embedding_model or "").strip()
    configured_dim = int(settings.embedding_dim or 0)
    if configured_model != TARGET_MODEL or configured_dim != EMBEDDING_DIM:
        raise SystemExit(
            "当前配置与迁移目标不一致："
            f"EMBEDDING_MODEL={configured_model!r}, EMBEDDING_DIM={configured_dim}"
        )

    with session_scope() as session:
        accepted_total = int(
            session.execute(
                select(func.count(MarketEvent.id)).where(MarketEvent.status == "accepted")
            ).scalar_one()
            or 0
        )
        source_rows = int(
            session.execute(
                select(func.count(MarketEventEmbedding.id)).where(
                    MarketEventEmbedding.embedding_model == SOURCE_MODEL,
                    MarketEventEmbedding.embedding_dim == EMBEDDING_DIM,
                )
            ).scalar_one()
            or 0
        )
        ready_target = int(
            session.execute(
                select(func.count(MarketEvent.id))
                .join(MarketEventEmbedding, MarketEventEmbedding.event_id == MarketEvent.id)
                .where(
                    MarketEvent.status == "accepted",
                    MarketEventEmbedding.embedding_model == TARGET_MODEL,
                    MarketEventEmbedding.embedding_dim == EMBEDDING_DIM,
                    MarketEventEmbedding.status == "ready",
                    MarketEventEmbedding.vector.is_not(None),
                )
            ).scalar_one()
            or 0
        )
        affected_playlists = list(
            session.execute(
                select(PlaylistMedia.playlist_id)
                .join(Video, Video.media_id == PlaylistMedia.media_id)
                .join(MarketEvent, MarketEvent.source_video_id == Video.id)
                .where(MarketEvent.status == "accepted")
                .distinct()
                .order_by(PlaylistMedia.playlist_id.asc())
            ).scalars()
        )
        print(
            "[event-embedding-backfill] "
            f"source_model={SOURCE_MODEL} target_model={TARGET_MODEL} "
            f"embedding_dim={EMBEDDING_DIM} batch_size={BATCH_SIZE}",
            flush=True,
        )
        print(
            "[event-embedding-backfill] "
            f"accepted_total={accepted_total} ready_target={ready_target} "
            f"remaining={max(0, accepted_total - ready_target)} source_rows={source_rows} "
            f"affected_playlists={len(affected_playlists)}",
            flush=True,
        )
        if not args.yes:
            print("[event-embedding-backfill] dry_run=true；传入 --yes 后入队", flush=True)
            return 0

        job_id = enqueue_job(
            session,
            type_="event.backfill_embeddings",
            params={
                "source_model": SOURCE_MODEL,
                "target_model": TARGET_MODEL,
                "embedding_dim": EMBEDDING_DIM,
                "batch_size": BATCH_SIZE,
            },
            priority=int(args.priority),
        )
        session.commit()
        print(f"[event-embedding-backfill] enqueued_job_id={job_id}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
