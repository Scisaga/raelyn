from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from raelyn.jobs.registry import registry
from raelyn.models import Job, Playlist, Video, VideoEmbedding
from raelyn.services.embeddings import EmbeddingError, EmbeddingOverBudgetError, EmbeddingTransientError, embed_text
from raelyn.services.playlist_analysis import (
    analysis_spec,
    backfill_playlist_embeddings,
    build_playlist_analysis_snapshot,
    ensure_playlist_analysis_state,
    fetch_plain_transcript_for_embedding,
    mark_playlists_analysis_dirty_for_video,
)
from raelyn.timeutil import utcnow


@registry.register("video.embed_transcript")
def video_embed_transcript(session: Session, job: Job) -> dict | None:
    video_id = uuid.UUID(str(job.params["video_id"]))
    video = session.get(Video, video_id)
    if not video:
        return {"skipped": "video not found"}

    transcript_payload = fetch_plain_transcript_for_embedding(session, video_id)
    if not transcript_payload:
        return {"skipped": "plain transcript not found"}

    text, current_checksum = transcript_payload
    expected_checksum = str(job.params.get("text_checksum") or "").strip()
    if expected_checksum and expected_checksum != current_checksum:
        return {"skipped": "stale checksum"}

    spec = analysis_spec()
    existing = session.execute(
        select(VideoEmbedding).where(
            VideoEmbedding.video_id == video_id,
            VideoEmbedding.transcript_variant == spec.transcript_variant,
            VideoEmbedding.embedding_model == spec.model,
            VideoEmbedding.embedding_dim == spec.dim,
        )
    ).scalar_one_or_none()
    previous_status = str(getattr(existing, "status", "") or "").strip()
    previous_checksum = str(getattr(existing, "text_checksum", "") or "").strip()

    if existing is None:
        existing = VideoEmbedding(
            video_id=video_id,
            transcript_variant=spec.transcript_variant,
            embedding_model=spec.model,
            embedding_dim=spec.dim,
            status="pending",
            text_checksum=current_checksum,
        )
        session.add(existing)
        session.flush([existing])

    if previous_status == "ready" and previous_checksum == current_checksum and existing.vector:
        return {"ok": True, "cached": True, "embedding_id": str(existing.id)}

    try:
        vector = embed_text(text)
        existing.status = "ready"
        existing.vector = vector
        existing.text_checksum = current_checksum
        existing.skip_reason = None
        existing.generated_at = utcnow()
    except EmbeddingOverBudgetError as exc:
        existing.status = "skipped_over_budget"
        existing.vector = None
        existing.text_checksum = current_checksum
        existing.skip_reason = str(exc)[:500]
        existing.generated_at = utcnow()
    except EmbeddingTransientError:
        raise
    except EmbeddingError as exc:
        existing.status = "failed"
        existing.vector = None
        existing.text_checksum = current_checksum
        existing.skip_reason = str(exc)[:500]
        existing.generated_at = utcnow()
        session.flush([existing])
        if previous_status != existing.status or previous_checksum != current_checksum:
            mark_playlists_analysis_dirty_for_video(session, video_id)
        raise

    session.flush([existing])
    if previous_status != existing.status or previous_checksum != current_checksum:
        mark_playlists_analysis_dirty_for_video(session, video_id)
    return {"ok": True, "status": existing.status, "embedding_id": str(existing.id)}


@registry.register("playlist.build_analysis_snapshot")
def playlist_build_analysis_snapshot(session: Session, job: Job) -> dict | None:
    playlist_id = uuid.UUID(str(job.params["playlist_id"]))
    playlist = session.get(Playlist, playlist_id)
    if not playlist:
        return {"skipped": "playlist not found"}

    ensure_playlist_analysis_state(session, playlist_id)
    return build_playlist_analysis_snapshot(session, playlist_id, job=job)


@registry.register("playlist.backfill_embeddings")
def playlist_backfill_embeddings(session: Session, job: Job) -> dict | None:
    playlist_id = uuid.UUID(str(job.params["playlist_id"]))
    playlist = session.get(Playlist, playlist_id)
    if not playlist:
        return {"skipped": "playlist not found"}

    return backfill_playlist_embeddings(
        session,
        playlist_id=playlist_id,
        force=bool(job.params.get("force", False)),
        batch_size=job.params.get("batch_size"),
        job=job,
    )
