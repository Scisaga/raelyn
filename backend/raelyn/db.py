from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import re
from typing import Any
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy import inspect
from sqlalchemy.orm import Session, sessionmaker

from raelyn.config import settings


def _database_engine_kwargs(
    database_url: str,
    *,
    pool_size: int,
    max_overflow: int,
    pool_timeout_seconds: int,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if make_url(database_url).get_backend_name() == "sqlite":
        return kwargs

    kwargs.update(
        {
            "pool_size": max(1, int(pool_size or 0)),
            "max_overflow": max(0, int(max_overflow or 0)),
            "pool_timeout": max(1, int(pool_timeout_seconds or 0)),
        }
    )
    return kwargs


engine = create_engine(
    settings.database_url,
    **_database_engine_kwargs(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout_seconds=settings.database_pool_timeout_seconds,
    ),
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, class_=Session)

_CREATE_ALL_LOCK_KEY = "raelyn.schema.create_all"
_BEST_EFFORT_DDL_LOCK_TIMEOUT = "2s"
_CREATE_INDEX_IF_MISSING_RE = re.compile(
    r"^\s*create\s+(?:unique\s+)?index\s+if\s+not\s+exists\s+([a-zA-Z_][a-zA-Z0-9_]*)\b",
    re.IGNORECASE,
)


def _uuid_column_sql(dialect_name: str) -> str:
    return "uuid" if dialect_name == "postgresql" else "varchar(36)"


def _guess_asset_format_from_key(key: str) -> str:
    ext = Path(str(key or "").strip()).suffix.lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    return ext or "bin"


def _timestamp_sql(dialect_name: str) -> str:
    return "now()" if dialect_name == "postgresql" else "CURRENT_TIMESTAMP"


def _execute_best_effort_ddl(conn, statement: str) -> None:
    try:
        match = _CREATE_INDEX_IF_MISSING_RE.match(statement)
        if match and _database_index_exists(conn, match.group(1)):
            return
        if conn.dialect.name == "postgresql":
            with conn.begin_nested():
                conn.execute(text(f"set local lock_timeout = '{_BEST_EFFORT_DDL_LOCK_TIMEOUT}'"))
                conn.execute(text(statement))
                conn.execute(text("set local lock_timeout = '0'"))
        else:
            conn.execute(text(statement))
    except Exception:
        pass


def _database_index_exists(conn, index_name: str) -> bool:
    if conn.dialect.name == "postgresql":
        statement = text(
            "select 1 from pg_class "
            "where relkind in ('i', 'I') and relname = :index_name "
            "and pg_table_is_visible(oid) limit 1"
        )
    elif conn.dialect.name == "sqlite":
        statement = text(
            "select 1 from sqlite_master "
            "where type = 'index' and name = :index_name limit 1"
        )
    else:
        return False
    return conn.execute(statement, {"index_name": index_name}).scalar_one_or_none() is not None


def _postgres_table_has_analyze_options(conn, table_name: str) -> bool:
    if conn.dialect.name != "postgresql":
        return False
    options = conn.execute(
        text("select reloptions from pg_class where oid = to_regclass(:table_name)"),
        {"table_name": table_name},
    ).scalar_one_or_none()
    values = set(options or [])
    return {
        "autovacuum_analyze_scale_factor=0.005",
        "autovacuum_analyze_threshold=500",
    }.issubset(values)


def _create_job_query_indexes(conn) -> None:
    if conn.dialect.name == "postgresql":
        statements = [
            (
                "create index if not exists job_pending_type_schedule_idx "
                "on job(type, scheduled_for, priority desc, created_at desc, id desc) "
                "where status = 'pending'"
            ),
            (
                "create index if not exists job_pending_schedule_idx "
                "on job(scheduled_for, priority desc, created_at desc, id desc) "
                "where status = 'pending'"
            ),
            (
                "create index if not exists job_pending_claim_order_idx "
                "on job("
                "priority desc, "
                "(case "
                "when ((type)::text = 'video.asr_transcribe'::text) then 0 "
                "when ((type)::text = 'video.normalize_subtitle'::text) then 1 "
                "when ((type)::text = 'video.extract_audio'::text) then 2 "
                "else 10 end), "
                "scheduled_for asc, created_at desc, id desc"
                ") "
                "where status = 'pending'"
            ),
            (
                "create index if not exists job_active_list_order_idx "
                "on job("
                "(case when ((status)::text = 'running'::text) then 0 else 1 end), "
                "started_at asc nulls last, scheduled_for asc, created_at asc"
                ") "
                "where status in ('pending', 'running')"
            ),
            "create index if not exists job_status_type_idx on job(status, type)",
            (
                "create index if not exists job_finished_status_finished_at_idx "
                "on job(status, finished_at desc, created_at desc) "
                "where finished_at is not null"
            ),
            (
                "create index if not exists job_running_lease_idx "
                "on job(lease_expires_at) "
                "where status = 'running' and lease_expires_at is not null"
            ),
            (
                "create index if not exists job_running_worker_idx "
                "on job(worker_id) "
                "where status = 'running' and worker_id is not null"
            ),
        ]
    else:
        statements = [
            "create index if not exists job_pending_type_schedule_idx on job(status, type, scheduled_for, priority, created_at, id)",
            "create index if not exists job_pending_schedule_idx on job(status, scheduled_for, priority, created_at, id)",
            "create index if not exists job_pending_claim_order_idx on job(status, priority, scheduled_for, created_at, id)",
            "create index if not exists job_active_list_order_idx on job(status, started_at, scheduled_for, created_at)",
            "create index if not exists job_status_type_idx on job(status, type)",
            "create index if not exists job_finished_status_finished_at_idx on job(status, finished_at, created_at)",
            "create index if not exists job_running_lease_idx on job(status, lease_expires_at)",
            "create index if not exists job_running_worker_idx on job(status, worker_id)",
        ]

    for statement in statements:
        _execute_best_effort_ddl(conn, statement)


_LEGACY_ANALYSIS_JOB_TYPES = (
    "video.embed_transcript",
    "playlist.backfill_embeddings",
    "playlist.build_analysis_snapshot",
)

_LEGACY_ANALYSIS_TABLES = (
    "playlist_analysis_signal",
    "playlist_analysis_period",
    "playlist_analysis_candidate",
    "playlist_analysis_state",
    "playlist_analysis_run",
    "video_embedding",
)


def _cancel_legacy_analysis_jobs(conn) -> None:
    placeholders = ", ".join([f":t{i}" for i in range(len(_LEGACY_ANALYSIS_JOB_TYPES))])
    params = {f"t{i}": value for i, value in enumerate(_LEGACY_ANALYSIS_JOB_TYPES)}
    rows = conn.execute(
        text(
            f"""
select id
from job
where type in ({placeholders})
  and status in ('pending', 'running')
"""
        ),
        params,
    ).mappings().all()
    if not rows:
        return
    conn.execute(
        text(
            f"""
update job
set status = 'canceled',
    error_message = 'legacy transcript embedding / playlist analysis chain removed',
    finished_at = coalesce(finished_at, {_timestamp_sql(conn.dialect.name)}),
    lease_expires_at = null,
    worker_id = null,
    execution_token = null
where type in ({placeholders})
  and status in ('pending', 'running')
"""
        ),
        params,
    )

    for row in rows:
        data_expr = "cast(:data as jsonb)" if conn.dialect.name == "postgresql" else ":data"
        conn.execute(
            text(
                f"""
insert into job_event (job_id, ts, level, message, data)
values (:job_id, {_timestamp_sql(conn.dialect.name)}, 'warning', 'legacy analysis job canceled by event-regime migration', {data_expr})
"""
            ),
            {
                "job_id": row["id"],
                "data": json.dumps({"reason": "legacy_analysis_removed"}),
            },
        )


def _drop_legacy_analysis_tables(conn) -> None:
    for table_name in _LEGACY_ANALYSIS_TABLES:
        suffix = " cascade" if conn.dialect.name == "postgresql" else ""
        _execute_best_effort_ddl(conn, f"drop table if exists {table_name}{suffix}")


def _create_event_analysis_indexes(conn) -> None:
    statements = [
        "create index if not exists market_event_source_video_idx on market_event(source_video_id)",
        "create index if not exists market_event_status_time_idx on market_event(status, event_time_start)",
        "create index if not exists market_event_type_time_idx on market_event(event_type, event_time_start)",
        "create index if not exists market_event_available_at_idx on market_event(available_at)",
        "create index if not exists market_event_evidence_event_idx on market_event_evidence(event_id)",
        "create index if not exists market_event_evidence_video_idx on market_event_evidence(video_id)",
        "create index if not exists market_event_entity_event_idx on market_event_entity(event_id)",
        "create index if not exists market_event_entity_key_idx on market_event_entity(entity_type, normalized_key)",
        "create index if not exists market_event_relation_event_idx on market_event_relation(event_id)",
        "create index if not exists market_event_embedding_event_idx on market_event_embedding(event_id)",
        "create index if not exists market_event_embedding_status_idx on market_event_embedding(status, embedding_model, embedding_dim)",
        "create index if not exists video_event_extraction_run_video_idx on video_event_extraction_run(video_id)",
        "create index if not exists video_event_extraction_run_status_idx on video_event_extraction_run(status, updated_at)",
        "create index if not exists event_map_snapshot_playlist_status_idx on event_map_snapshot(playlist_id, status, created_at desc)",
        "create index if not exists event_map_snapshot_playlist_generation_idx on event_map_snapshot(playlist_id, input_generation desc)",
        "create index if not exists event_map_snapshot_parent_idx on event_map_snapshot(parent_snapshot_id)",
        "create unique index if not exists event_map_snapshot_ready_build_ux on event_map_snapshot(playlist_id, build_key) where status = 'ready' and build_key <> ''",
        "create index if not exists event_map_state_current_snapshot_idx on event_map_state(current_snapshot_id)",
        "create index if not exists event_map_record_revision_event_idx on event_map_record_revision(event_id)",
        "create index if not exists event_map_canonical_identity_created_snapshot_idx on event_map_canonical_identity(created_snapshot_id)",
        "create index if not exists event_map_canonical_identity_retired_snapshot_idx on event_map_canonical_identity(retired_snapshot_id)",
        "create index if not exists event_map_canonical_time_idx on event_map_canonical(snapshot_id, event_start_day, event_end_day)",
        "create index if not exists event_map_canonical_type_idx on event_map_canonical(snapshot_id, event_type_code, point_index)",
        "create index if not exists event_map_canonical_member_canonical_idx on event_map_canonical_member(snapshot_id, canonical_id)",
        "create index if not exists event_map_entity_index_key_idx on event_map_entity_index(snapshot_id, entity_type, normalized_key, point_index)",
        "create index if not exists event_map_topic_level_idx on event_map_topic(snapshot_id, level)",
        "create index if not exists event_map_topic_anchor_idx on event_map_topic(snapshot_id, anchor_canonical_id)",
        "create index if not exists event_map_topic_member_topic_idx on event_map_topic_member(snapshot_id, topic_id, level)",
        "create index if not exists event_map_topic_member_level_canonical_idx on event_map_topic_member(snapshot_id, level, canonical_id)",
        "create index if not exists event_map_story_member_canonical_idx on event_map_story_member(snapshot_id, canonical_id)",
        "create index if not exists event_map_story_edge_source_idx on event_map_story_edge(snapshot_id, source_canonical_id)",
        "create index if not exists event_map_story_edge_target_idx on event_map_story_edge(snapshot_id, target_canonical_id)",
        "create index if not exists event_map_story_edge_story_idx on event_map_story_edge(snapshot_id, story_id)",
    ]
    if conn.dialect.name == "postgresql":
        for table_name in ("event_map_canonical", "event_map_entity_index"):
            if not _postgres_table_has_analyze_options(conn, table_name):
                statements.append(
                    f"alter table {table_name} set ("
                    "autovacuum_analyze_scale_factor = 0.005, "
                    "autovacuum_analyze_threshold = 500)"
                )
    for statement in statements:
        _execute_best_effort_ddl(conn, statement)


def _backfill_owned_image_assets(conn, *, owner_table: str, owner_id_col: str, asset_id_col: str, key_col: str, variant: str) -> None:
    try:
        rows = conn.execute(
            text(
                f"""
select {owner_id_col} as owner_id, {key_col} as s3_key
from {owner_table}
where {asset_id_col} is null
  and {key_col} is not null
  and trim({key_col}) <> ''
"""
            )
        ).mappings().all()
    except Exception:
        rows = []
    if not rows:
        return

    ts_sql = _timestamp_sql(conn.dialect.name)
    insert_sql = text(
        f"""
insert into asset (
    id, video_id, type, format, language, source, variant,
    s3_bucket, s3_key, size_bytes, checksum_sha256, metadata, created_at, updated_at
) values (
    :id, null, 'image', :format, null, :source, :variant,
    :bucket, :key, null, null, null, {ts_sql}, {ts_sql}
)
"""
    )
    update_sql = text(f"update {owner_table} set {asset_id_col} = :asset_id where {owner_id_col} = :owner_id")
    for row in rows:
        s3_key = str(row.get("s3_key") or "").strip()
        owner_id = row.get("owner_id")
        if not owner_id or not s3_key:
            continue
        asset_id = str(uuid.uuid4())
        conn.execute(
            insert_sql,
            {
                "id": asset_id,
                "format": _guess_asset_format_from_key(s3_key),
                "source": owner_table,
                "variant": variant,
                "bucket": settings.s3_bucket,
                "key": s3_key,
            },
        )
        conn.execute(update_sql, {"asset_id": asset_id, "owner_id": owner_id})


def _migrate_schema(conn) -> None:
    insp = inspect(conn)
    tables = set(insp.get_table_names())

    if "event_map_canonical" in tables:
        cols = {c.get("name") for c in insp.get_columns("event_map_canonical")}
        if "z" not in cols:
            conn.execute(text("alter table event_map_canonical add column z real not null default 0"))

    if "event_map_projection_anchor" in tables:
        cols = {c.get("name") for c in insp.get_columns("event_map_projection_anchor")}
        if "z" not in cols:
            conn.execute(text("alter table event_map_projection_anchor add column z real not null default 0"))

    if "event_map_topic" in tables:
        cols = {c.get("name") for c in insp.get_columns("event_map_topic")}
        for column_name in ("center_x", "center_y", "center_z", "radius"):
            if column_name not in cols:
                conn.execute(
                    text(
                        f"alter table event_map_topic add column {column_name} "
                        "real not null default 0"
                    )
                )
        if "label_x" in cols:
            conn.execute(text("update event_map_topic set center_x = label_x"))
        if "label_y" in cols:
            conn.execute(text("update event_map_topic set center_y = label_y"))
        for legacy_column in ("label_x", "label_y", "geometry"):
            if legacy_column in cols:
                _execute_best_effort_ddl(
                    conn,
                    f"alter table event_map_topic drop column {legacy_column}",
                )

    if "event_map_snapshot" in tables:
        cols = {c.get("name") for c in insp.get_columns("event_map_snapshot")}
        if "monthly_distribution" not in cols:
            column_type = "jsonb" if conn.dialect.name == "postgresql" else "json"
            conn.execute(text(f"alter table event_map_snapshot add column monthly_distribution {column_type}"))
        if "entity_count" not in cols:
            conn.execute(text("alter table event_map_snapshot add column entity_count integer not null default 0"))
        if "execution_token" not in cols:
            conn.execute(
                text(
                    f"alter table event_map_snapshot add column execution_token "
                    f"{_uuid_column_sql(conn.dialect.name)}"
                )
            )
        if conn.dialect.name == "postgresql":
            _execute_best_effort_ddl(
                conn,
                "alter table event_map_snapshot drop constraint if exists event_map_snapshot_job_attempt_ux",
            )
            _execute_best_effort_ddl(
                conn,
                "alter table event_map_snapshot add constraint event_map_snapshot_job_execution_ux "
                "unique (job_id, execution_token)",
            )
        else:
            _execute_best_effort_ddl(
                conn,
                "create unique index if not exists event_map_snapshot_job_execution_ux "
                "on event_map_snapshot(job_id, execution_token)",
            )
        if "lod_node_count" in cols:
            _execute_best_effort_ddl(
                conn,
                "alter table event_map_snapshot drop column lod_node_count",
            )

    for table_name in ("event_map_lod_member", "event_map_lod_node"):
        suffix = " cascade" if conn.dialect.name == "postgresql" else ""
        _execute_best_effort_ddl(conn, f"drop table if exists {table_name}{suffix}")

    if "asset" in tables:
        cols = {c.get("name") for c in insp.get_columns("asset")}
        if "updated_at" not in cols:
            if conn.dialect.name == "postgresql":
                conn.execute(text("alter table asset add column updated_at timestamptz"))
                conn.execute(text("update asset set updated_at = created_at where updated_at is null"))
                conn.execute(text("alter table asset alter column updated_at set default now()"))
                conn.execute(text("alter table asset alter column updated_at set not null"))
            else:
                conn.execute(text("alter table asset add column updated_at datetime"))
                conn.execute(text("update asset set updated_at = created_at where updated_at is null"))

    if "media" in tables:
        cols = {c.get("name") for c in insp.get_columns("media")}
        if "monitor_enabled" not in cols:
            if conn.dialect.name == "postgresql":
                conn.execute(text("alter table media add column monitor_enabled boolean not null default false"))
            else:
                conn.execute(text("alter table media add column monitor_enabled boolean not null default 0"))
        if "avatar_s3_key" not in cols:
            conn.execute(text("alter table media add column avatar_s3_key varchar"))
        if "avatar_asset_id" not in cols:
            conn.execute(text(f"alter table media add column avatar_asset_id {_uuid_column_sql(conn.dialect.name)}"))

    if "playlist" in tables:
        cols = {c.get("name") for c in insp.get_columns("playlist")}
        if "avatar_s3_key" not in cols:
            conn.execute(text("alter table playlist add column avatar_s3_key varchar"))
        if "background_s3_key" not in cols:
            conn.execute(text("alter table playlist add column background_s3_key varchar"))
        if "avatar_asset_id" not in cols:
            conn.execute(text(f"alter table playlist add column avatar_asset_id {_uuid_column_sql(conn.dialect.name)}"))
        if "background_asset_id" not in cols:
            conn.execute(text(f"alter table playlist add column background_asset_id {_uuid_column_sql(conn.dialect.name)}"))
        if "brief_granularity" not in cols:
            if conn.dialect.name == "postgresql":
                conn.execute(text("alter table playlist add column brief_granularity varchar not null default 'day'"))
            else:
                conn.execute(text("alter table playlist add column brief_granularity varchar not null default 'day'"))
        if "brief_prompt" not in cols:
            conn.execute(text("alter table playlist add column brief_prompt text"))

    # Migrate legacy daily_brief -> brief (day granularity).
    if "daily_brief" in tables and "brief" in tables:
        if conn.dialect.name == "postgresql":
            conn.execute(
                text(
                    """
insert into brief (id, playlist_id, granularity, period_start, status, markdown_asset_id, error_message, created_at, updated_at)
select id, playlist_id, 'day', brief_date, status, markdown_asset_id, error_message, created_at, updated_at
from daily_brief
on conflict (playlist_id, granularity, period_start) do nothing
"""
                )
            )
        else:
            conn.execute(
                text(
                    """
insert or ignore into brief (id, playlist_id, granularity, period_start, status, markdown_asset_id, error_message, created_at, updated_at)
select id, playlist_id, 'day', brief_date, status, markdown_asset_id, error_message, created_at, updated_at
from daily_brief
"""
                )
            )

    # Migrate legacy global prompt (app_config.briefs.daily_prompt) -> per-playlist prompt (only if unset).
    if "app_config" in tables and "playlist" in tables:
        try:
            raw = conn.execute(text("select value from app_config where key = 'briefs'")).scalar_one_or_none()
        except Exception:
            raw = None
        daily_prompt = ""
        try:
            if isinstance(raw, dict):
                daily_prompt = str(raw.get("daily_prompt") or "").strip()
            elif isinstance(raw, str) and raw.strip():
                v = json.loads(raw)
                if isinstance(v, dict):
                    daily_prompt = str(v.get("daily_prompt") or "").strip()
        except Exception:
            daily_prompt = ""
        if daily_prompt:
            try:
                conn.execute(
                    text("update playlist set brief_prompt = :p where brief_prompt is null").bindparams(p=daily_prompt)
                )
            except Exception:
                # Best-effort; ignore if dialect doesn't support the above semantics.
                pass

    # Migrate legacy pending download jobs (video.download) -> provider-specific queues.
    # This keeps download isolation after introducing video.download.youtube / video.download.bilibili.
    if "job" in tables and "video" in tables:
        if conn.dialect.name == "postgresql":
            try:
                conn.execute(
                    text(
                        """
update job
set type = case
  when v.provider = 'youtube' then 'video.download.youtube'
  when v.provider = 'bilibili' then 'video.download.bilibili'
  else job.type
end
from video v
where job.type = 'video.download'
  and job.status = 'pending'
  and (job.params->>'video_id') is not null
  and v.id::text = (job.params->>'video_id')
  and v.provider in ('youtube', 'bilibili')
"""
                    )
                )
            except Exception:
                pass

    # Brief job dedupe: keep at most one pending job per normalized brief key.
    if "job" in tables:
        cols = {c.get("name") for c in insp.get_columns("job")}
        if "dedupe_key" not in cols:
            conn.execute(text("alter table job add column dedupe_key varchar"))
        if "cancel_requested_at" not in cols:
            if conn.dialect.name == "postgresql":
                conn.execute(text("alter table job add column cancel_requested_at timestamptz"))
            else:
                conn.execute(text("alter table job add column cancel_requested_at datetime"))
        if "execution_token" not in cols:
            conn.execute(
                text(
                    f"alter table job add column execution_token "
                    f"{_uuid_column_sql(conn.dialect.name)}"
                )
            )
        _execute_best_effort_ddl(conn, "drop index if exists job_brief_dedupe_active_ux")
        _execute_best_effort_ddl(
            conn,
            (
                "create unique index if not exists job_brief_dedupe_pending_ux "
                "on job(dedupe_key) "
                "where dedupe_key is not null and status = 'pending'"
            )
        )
        _create_job_query_indexes(conn)

        try:
            _cancel_legacy_analysis_jobs(conn)
        except Exception:
            pass
        _drop_legacy_analysis_tables(conn)
        tables = set(inspect(conn).get_table_names())
    # Worker heartbeats: add optional metadata columns (role) for UI observability.
    if "worker_heartbeat" in tables:
        cols = {c.get("name") for c in insp.get_columns("worker_heartbeat")}
        if "role" not in cols:
            try:
                conn.execute(text("alter table worker_heartbeat add column role varchar"))
            except Exception:
                pass
        if "active_at" not in cols:
            try:
                conn.execute(text("alter table worker_heartbeat add column active_at timestamptz"))
                conn.execute(text("update worker_heartbeat set active_at = updated_at where active_at is null"))
            except Exception:
                pass
        if "current_job_id" not in cols:
            try:
                conn.execute(text(f"alter table worker_heartbeat add column current_job_id {_uuid_column_sql(conn.dialect.name)}"))
            except Exception:
                pass

    if "market_event" in tables and "video_event_extraction_run" in tables:
        _create_event_analysis_indexes(conn)

    if "video_time_evidence" in tables:
        _execute_best_effort_ddl(conn, "create index if not exists video_time_evidence_video_id_idx on video_time_evidence(video_id)")
        _execute_best_effort_ddl(
            conn,
            (
                "create index if not exists video_time_evidence_role_status_date_idx "
                "on video_time_evidence(time_role, status, date_year, date_month, date_day)"
            ),
        )
        _execute_best_effort_ddl(
            conn,
            (
                "create unique index if not exists video_time_evidence_one_accepted_role_ux "
                "on video_time_evidence(video_id, time_role) "
                "where status = 'accepted'"
            ),
        )

    if "video" in tables:
        _execute_best_effort_ddl(conn, "create index if not exists video_media_id_idx on video(media_id)")

    # Query performance indexes (best-effort).
    if "video" in tables:
        if conn.dialect.name == "postgresql":
            _execute_best_effort_ddl(
                conn,
                (
                    "create index if not exists video_media_published_at_idx "
                    "on video(media_id, published_at desc) "
                    "where published_at is not null"
                ),
            )
        else:
            _execute_best_effort_ddl(conn, "create index if not exists video_media_published_at_idx on video(media_id, published_at)")

    if "asset" in tables:
        _execute_best_effort_ddl(conn, "create index if not exists asset_video_created_at_idx on asset(video_id, created_at desc)")
        if conn.dialect.name == "postgresql":
            _execute_best_effort_ddl(conn, "create index if not exists asset_playback_video_idx on asset(video_id) where type = 'video'")
        else:
            _execute_best_effort_ddl(conn, "create index if not exists asset_playback_video_idx on asset(video_id, type)")

    if "asset" in tables and "media" in tables:
        _backfill_owned_image_assets(
            conn,
            owner_table="media",
            owner_id_col="id",
            asset_id_col="avatar_asset_id",
            key_col="avatar_s3_key",
            variant="avatar",
        )
    if "asset" in tables and "playlist" in tables:
        _backfill_owned_image_assets(
            conn,
            owner_table="playlist",
            owner_id_col="id",
            asset_id_col="avatar_asset_id",
            key_col="avatar_s3_key",
            variant="avatar",
        )
        _backfill_owned_image_assets(
            conn,
            owner_table="playlist",
            owner_id_col="id",
            asset_id_col="background_asset_id",
            key_col="background_s3_key",
            variant="background",
        )
        if conn.dialect.name == "postgresql":
            _execute_best_effort_ddl(
                conn,
                (
                    "create index if not exists asset_transcript_pick_idx "
                    "on asset(video_id, variant, source, language, created_at desc) "
                    "where type = 'transcript' and format = 'txt'"
                ),
            )


def init_db() -> None:
    # Multiple processes (api/worker/scheduler) may call this at startup; serialize to avoid
    # Postgres catalog races during CREATE TABLE.
    from raelyn.models import Base  # local import to avoid import cycles at module load

    dialect = engine.dialect.name
    with engine.begin() as conn:
        if dialect == "postgresql":
            conn.execute(text("select pg_advisory_lock(hashtext(:k))").bindparams(k=_CREATE_ALL_LOCK_KEY))
        try:
            Base.metadata.create_all(bind=conn)
            _migrate_schema(conn)
        finally:
            if dialect == "postgresql":
                conn.execute(text("select pg_advisory_unlock(hashtext(:k))").bindparams(k=_CREATE_ALL_LOCK_KEY))


@contextmanager
def session_scope() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
