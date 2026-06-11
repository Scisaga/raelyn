#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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
from raelyn.jobs.handlers.video_download import _target_subtitle_langs, _video_download_target
from raelyn.models import Asset, Media, Video
from raelyn.services.ytdlp import ytdlp_available_subtitle_languages, ytdlp_extract_info


def _log(message: str) -> None:
    print(message, flush=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe subtitle availability by media using yt-dlp metadata extraction only.",
    )
    parser.add_argument(
        "--scope",
        default="downloaded",
        choices=["downloaded", "transcribed", "all"],
        help="Video sample scope. Default: downloaded.",
    )
    parser.add_argument("--provider", choices=["youtube", "bilibili"], help="Only probe this provider.")
    parser.add_argument("--video-id", help="Only probe one video UUID.")
    parser.add_argument("--media-id", help="Only probe one media UUID.")
    parser.add_argument("--media-name-like", help="Case-insensitive substring filter for media name.")
    parser.add_argument("--media-limit", type=int, default=10, help="Max media rows to probe. Default: 10.")
    parser.add_argument("--limit-per-media", type=int, default=2, help="Max videos to probe per media. Default: 2.")
    parser.add_argument(
        "--target-language",
        default="auto",
        choices=["auto", "zh", "en", "all"],
        help="Target subtitle language family. Default: auto.",
    )
    parser.add_argument("--socket-timeout", type=int, default=20, help="yt-dlp socket timeout seconds. Default: 20.")
    parser.add_argument("--jsonl", action="store_true", help="Print per-video probe rows as JSON lines.")
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


def _video_from_row(row) -> SimpleNamespace:
    return SimpleNamespace(
        id=row["video_id"],
        provider=row["provider"],
        provider_video_id=row["provider_video_id"],
        media_id=row["media_id"],
        url=row["url"],
        title=row["title"],
        description=row["description"],
    )


def _media_from_model(media: Media) -> SimpleNamespace:
    return SimpleNamespace(id=media.id, name=media.name, description=media.description)


def _short(value: object, limit: int = 72) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}..."


def _matching_langs(available: list[str], target_langs: list[str]) -> list[str]:
    target = {str(item or "").strip().lower() for item in target_langs if str(item or "").strip()}
    return [lang for lang in available if str(lang or "").strip().lower() in target]


def _video_stmt(media_id: uuid.UUID, scope: str, limit: int, video_id: uuid.UUID | None):
    stmt = (
        select(
            Video.id.label("video_id"),
            Video.provider,
            Video.provider_video_id,
            Video.media_id,
            Video.url,
            Video.title,
            Video.description,
        )
        .where(Video.media_id == media_id)
        .order_by(Video.published_at.desc().nulls_last(), Video.created_at.desc(), Video.id.desc())
    )
    if video_id:
        stmt = stmt.where(Video.id == video_id)
    scope_filter = _scope_filter(scope)
    if scope_filter is not None:
        stmt = stmt.where(scope_filter)
    if limit > 0:
        stmt = stmt.limit(limit)
    return stmt


def main() -> int:
    args = _build_parser().parse_args()
    video_id = _parse_uuid(args.video_id, name="--video-id")
    media_id = _parse_uuid(args.media_id, name="--media-id")
    media_limit = max(0, int(args.media_limit or 0))
    limit_per_media = max(1, int(args.limit_per_media or 2))
    target_arg = None if args.target_language == "auto" else args.target_language

    if not args.jsonl:
        _log("[subtitle-analysis] start")
        _log(
            "[subtitle-analysis] "
            f"scope={args.scope} provider={args.provider or '*'} video_id={video_id or '*'} media_id={media_id or '*'} "
            f"target_language={args.target_language} media_limit={media_limit or 'all'} "
            f"limit_per_media={limit_per_media}"
        )

    with session_scope() as session:
        media_stmt = select(Media).order_by(Media.provider.asc(), Media.name.asc().nulls_last(), Media.id.asc())
        if args.provider:
            media_stmt = media_stmt.where(Media.provider == args.provider)
        if media_id:
            media_stmt = media_stmt.where(Media.id == media_id)
        if args.media_name_like:
            media_stmt = media_stmt.where(Media.name.ilike(f"%{str(args.media_name_like).strip()}%"))

        video_exists = select(1).where(Video.media_id == Media.id)
        if video_id:
            video_exists = video_exists.where(Video.id == video_id)
        scope_filter = _scope_filter(args.scope)
        if scope_filter is not None:
            video_exists = video_exists.where(scope_filter)
        media_stmt = media_stmt.where(exists(video_exists))
        if media_limit > 0:
            media_stmt = media_stmt.limit(media_limit)

        medias = session.execute(media_stmt).scalars().all()
        if not args.jsonl:
            _log(f"[subtitle-analysis] media_count={len(medias)}")

        total = Counter()
        for media in medias:
            media_ns = _media_from_model(media)
            summary = Counter()
            target_counter: Counter[str] = Counter()
            video_rows = session.execute(_video_stmt(media.id, args.scope, limit_per_media, video_id)).mappings().all()

            for row in video_rows:
                video = _video_from_row(row)
                target_language, target_langs = _target_subtitle_langs(video, media_ns, target_arg)
                target_counter[target_language] += 1
                record = {
                    "media_id": str(media.id),
                    "media_name": media.name,
                    "provider": media.provider,
                    "video_id": str(video.id),
                    "provider_video_id": video.provider_video_id,
                    "title": video.title,
                    "target_language": target_language,
                    "target_subtitle_languages": target_langs,
                    "manual": [],
                    "automatic": [],
                    "matched_manual": [],
                    "matched_automatic": [],
                    "target_hit": False,
                    "error": None,
                }
                try:
                    info = ytdlp_extract_info(
                        _video_download_target(video),
                        provider=video.provider,
                        flat=False,
                        max_entries=1,
                        socket_timeout=int(args.socket_timeout or 20),
                    )
                    available = ytdlp_available_subtitle_languages(info)
                    manual = available["manual"]
                    automatic = available["automatic"]
                    matched_manual = _matching_langs(manual, target_langs)
                    matched_automatic = _matching_langs(automatic, target_langs)
                    target_hit = bool(matched_manual or matched_automatic)
                    record.update(
                        {
                            "manual": manual,
                            "automatic": automatic,
                            "matched_manual": matched_manual,
                            "matched_automatic": matched_automatic,
                            "target_hit": target_hit,
                        }
                    )
                    summary["sampled"] += 1
                    total["sampled"] += 1
                    if target_hit:
                        summary["target_hits"] += 1
                        total["target_hits"] += 1
                    else:
                        summary["target_missing"] += 1
                        total["target_missing"] += 1
                    if matched_manual:
                        summary["manual_hits"] += 1
                        total["manual_hits"] += 1
                    if matched_automatic:
                        summary["automatic_hits"] += 1
                        total["automatic_hits"] += 1
                except Exception as exc:
                    message = str(exc).replace("\n", " ")[:300]
                    record["error"] = message
                    summary["errors"] += 1
                    total["errors"] += 1

                if args.jsonl:
                    print(json.dumps(record, ensure_ascii=False, sort_keys=True), flush=True)
                else:
                    status = "hit" if record["target_hit"] else ("error" if record["error"] else "missing")
                    detail = (
                        f"manual={record['matched_manual'] or []} "
                        f"auto={record['matched_automatic'] or []}"
                    )
                    if record["error"]:
                        detail = f"error={record['error']}"
                    _log(
                        "[subtitle-analysis] "
                        f"video status={status} media={_short(media.name, 36)!r} "
                        f"target={target_language} title={_short(video.title)!r} {detail}"
                    )

            if not args.jsonl:
                _log(
                    "[subtitle-analysis] media "
                    f"id={media.id} provider={media.provider} name={_short(media.name, 48)!r} "
                    f"sampled={summary['sampled']} target_hits={summary['target_hits']} "
                    f"manual_hits={summary['manual_hits']} automatic_hits={summary['automatic_hits']} "
                    f"target_missing={summary['target_missing']} errors={summary['errors']} "
                    f"targets={dict(target_counter)}"
                )

        if not args.jsonl:
            _log(
                "[subtitle-analysis] total "
                f"sampled={total['sampled']} target_hits={total['target_hits']} "
                f"manual_hits={total['manual_hits']} automatic_hits={total['automatic_hits']} "
                f"target_missing={total['target_missing']} errors={total['errors']}"
            )
            _log("[subtitle-analysis] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
