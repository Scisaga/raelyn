"""把旧简报的实际引用冻结到引用行，并移除星域全量历史。

先完成 compact_event_map_history 的引用备份和当前数据部署，再运行：
PYTHONPATH=backend ./.venv/bin/python -m raelyn.tools.finalize_current_event_map
"""

from sqlalchemy import text

from raelyn.db import engine


STORY_REFERENCE_CONTEXT_SQL = """
update brief_reference set context = context || jsonb_build_object(
    'source_canonical_id', context->'quoted_object'->'relation'->'source_canonical_id',
    'target_canonical_id', context->'quoted_object'->'relation'->'target_canonical_id',
    'focus_canonical_id', context->'quoted_object'->'relation'->'target_canonical_id'
)
where object_type = 'story' and context->'quoted_object'->'relation'->>'target_canonical_id' is not null
  and nullif(context->>'focus_canonical_id', '') is null
"""


def main() -> None:
    with engine.connect() as conn:
        conn.execute(text("set local lock_timeout = '15s'"))
        conn.execute(text("set local statement_timeout = '5min'"))
        active = conn.scalar(text("select count(*) from job where status = 'running' and type = 'playlist.build_event_map_snapshot'"))
        if active:
            raise RuntimeError("请先停止星域构建任务")
        before = conn.scalar(text("select count(*) from brief_reference"))
        conn.execute(text("""
update brief_reference reference set context = coalesce(reference.context, '{}'::jsonb) || jsonb_build_object(
    'quoted_object', jsonb_build_object(
        'type', 'canonical', 'title', archive.revision->>'title', 'summary', archive.revision->>'summary',
        'event_type', archive.revision->>'event_type',
        'event_time_start', archive.revision->'event_time_start', 'event_time_end', archive.revision->'event_time_end'
    )
)
from event_map_canonical_history_revision archive
where reference.object_type = 'canonical' and reference.snapshot_id = archive.snapshot_id
  and reference.object_id = archive.canonical_id
  and not (coalesce(reference.context, '{}'::jsonb) ? 'quoted_object')
"""))
        conn.execute(text("""
update brief_reference reference set context = coalesce(reference.context, '{}'::jsonb) || jsonb_build_object(
    'quoted_object', jsonb_build_object(
        'type', 'story', 'title', archive.title, 'summary', archive.summary, 'maturity', archive.maturity,
        'event_time_start', archive.event_time_start, 'event_time_end', archive.event_time_end,
        'relation', (
            select edge - 'score' from jsonb_array_elements(archive.edges) edge
            where edge->>'edge_id' = reference.context->>'edge_id'
            limit 1
        )
    )
)
from event_map_story_history_revision archive
where reference.object_type = 'story' and reference.snapshot_id = archive.snapshot_id
  and reference.object_id = archive.story_identity_id
  and not (coalesce(reference.context, '{}'::jsonb) ? 'quoted_object')
"""))
        conn.execute(text(STORY_REFERENCE_CONTEXT_SQL))
        conn.execute(text("""
update brief_reference reference set context = coalesce(reference.context, '{}'::jsonb) || jsonb_build_object(
    'source_video_id', revision.source_video_id::text,
    'web_url', case reference.object_type
        when 'canonical' then '/field?domain_id=' || brief.playlist_id || '&canonical_id=' || reference.object_id
        when 'story' then '/stories?domain_id=' || brief.playlist_id || '&story_id=' || reference.object_id
        when 'evidence' then '/field?domain_id=' || brief.playlist_id || '&mode=verify&evidence_id=' || reference.object_id
        else '/video?video_id=' || revision.source_video_id end
)
from event_map_record_revision revision, brief
where revision.id = reference.evidence_revision_id and brief.id = reference.brief_id
"""))
        missing = conn.scalar(text("""
select count(*) from brief_reference reference
left join event_map_record_revision revision on revision.id = reference.evidence_revision_id
where reference.object_type in ('canonical','story') and (
    nullif(reference.context->'quoted_object'->>'title', '') is null
    or revision.id is null
    or reference.context->>'source_video_id' is distinct from revision.source_video_id::text
    or (reference.object_type = 'story' and reference.context->>'edge_id' is not null
        and reference.context->'quoted_object'->'relation'->>'edge_id' is null)
)
"""))
        if missing:
            raise RuntimeError(f"引用迁移未通过完整性校验：{missing}")
        if conn.scalar(text("select count(*) from brief_reference")) != before:
            raise RuntimeError("引用行数发生变化")
        conn.execute(text("""
drop table event_map_canonical_history_member, event_map_story_history_evidence,
    event_map_canonical_history_revision, event_map_story_history_revision, event_map_change
"""))
        conn.execute(text("alter table domain_observation_cursor drop column last_change_id"))
        conn.commit()
        print({"已迁移引用": before, "已移除历史表": 5})


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print({"清理失败类型": type(exc).__name__})
        raise SystemExit(1)
