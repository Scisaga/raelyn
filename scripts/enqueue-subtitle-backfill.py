#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections import Counter
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if VENV_PYTHON.exists() and Path(sys.executable).absolute() != VENV_PYTHON.absolute():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import exists, select

from raelyn.db import session_scope
from raelyn.jobs.enqueue import enqueue_job
from raelyn.jobs.handlers.video_download import _target_subtitle_langs
from raelyn.models import Asset, Media, Video


SUPPORTED_PROVIDERS = {"youtube", "bilibili"}


def _log(message: str) -> None:
    print(message, flush=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Enqueue subtitle-only yt-dlp backfill jobs for collected videos.",
    )
    parser.add_argument(
        "--scope",
        default="downloaded",
        choices=["downloaded", "transcribed", "all"],
        help="Candidate scope. downloaded=videos with raw video asset, transcribed=videos with plain transcript, all=all video rows. Default: downloaded.",
    )
    parser.add_argument("--provider", choices=["youtube", "bilibili"], help="Only enqueue videos from this provider.")
    parser.add_argument("--video-id", help="Only enqueue one video UUID.")
    parser.add_argument("--media-id", help="Only enqueue videos from one media UUID.")
    parser.add_argument("--media-name-like", help="Case-insensitive substring filter for media name.")
    parser.add_argument(
        "--target-language",
        default="auto",
        choices=["auto", "zh", "en", "all"],
        help="Target subtitle language family. auto infers zh/en from provider/title/media metadata. Default: auto.",
    )
    parser.add_argument("--include-existing", action="store_true", help="Also enqueue when target raw subtitle asset already exists.")
    parser.add_argument("--force", action="store_true", help="Replace existing raw subtitle asset when the worker downloads one.")
    parser.add_argument("--priority", type=int, default=5, help="Job priority. Default: 5.")
    parser.add_argument("--limit", type=int, default=0, help="Max eligible videos to enqueue/print. Default: no limit.")
    parser.add_argument("--progress-every", type=int, default=1000, help="Print progress every N scanned rows. Default: 1000.")
    parser.add_argument("--commit-every", type=int, default=1000, help="Commit every N enqueued jobs. Default: 1000.")
    parser.add_argument("--sample", type=int, default=10, help="Print first N eligible samples. Default: 10.")
    parser.add_argument("--yes", action="store_true", help="Actually enqueue jobs. Without this flag the script is dry-run only.")
    return parser


def _parse_uuid(value: str | None, *, name: str) -> uuid.UUID | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be a UUID, got: {raw}") from exc


def _scope_filter(scope: str):
    if scope == "downloaded":
        return exists(
            select(1).where(
                Asset.video_id == Video.id,
                Asset.type == "video",
                Asset.source == "ytdlp",
                Asset.variant == "raw",
            )
        )
    if scope == "transcribed":
        return exists(
            select(1).where(
                Asset.video_id == Video.id,
                Asset.type == "transcript",
                Asset.format == "txt",
                Asset.variant == "plain",
            )
        )
    return None


def _job_type(provider: str) -> str | None:
    if provider == "youtube":
        return "video.backfill_subtitles.youtube"
    if provider == "bilibili":
        return "video.backfill_subtitles.bilibili"
    return None


def _video_from_row(row) -> SimpleNamespace:
    return SimpleNamespace(
        id=row.video_id,
        provider=row.provider,
        provider_video_id=row.provider_video_id,
        media_id=row.media_id,
        url=row.url,
        title=row.title,
        description=row.description,
    )


def _media_from_row(row) -> SimpleNamespace:
    return SimpleNamespace(id=row.media_id, name=row.media_name, description=row.media_description)


def _short(value: object, limit: int = 72) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}..."


def main() -> int:
    args = _build_parser().parse_args()
    video_id = _parse_uuid(args.video_id, name="--video-id")
    media_id = _parse_uuid(args.media_id, name="--media-id")
    dry_run = not bool(args.yes)
    target_arg = None if args.target_language == "auto" else args.target_language
    limit = max(0, int(args.limit or 0))
    progress_every = max(1, int(args.progress_every or 1000))
    commit_every = max(1, int(args.commit_every or 1000))
    sample_limit = max(0, int(args.sample or 0))

    _log("[subtitle-backfill] start")
    _log(
        "[subtitle-backfill] "
        f"scope={args.scope} provider={args.provider or '*'} video_id={video_id or '*'} media_id={media_id or '*'} "
        f"target_language={args.target_language} force={bool(args.force)} dry_run={dry_run}"
    )

    with session_scope() as session:
        raw_subtitle_rows = session.execute(
            select(Asset.video_id, Asset.language).where(
                Asset.type == "subtitle",
                Asset.source == "ytdlp",
                Asset.variant == "raw",
            )
        ).all()
        raw_subtitle_langs: dict[uuid.UUID, set[str]] = {}
        for raw_video_id, language in raw_subtitle_rows:
            if not raw_video_id:
                continue
            raw_subtitle_langs.setdefault(raw_video_id, set()).add(str(language or "").strip())

        stmt = (
            select(
                Video.id.label("video_id"),
                Video.provider,
                Video.provider_video_id,
                Video.media_id,
                Video.url,
                Video.title,
                Video.description,
                Media.name.label("media_name"),
                Media.description.label("media_description"),
            )
            .join(Media, Media.id == Video.media_id)
            .order_by(Video.created_at.asc(), Video.id.asc())
        )
        scope_filter = _scope_filter(args.scope)
        if scope_filter is not None:
            stmt = stmt.where(scope_filter)
        if args.provider:
            stmt = stmt.where(Video.provider == args.provider)
        if video_id:
            stmt = stmt.where(Video.id == video_id)
        if media_id:
            stmt = stmt.where(Video.media_id == media_id)
        if args.media_name_like:
            stmt = stmt.where(Media.name.ilike(f"%{str(args.media_name_like).strip()}%"))

        scanned = 0
        eligible = 0
        skipped_existing = 0
        skipped_provider = 0
        target_counts: Counter[str] = Counter()
        provider_counts: Counter[str] = Counter()
        job_ids: list[uuid.UUID] = []
        samples: list[str] = []

        _log("[subtitle-backfill] scanning candidates ...")
        for row in session.execute(stmt).mappings():
            scanned += 1
            if scanned == 1 or scanned % progress_every == 0:
                _log(f"[subtitle-backfill] scanned={scanned} eligible={eligible} enqueued={len(job_ids)}")

            provider = str(row["provider"] or "").strip().lower()
            job_type = _job_type(provider)
            if provider not in SUPPORTED_PROVIDERS or not job_type:
                skipped_provider += 1
                continue

            video = _video_from_row(row)
            media = _media_from_row(row)
            target_language, subtitle_langs = _target_subtitle_langs(video, media, target_arg)

            existing_langs = raw_subtitle_langs.get(video.id, set())
            has_existing_target = bool({str(item).strip() for item in subtitle_langs} & existing_langs)
            if has_existing_target and not args.include_existing and not args.force:
                skipped_existing += 1
                continue

            eligible += 1
            target_counts[target_language] += 1
            provider_counts[provider] += 1

            if len(samples) < sample_limit:
                samples.append(
                    f"video_id={video.id} provider={provider} target={target_language} "
                    f"media={_short(media.name, 36)!r} title={_short(video.title)!r}"
                )

            if not dry_run:
                job_ids.append(
                    enqueue_job(
                        session,
                        type_=job_type,
                        params={
                            "video_id": str(video.id),
                            "target_language": args.target_language,
                            "force": bool(args.force),
                        },
                        priority=int(args.priority),
                    )
                )
                if len(job_ids) % commit_every == 0:
                    session.commit()
                    _log(f"[subtitle-backfill] committed enqueued={len(job_ids)}")

            if limit > 0 and eligible >= limit:
                break

        if job_ids and len(job_ids) % commit_every != 0:
            session.commit()
            _log(f"[subtitle-backfill] committed enqueued={len(job_ids)}")

    for item in samples:
        _log(f"[subtitle-backfill] sample {item}")
    _log(f"[subtitle-backfill] scanned={scanned}")
    _log(f"[subtitle-backfill] eligible={eligible}")
    _log(f"[subtitle-backfill] skipped_existing_target_subtitle={skipped_existing}")
    _log(f"[subtitle-backfill] skipped_unsupported_provider={skipped_provider}")
    _log(f"[subtitle-backfill] by_provider={dict(provider_counts)}")
    _log(f"[subtitle-backfill] by_target={dict(target_counts)}")
    if dry_run:
        _log("[subtitle-backfill] dry_run=true; pass --yes to enqueue")
    else:
        _log(f"[subtitle-backfill] submitted_jobs={len(job_ids)} unique_job_ids_returned={len(set(job_ids))}")
    _log("[subtitle-backfill] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
