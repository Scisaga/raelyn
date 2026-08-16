from __future__ import annotations

from datetime import datetime
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, or_, select

from raelyn.db import session_scope
from raelyn.models import Asset, Media, Playlist, PlaylistMedia, Video
from raelyn.services.domain_management import (
    attach_domain_source,
    delete_domain,
    detach_domain_source,
    domain_deletion_impact,
)
from raelyn.services.media_sources import resolve_media_url
from raelyn.services.v2_observation import (
    canonical_directory,
    canonical_history,
    cursor_payload,
    domain_directory,
    domain_observation,
    evidence_context,
    list_changes,
    object_brief_references,
    observation_feed,
    semantic_search,
    source_semantic_references,
    story_directory,
    story_history,
    structured_brief,
    topic_detail,
    update_observation_cursor,
    update_story_read_state,
)


router = APIRouter(tags=["v2-observation"])


def _not_found(error: LookupError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(error) or "not found")


class ObservationCursorUpdate(BaseModel):
    snapshot_id: uuid.UUID | None = None
    observed_at: datetime | None = None
    event_time_start: datetime | None = None
    event_time_end: datetime | None = None
    view_mode: Literal["now", "replay", "story", "verify"] | None = None
    last_page: Literal["field", "stories", "briefs", "playlist", "library", "operations"] | None = None
    camera_state: dict[str, Any] | None = None
    filter_state: dict[str, Any] | None = None
    selected_object_type: Literal["canonical", "story", "topic", "evidence"] | None = None
    selected_object_id: uuid.UUID | None = None
    last_change_id: uuid.UUID | None = None


class StoryReadUpdate(BaseModel):
    followed: bool | None = None
    snapshot_id: uuid.UUID | None = None
    position: int | None = Field(default=None, ge=0)
    mark_read: bool = False


class DomainSourceCreate(BaseModel):
    media_id: uuid.UUID | None = None
    url: str | None = None

    @model_validator(mode="after")
    def validate_identity(self) -> "DomainSourceCreate":
        has_media_id = self.media_id is not None
        has_url = bool(str(self.url or "").strip())
        if has_media_id == has_url:
            raise ValueError("exactly one of media_id or url is required")
        return self


class DomainDeleteRequest(BaseModel):
    confirm_name: str


def _source_payload(media: Media) -> dict[str, Any]:
    return {
        "id": str(media.id),
        "provider": media.provider,
        "name": media.name,
        "url": media.url,
        "monitor_enabled": bool(media.monitor_enabled),
    }


@router.get("/domains")
def list_domains() -> dict[str, Any]:
    with session_scope() as session:
        return {"items": domain_directory(session)}


@router.post("/domains/{playlist_id}/sources")
def create_domain_source(playlist_id: uuid.UUID, payload: DomainSourceCreate) -> dict[str, Any]:
    with session_scope() as session:
        created = False
        if payload.media_id is not None:
            media_id = payload.media_id
        else:
            try:
                resolution = resolve_media_url(session, url=str(payload.url or ""))
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            media_id = resolution.media.id
            created = resolution.created

        try:
            result = attach_domain_source(session, domain_id=playlist_id, media_id=media_id)
        except LookupError as error:
            raise _not_found(error) from error
        session.flush()
        return {
            "source": _source_payload(result.media),
            "created": created,
            "attached": result.attached,
        }


@router.delete("/domains/{playlist_id}/sources/{media_id}")
def delete_domain_source(playlist_id: uuid.UUID, media_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as session:
        try:
            detached = detach_domain_source(session, domain_id=playlist_id, media_id=media_id)
        except LookupError as error:
            raise _not_found(error) from error
        return {"ok": True, "detached": detached}


@router.get("/domains/{playlist_id}/deletion-impact")
def get_domain_deletion_impact(playlist_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as session:
        try:
            return domain_deletion_impact(session, playlist_id)
        except LookupError as error:
            raise _not_found(error) from error


@router.delete("/domains/{playlist_id}")
def delete_domain_safely(playlist_id: uuid.UUID, payload: DomainDeleteRequest) -> dict[str, Any]:
    with session_scope() as session:
        try:
            impact = delete_domain(
                session,
                domain_id=playlist_id,
                confirm_name=payload.confirm_name,
            )
        except LookupError as error:
            raise _not_found(error) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except RuntimeError as error:
            jobs = domain_deletion_impact(session, playlist_id).get("active_jobs", [])
            raise HTTPException(
                status_code=409,
                detail={"message": str(error), "active_jobs": jobs},
            ) from error
        return {"ok": True, "deleted_domain_id": str(playlist_id), "impact": impact}


@router.get("/domains/{playlist_id}/observation")
def get_domain_observation(playlist_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as session:
        try:
            return domain_observation(session, playlist_id)
        except LookupError as error:
            raise _not_found(error) from error


@router.get("/domains/{playlist_id}/observation/cursor")
def get_observation_cursor(playlist_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as session:
        if session.get(Playlist, playlist_id) is None:
            raise HTTPException(status_code=404, detail="domain not found")
        from raelyn.models import DomainObservationCursor

        return cursor_payload(session.get(DomainObservationCursor, playlist_id), playlist_id)


@router.put("/domains/{playlist_id}/observation/cursor")
def put_observation_cursor(playlist_id: uuid.UUID, payload: ObservationCursorUpdate) -> dict[str, Any]:
    with session_scope() as session:
        try:
            cursor = update_observation_cursor(
                session,
                playlist_id=playlist_id,
                values=payload.model_dump(exclude_unset=True),
            )
            return cursor_payload(cursor, playlist_id)
        except LookupError as error:
            raise _not_found(error) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/domains/{playlist_id}/changes")
def get_changes(
    playlist_id: uuid.UUID,
    after_snapshot_id: uuid.UUID | None = None,
    object_type: str | None = None,
    change_type: str | None = None,
    cursor: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    with session_scope() as session:
        try:
            return list_changes(
                session,
                playlist_id=playlist_id,
                after_snapshot_id=after_snapshot_id,
                object_type=object_type,
                change_type=change_type,
                cursor_id=cursor,
                limit=limit,
            )
        except LookupError as error:
            raise _not_found(error) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/domains/{playlist_id}/observation/feed")
def get_observation_feed(
    playlist_id: uuid.UUID,
    limit: int = Query(default=30, ge=1, le=100),
) -> dict[str, Any]:
    with session_scope() as session:
        try:
            return observation_feed(session, playlist_id, limit=limit)
        except LookupError as error:
            raise _not_found(error) from error


@router.get("/domains/{playlist_id}/canonicals/{canonical_id}/history")
def get_canonical_history(playlist_id: uuid.UUID, canonical_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as session:
        try:
            return canonical_history(session, playlist_id, canonical_id)
        except LookupError as error:
            raise _not_found(error) from error


@router.get("/domains/{playlist_id}/canonicals")
def list_domain_canonicals(
    playlist_id: uuid.UUID,
    event_time_start: datetime | None = None,
    event_time_end: datetime | None = None,
    event_type: str | None = None,
    limit: int = Query(default=500, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    with session_scope() as session:
        if session.get(Playlist, playlist_id) is None:
            raise HTTPException(status_code=404, detail="domain not found")
        return canonical_directory(
            session,
            playlist_id,
            event_time_start=event_time_start,
            event_time_end=event_time_end,
            event_type=event_type,
            limit=limit,
            offset=offset,
        )


@router.get("/domains/{playlist_id}/stories")
def list_domain_stories(playlist_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as session:
        if session.get(Playlist, playlist_id) is None:
            raise HTTPException(status_code=404, detail="domain not found")
        return {"items": story_directory(session, playlist_id)}


@router.get("/domains/{playlist_id}/topics/{topic_id}")
def get_domain_topic(
    playlist_id: uuid.UUID,
    topic_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    snapshot_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    with session_scope() as session:
        try:
            return topic_detail(session, playlist_id, topic_id, limit=limit, snapshot_id=snapshot_id)
        except LookupError as error:
            raise _not_found(error) from error


@router.get("/domains/{playlist_id}/stories/{story_identity_id}")
def get_story_history(
    playlist_id: uuid.UUID,
    story_identity_id: uuid.UUID,
    snapshot_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    with session_scope() as session:
        try:
            return story_history(
                session,
                playlist_id,
                story_identity_id,
                snapshot_id=snapshot_id,
            )
        except LookupError as error:
            raise _not_found(error) from error


@router.patch("/domains/{playlist_id}/stories/{story_identity_id}/read-state")
def patch_story_read_state(
    playlist_id: uuid.UUID,
    story_identity_id: uuid.UUID,
    payload: StoryReadUpdate,
) -> dict[str, Any]:
    with session_scope() as session:
        try:
            state = update_story_read_state(
                session,
                playlist_id=playlist_id,
                identity_id=story_identity_id,
                followed=payload.followed,
                snapshot_id=payload.snapshot_id,
                position=payload.position,
                mark_read=payload.mark_read,
            )
            return {
                "story_identity_id": str(story_identity_id),
                "followed": bool(state.followed),
                "last_read_snapshot_id": str(state.last_read_snapshot_id) if state.last_read_snapshot_id else None,
                "last_read_at": state.last_read_at.isoformat() if state.last_read_at else None,
                "last_position": state.last_position,
            }
        except LookupError as error:
            raise _not_found(error) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/briefs/{brief_id}/structured")
def get_structured_brief(brief_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as session:
        try:
            return structured_brief(session, brief_id)
        except LookupError as error:
            raise _not_found(error) from error


@router.get("/domains/{playlist_id}/objects/{object_type}/{object_id}/brief-references")
def get_object_brief_references(
    playlist_id: uuid.UUID,
    object_type: Literal["canonical", "topic", "story", "evidence", "source"],
    object_id: uuid.UUID,
    snapshot_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    with session_scope() as session:
        return {
            "items": object_brief_references(
                session,
                playlist_id=playlist_id,
                object_type=object_type,
                object_id=object_id,
                snapshot_id=snapshot_id,
            )
        }


@router.get("/domains/{playlist_id}/evidence/{revision_id}")
def get_evidence_context(playlist_id: uuid.UUID, revision_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as session:
        try:
            return evidence_context(session, playlist_id, revision_id)
        except LookupError as error:
            raise _not_found(error) from error


@router.get("/domains/{playlist_id}/source-records/{video_id}/semantic-references")
def get_source_semantic_references(playlist_id: uuid.UUID, video_id: uuid.UUID) -> dict[str, Any]:
    with session_scope() as session:
        try:
            return source_semantic_references(session, playlist_id, video_id)
        except LookupError as error:
            raise _not_found(error) from error


@router.get("/library/sources")
def list_library_sources(
    domain_id: uuid.UUID | None = None,
    scope: Literal["domain", "global"] = "domain",
    q: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    with session_scope() as session:
        current_domain_media_ids: set[uuid.UUID] = set()
        if domain_id is not None:
            if session.get(Playlist, domain_id) is None:
                raise HTTPException(status_code=404, detail="domain not found")
            current_domain_media_ids = set(
                session.execute(
                    select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == domain_id)
                ).scalars()
            )
        statement = select(Media)
        if q and str(q).strip():
            like = f"%{str(q).strip()}%"
            statement = statement.where(
                or_(
                    Media.name.ilike(like),
                    Media.description.ilike(like),
                    Media.url.ilike(like),
                )
            )
        if scope == "domain":
            if domain_id is None:
                raise HTTPException(status_code=400, detail="domain_id is required for domain scope")
            statement = statement.where(
                Media.id.in_(select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == domain_id))
            )
        rows = session.execute(statement.order_by(Media.updated_at.desc()).limit(limit).offset(offset)).scalars().all()
        counts = dict(
            session.execute(
                select(Video.media_id, func.count(Video.id)).where(Video.media_id.in_([row.id for row in rows])).group_by(Video.media_id)
            ).all()
        ) if rows else {}
        return {
            "scope": scope,
            "domain_id": str(domain_id) if domain_id else None,
            "items": [
                {
                    "id": str(row.id),
                    "provider": row.provider,
                    "name": row.name,
                    "url": row.url,
                    "monitor_enabled": row.monitor_enabled,
                    "in_current_domain": row.id in current_domain_media_ids,
                    "record_count": int(counts.get(row.id, 0)),
                    "last_sync_at": (row.last_video_sync_at or row.last_profile_sync_at).isoformat() if (row.last_video_sync_at or row.last_profile_sync_at) else None,
                    "web_url": f"/library?tab=sources&media_id={row.id}",
                }
                for row in rows
            ],
        }


@router.get("/library/source-records")
def list_library_source_records(
    domain_id: uuid.UUID | None = None,
    scope: Literal["domain", "global"] = "domain",
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    with session_scope() as session:
        statement = select(Video, Media).join(Media, Media.id == Video.media_id)
        if scope == "domain":
            if domain_id is None:
                raise HTTPException(status_code=400, detail="domain_id is required for domain scope")
            statement = statement.where(
                Video.media_id.in_(select(PlaylistMedia.media_id).where(PlaylistMedia.playlist_id == domain_id))
            )
        rows = session.execute(
            statement.order_by(Video.published_at.desc().nullslast(), Video.created_at.desc()).limit(limit).offset(offset)
        ).all()
        return {
            "scope": scope,
            "domain_id": str(domain_id) if domain_id else None,
            "items": [
                {
                    "id": str(video.id),
                    "media_id": str(video.media_id),
                    "media_name": media.name,
                    "provider": video.provider,
                    "title": video.title,
                    "url": video.url,
                    "published_at": video.published_at.isoformat() if video.published_at else None,
                    "status": video.status,
                    "web_url": f"/video?video_id={video.id}",
                    "semantic_references_url": f"/api/domains/{domain_id}/source-records/{video.id}/semantic-references" if domain_id else None,
                }
                for video, media in rows
            ],
        }


@router.get("/search/semantic")
def search_semantic_objects(
    q: str = Query(min_length=1),
    domain_id: uuid.UUID | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    with session_scope() as session:
        return {
            "query": q,
            "domain_id": str(domain_id) if domain_id else None,
            "groups": semantic_search(session, query=q, playlist_id=domain_id, limit=limit),
        }
