from __future__ import annotations

from collections.abc import Sequence
import uuid

from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from raelyn.models import (
    Brief,
    BriefReference,
    EventMapCanonical,
    EventMapCanonicalMember,
    EventMapRecordRevision,
    EventMapState,
    EventMapStory,
    EventMapStoryHistoryEvidence,
    Video,
)


_BRIEF_REFERENCE_NAMESPACE = uuid.UUID("a7870980-c9a5-40eb-96f0-cdc1bec74161")
_MAX_BRIEF_REFERENCES = 24


def _has_verified_evidence(items: object) -> bool:
    if not isinstance(items, list):
        return False
    for item in items:
        if not isinstance(item, dict):
            continue
        payload = item.get("evidence_json")
        if isinstance(payload, dict) and payload.get("verified") is True:
            return True
        if item.get("verified") is True:
            return True
    return False


def attach_structured_brief_references(
    session: Session,
    *,
    brief: Brief,
    videos: Sequence[Video],
    markdown: str,
) -> tuple[str, int]:
    """只把本次简报真实输入且已有 verified evidence 的星域对象写入引用。"""

    session.execute(delete(BriefReference).where(BriefReference.brief_id == brief.id))
    state = session.get(EventMapState, brief.playlist_id)
    snapshot_id = state.current_snapshot_id if state else None
    brief.snapshot_id = snapshot_id
    brief.generation_basis = {
        "schema_version": "structured_brief_v1",
        "snapshot_id": str(snapshot_id) if snapshot_id else None,
        "video_ids": [str(video.id) for video in videos],
        "reference_policy": "input_video_and_verified_semantic_evidence",
    }
    if snapshot_id is None or not videos:
        return markdown, 0

    video_ids = [video.id for video in videos]
    candidates = session.execute(
        select(EventMapCanonical, EventMapRecordRevision)
        .join(
            EventMapCanonicalMember,
            and_(
                EventMapCanonicalMember.snapshot_id == EventMapCanonical.snapshot_id,
                EventMapCanonicalMember.canonical_id == EventMapCanonical.canonical_id,
            ),
        )
        .join(
            EventMapRecordRevision,
            EventMapRecordRevision.id == EventMapCanonicalMember.record_revision_id,
        )
        .where(
            EventMapCanonical.snapshot_id == snapshot_id,
            EventMapRecordRevision.source_video_id.in_(video_ids),
        )
        .order_by(
            EventMapCanonical.event_time_start.desc(),
            EventMapCanonical.canonical_id.asc(),
            EventMapCanonicalMember.is_representative.desc(),
        )
    ).all()

    selected: list[tuple[EventMapCanonical, EventMapRecordRevision]] = []
    seen: set[uuid.UUID] = set()
    for canonical, revision in candidates:
        if canonical.canonical_id in seen or not _has_verified_evidence(revision.evidence_json):
            continue
        selected.append((canonical, revision))
        seen.add(canonical.canonical_id)
        if len(selected) >= _MAX_BRIEF_REFERENCES:
            break
    if not selected:
        return markdown, 0

    lines = [markdown.rstrip(), "", "## 星域引用", ""]
    position = 0

    def add_reference(
        *,
        object_type: str,
        object_id: uuid.UUID,
        label: str,
        evidence_revision_id: uuid.UUID | None,
        event_time_start: object = None,
        event_time_end: object = None,
        context: dict[str, object],
    ) -> None:
        nonlocal position
        if position >= _MAX_BRIEF_REFERENCES:
            return
        position += 1
        anchor = f"field-ref-{position}"
        reference = BriefReference(
            id=uuid.uuid5(_BRIEF_REFERENCE_NAMESPACE, f"{brief.id}:{anchor}"),
            brief_id=brief.id,
            anchor=anchor,
            position=position,
            object_type=object_type,
            object_id=object_id,
            snapshot_id=snapshot_id,
            event_time_start=event_time_start,
            event_time_end=event_time_end,
            evidence_revision_id=evidence_revision_id,
            label=label,
            context=context,
        )
        session.add(reference)
        lines.append(f'<a id="{anchor}"></a> [{position}] {label}')

    # canonical 是简报事实引用的主索引；单类最多 12 条，为故事、原证据和来源记录保留空间。
    for canonical, revision in selected[:12]:
        label = str(canonical.title or canonical.canonical_id)
        add_reference(
            object_type="canonical",
            object_id=canonical.canonical_id,
            label=label,
            evidence_revision_id=revision.id,
            event_time_start=canonical.event_time_start,
            event_time_end=canonical.event_time_end,
            context={
                "source_video_id": str(revision.source_video_id),
                "time_basis": "event_occurrence",
                "web_url": (
                    f"/field?domain_id={brief.playlist_id}&canonical_id={canonical.canonical_id}"
                    f"&snapshot_id={snapshot_id}"
                ),
            },
        )

    selected_revision_ids = [revision.id for _, revision in selected]
    story_rows = session.execute(
        select(EventMapStory, EventMapStoryHistoryEvidence)
        .join(
            EventMapStoryHistoryEvidence,
            and_(
                EventMapStoryHistoryEvidence.snapshot_id == EventMapStory.snapshot_id,
                EventMapStoryHistoryEvidence.story_identity_id == EventMapStory.story_identity_id,
            ),
        )
        .where(
            EventMapStory.snapshot_id == snapshot_id,
            EventMapStoryHistoryEvidence.record_revision_id.in_(selected_revision_ids),
        )
        .order_by(EventMapStory.event_time_end.desc(), EventMapStory.story_id.asc())
    ).all()
    seen_stories: set[uuid.UUID] = set()
    for story, evidence in story_rows:
        identity_id = story.story_identity_id or story.story_id
        if identity_id in seen_stories or len(seen_stories) >= 4:
            continue
        seen_stories.add(identity_id)
        add_reference(
            object_type="story",
            object_id=identity_id,
            label=f"故事 · {story.title}",
            evidence_revision_id=evidence.record_revision_id,
            event_time_start=story.event_time_start,
            event_time_end=story.event_time_end,
            context={
                "edge_id": str(evidence.edge_id),
                "relation_type": evidence.relation_type,
                "time_basis": "event_occurrence_and_system_cognition",
                "web_url": (
                    f"/field?domain_id={brief.playlist_id}&mode=story&story_id={identity_id}"
                    f"&snapshot_id={snapshot_id}"
                ),
            },
        )

    for canonical, revision in selected[:4]:
        add_reference(
            object_type="evidence",
            object_id=revision.id,
            label=f"证据 · {canonical.title or canonical.canonical_id}",
            evidence_revision_id=revision.id,
            event_time_start=canonical.event_time_start,
            event_time_end=canonical.event_time_end,
            context={
                "source_video_id": str(revision.source_video_id),
                "time_basis": "event_occurrence",
                "web_url": (
                    f"/field?domain_id={brief.playlist_id}&mode=verify&evidence_id={revision.id}"
                    f"&snapshot_id={snapshot_id}"
                ),
            },
        )

    videos_by_id = {video.id: video for video in videos}
    seen_videos: set[uuid.UUID] = set()
    for canonical, revision in selected:
        video = videos_by_id.get(revision.source_video_id)
        if video is None or video.id in seen_videos or len(seen_videos) >= 4:
            continue
        seen_videos.add(video.id)
        add_reference(
            object_type="source",
            object_id=video.id,
            label=f"来源记录 · {video.title or video.id}",
            evidence_revision_id=revision.id,
            event_time_start=canonical.event_time_start,
            event_time_end=canonical.event_time_end,
            context={
                "media_id": str(video.media_id),
                "time_basis": "source_record",
                "web_url": f"/video?video_id={video.id}",
            },
        )
    return "\n".join(lines).strip(), position
