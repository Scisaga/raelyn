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


def _timestamp_column_sql(dialect_name: str) -> str:
    return "timestamptz" if dialect_name == "postgresql" else "datetime"


_STORY_MATERIAL_CHANGE_TYPES = (
    "story_added",
    "story_members_changed",
    "story_relations_changed",
    "story_evidence_changed",
    "story_correction_added",
    "story_maturity_changed",
)


def _first_row_by_partition(
    conn,
    *,
    table_name: str,
    partition_column: str,
    selected_columns: tuple[str, ...],
    order_by: str,
    where: str = "",
) -> dict[str, dict[str, Any]]:
    """用窗口函数一次取出每个故事身份的首行，避免迁移期逐身份查询。"""

    columns = ", ".join((partition_column, *selected_columns))
    where_sql = f"where {where}" if where else ""
    rows = conn.execute(
        text(
            f"""
select {columns}
from (
    select {columns},
           row_number() over (partition by {partition_column} order by {order_by}) as row_rank
    from {table_name}
    {where_sql}
) ranked
where row_rank = 1
"""
        )
    ).mappings()
    return {str(row[partition_column]): dict(row) for row in rows}


def _backfill_story_identity_reading_metadata(conn, *, tables: set[str]) -> None:
    """严格回填稳定标题与最后实质变化；绝不以空标题掩盖损坏身份。"""

    identity_columns = {
        column.get("name") for column in inspect(conn).get_columns("event_map_story_identity")
    }
    required_columns = {
        "stable_title",
        "last_material_snapshot_id",
        "last_material_changed_at",
    }
    if not required_columns.issubset(identity_columns):
        return

    history_by_identity: dict[str, dict[str, Any]] = {}
    if "event_map_story_history_revision" in tables:
        history_columns = {
            column.get("name")
            for column in inspect(conn).get_columns("event_map_story_history_revision")
        }
        if {
            "story_identity_id",
            "title",
            "snapshot_id",
            "observed_at",
            "id",
        }.issubset(history_columns):
            history_by_identity = _first_row_by_partition(
                conn,
                table_name="event_map_story_history_revision",
                partition_column="story_identity_id",
                selected_columns=("title", "snapshot_id", "observed_at"),
                order_by="observed_at asc, id asc",
            )

    story_by_identity: dict[str, dict[str, Any]] = {}
    if "event_map_story" in tables:
        story_columns = {
            column.get("name") for column in inspect(conn).get_columns("event_map_story")
        }
        if {
            "story_identity_id",
            "title",
            "snapshot_id",
            "created_at",
            "story_id",
        }.issubset(story_columns):
            story_by_identity = _first_row_by_partition(
                conn,
                table_name="event_map_story",
                partition_column="story_identity_id",
                selected_columns=("title", "snapshot_id", "created_at"),
                order_by="created_at asc, snapshot_id asc, story_id asc",
                where="story_identity_id is not null",
            )

    material_by_identity: dict[str, dict[str, Any]] = {}
    if "event_map_change" in tables:
        change_columns = {
            column.get("name") for column in inspect(conn).get_columns("event_map_change")
        }
        if {
            "object_id",
            "object_type",
            "change_type",
            "to_snapshot_id",
            "observed_at",
            "id",
        }.issubset(change_columns):
            quoted_types = ", ".join(f"'{value}'" for value in _STORY_MATERIAL_CHANGE_TYPES)
            material_by_identity = _first_row_by_partition(
                conn,
                table_name="event_map_change",
                partition_column="object_id",
                selected_columns=("to_snapshot_id", "observed_at"),
                order_by="observed_at desc, to_snapshot_id desc, id desc",
                where=f"object_type = 'story' and change_type in ({quoted_types})",
            )

    missing_active_titles: list[str] = []
    missing_other_titles: list[str] = []
    identity_rows = conn.execute(
        text(
            "select id, status, stable_title, created_snapshot_id, "
            "last_material_snapshot_id, last_material_changed_at, created_at "
            "from event_map_story_identity"
        )
    ).mappings()
    for identity in identity_rows:
        identity_key = str(identity["id"])
        first_history = history_by_identity.get(identity_key)
        first_story = story_by_identity.get(identity_key)
        stable_title = str(identity.get("stable_title") or "").strip()
        if not stable_title:
            source = first_history if first_history is not None else first_story
            stable_title = str((source or {}).get("title") or "").strip()
        if not stable_title:
            if str(identity.get("status") or "") == "active":
                missing_active_titles.append(identity_key)
            else:
                missing_other_titles.append(identity_key)
            continue

        material = material_by_identity.get(identity_key)
        formed = first_history if first_history is not None else first_story
        material_snapshot_id = identity.get("last_material_snapshot_id")
        material_changed_at = identity.get("last_material_changed_at")
        if material is not None:
            material_snapshot_id = material.get("to_snapshot_id")
            material_changed_at = material.get("observed_at")
        else:
            material_snapshot_id = material_snapshot_id or (formed or {}).get("snapshot_id")
            material_snapshot_id = material_snapshot_id or identity.get("created_snapshot_id")
            material_changed_at = material_changed_at or (formed or {}).get("observed_at")
            material_changed_at = material_changed_at or (formed or {}).get("created_at")
            material_changed_at = material_changed_at or identity.get("created_at")

        conn.execute(
            text(
                "update event_map_story_identity "
                "set stable_title = :stable_title, "
                "last_material_snapshot_id = :last_material_snapshot_id, "
                "last_material_changed_at = :last_material_changed_at "
                "where id = :identity_id"
            ),
            {
                "identity_id": identity["id"],
                "stable_title": stable_title,
                "last_material_snapshot_id": material_snapshot_id,
                "last_material_changed_at": material_changed_at,
            },
        )

    if missing_active_titles:
        sample = ", ".join(missing_active_titles[:10])
        raise RuntimeError(
            "active story identities cannot backfill stable_title; "
            f"missing earliest history/story title for: {sample}"
        )
    if missing_other_titles:
        sample = ", ".join(missing_other_titles[:10])
        raise RuntimeError(
            "story identities cannot satisfy non-null stable_title; "
            f"missing earliest history/story title for: {sample}"
        )


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
        "create index if not exists event_map_canonical_review_idx on event_map_canonical(snapshot_id, has_uncertainty, event_start_day)",
        "create index if not exists event_map_canonical_member_canonical_idx on event_map_canonical_member(snapshot_id, canonical_id)",
        "create index if not exists event_map_canonical_history_member_record_idx on event_map_canonical_history_member(playlist_id, record_revision_id, canonical_id)",
        "create index if not exists event_map_canonical_history_member_object_idx on event_map_canonical_history_member(playlist_id, canonical_id, snapshot_id)",
        "create index if not exists event_map_entity_index_key_idx on event_map_entity_index(snapshot_id, entity_type, normalized_key, point_index)",
        "create index if not exists event_map_topic_level_idx on event_map_topic(snapshot_id, level)",
        "create index if not exists event_map_topic_anchor_idx on event_map_topic(snapshot_id, anchor_canonical_id)",
        "create index if not exists event_map_topic_member_topic_idx on event_map_topic_member(snapshot_id, topic_id, level)",
        "create index if not exists event_map_topic_member_level_canonical_idx on event_map_topic_member(snapshot_id, level, canonical_id)",
        "create index if not exists event_map_story_member_canonical_idx on event_map_story_member(snapshot_id, canonical_id)",
        "create index if not exists event_map_story_edge_source_idx on event_map_story_edge(snapshot_id, source_canonical_id)",
        "create index if not exists event_map_story_edge_target_idx on event_map_story_edge(snapshot_id, target_canonical_id)",
        "create index if not exists event_map_story_edge_story_idx on event_map_story_edge(snapshot_id, story_id)",
        "create index if not exists event_map_story_identity_material_idx on event_map_story_identity(playlist_id, status, last_material_changed_at)",
        "create index if not exists event_map_story_quality_idx on event_map_story(snapshot_id, maturity, quality_score)",
        "create index if not exists event_map_story_history_evidence_record_idx on event_map_story_history_evidence(playlist_id, record_revision_id, story_identity_id)",
        "create index if not exists event_map_story_history_evidence_story_idx on event_map_story_history_evidence(playlist_id, story_identity_id, snapshot_id)",
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

    if "event_map_story" in tables:
        cols = {c.get("name") for c in insp.get_columns("event_map_story")}
        if "anchor_key" not in cols:
            conn.execute(text("alter table event_map_story add column anchor_key varchar"))
        if "maturity" not in cols:
            conn.execute(
                text("alter table event_map_story add column maturity varchar not null default 'legacy'")
            )
        if "quality_score" not in cols:
            conn.execute(text("alter table event_map_story add column quality_score real"))
        if "story_identity_id" not in cols:
            conn.execute(
                text(
                    "alter table event_map_story add column story_identity_id "
                    f"{_uuid_column_sql(conn.dialect.name)}"
                )
            )
        if "event_map_story_identity" in tables:
            # 旧快照的 story_id 只在快照内稳定；迁移时将它作为第一代稳定身份，
            # 后续构建再通过保守成员重叠匹配延续。
            identity_cols = {
                column.get("name")
                for column in inspect(conn).get_columns("event_map_story_identity")
            }
            insert_columns = [
                "id",
                "playlist_id",
                "status",
                "created_snapshot_id",
                "created_at",
                "updated_at",
            ]
            select_values = [
                "s.story_id",
                "p.playlist_id",
                "'active'",
                "s.snapshot_id",
                "s.created_at",
                "s.created_at",
            ]
            if "stable_title" in identity_cols:
                insert_columns.append("stable_title")
                select_values.append("s.title")
            if "last_material_snapshot_id" in identity_cols:
                insert_columns.append("last_material_snapshot_id")
                select_values.append("s.snapshot_id")
            if "last_material_changed_at" in identity_cols:
                insert_columns.append("last_material_changed_at")
                select_values.append("s.created_at")
            insert_column_sql = ", ".join(insert_columns)
            select_value_sql = ", ".join(select_values)
            if conn.dialect.name == "postgresql":
                conn.execute(
                    text(
                        f"""
insert into event_map_story_identity ({insert_column_sql})
select {select_value_sql}
from event_map_story s
join event_map_snapshot p on p.id = s.snapshot_id
where s.story_identity_id is null
on conflict (id) do nothing
"""
                    )
                )
            else:
                conn.execute(
                    text(
                        f"""
insert or ignore into event_map_story_identity ({insert_column_sql})
select {select_value_sql}
from event_map_story s
join event_map_snapshot p on p.id = s.snapshot_id
where s.story_identity_id is null
"""
                    )
                )
            conn.execute(
                text(
                    "update event_map_story set story_identity_id = story_id "
                    "where story_identity_id is null"
                )
            )
            _execute_best_effort_ddl(
                conn,
                "create unique index if not exists event_map_story_snapshot_identity_ux "
                "on event_map_story(snapshot_id, story_identity_id)",
            )
            _execute_best_effort_ddl(
                conn,
                "create index if not exists event_map_story_identity_idx "
                "on event_map_story(story_identity_id, snapshot_id)",
            )

    if "event_map_story_edge" in tables:
        cols = {c.get("name") for c in insp.get_columns("event_map_story_edge")}
        if "evidence_json" not in cols:
            column_type = "jsonb" if conn.dialect.name == "postgresql" else "json"
            conn.execute(text(f"alter table event_map_story_edge add column evidence_json {column_type}"))

    if "event_map_story_history_revision" in tables:
        cols = {c.get("name") for c in insp.get_columns("event_map_story_history_revision")}
        if "anchor_key" not in cols:
            conn.execute(text("alter table event_map_story_history_revision add column anchor_key varchar"))
        if "maturity" not in cols:
            conn.execute(
                text(
                    "alter table event_map_story_history_revision "
                    "add column maturity varchar not null default 'legacy'"
                )
            )
        if "quality_score" not in cols:
            conn.execute(text("alter table event_map_story_history_revision add column quality_score real"))

    if "event_map_story_identity" in tables:
        cols = {
            column.get("name")
            for column in inspect(conn).get_columns("event_map_story_identity")
        }
        if "stable_title" not in cols:
            conn.execute(text("alter table event_map_story_identity add column stable_title varchar"))
        if "last_material_snapshot_id" not in cols:
            conn.execute(
                text(
                    "alter table event_map_story_identity add column last_material_snapshot_id "
                    f"{_uuid_column_sql(conn.dialect.name)}"
                )
            )
        if "last_material_changed_at" not in cols:
            conn.execute(
                text(
                    "alter table event_map_story_identity add column last_material_changed_at "
                    f"{_timestamp_column_sql(conn.dialect.name)}"
                )
            )
        _backfill_story_identity_reading_metadata(conn, tables=tables)
        if conn.dialect.name == "postgresql":
            conn.execute(
                text(
                    "alter table event_map_story_identity "
                    "alter column stable_title set not null"
                )
            )
            conn.execute(
                text(
                    "alter table event_map_story_identity "
                    "alter column last_material_changed_at set not null"
                )
            )
        _execute_best_effort_ddl(
            conn,
            "create index if not exists event_map_story_identity_material_idx "
            "on event_map_story_identity(playlist_id, status, last_material_changed_at)",
        )

    if "event_map_canonical" in tables:
        cols = {c.get("name") for c in insp.get_columns("event_map_canonical")}
        if "has_uncertainty" not in cols:
            # 原位补齐热查询列；不重建快照，不取消任务，也不删除历史对象。
            conn.execute(text("alter table event_map_canonical add column has_uncertainty boolean"))
            json_length = (
                "jsonb_array_length(uncertainty_flags)"
                if conn.dialect.name == "postgresql"
                else "json_array_length(uncertainty_flags)"
            )
            conn.execute(
                text(
                    "update event_map_canonical "
                    f"set has_uncertainty = coalesce({json_length}, 0) > 0 "
                    "where has_uncertainty is null"
                )
            )
            if conn.dialect.name == "postgresql":
                conn.execute(text("alter table event_map_canonical alter column has_uncertainty set default false"))
                conn.execute(text("alter table event_map_canonical alter column has_uncertainty set not null"))

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
        if "observation_enabled" not in cols:
            default = "true" if conn.dialect.name == "postgresql" else "1"
            conn.execute(
                text(
                    "alter table playlist add column observation_enabled "
                    f"boolean not null default {default}"
                )
            )
        if "brief_granularity" not in cols:
            if conn.dialect.name == "postgresql":
                conn.execute(text("alter table playlist add column brief_granularity varchar not null default 'day'"))
            else:
                conn.execute(text("alter table playlist add column brief_granularity varchar not null default 'day'"))
        if "brief_prompt" not in cols:
            conn.execute(text("alter table playlist add column brief_prompt text"))

    if "brief" in tables:
        cols = {c.get("name") for c in insp.get_columns("brief")}
        if "snapshot_id" not in cols:
            conn.execute(
                text(
                    "alter table brief add column snapshot_id "
                    f"{_uuid_column_sql(conn.dialect.name)}"
                )
            )
        if "generation_basis" not in cols:
            column_type = "jsonb" if conn.dialect.name == "postgresql" else "json"
            conn.execute(text(f"alter table brief add column generation_basis {column_type}"))

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

        # 自动迁移不得取消或删除仍在队列中的历史任务；遗留链路只允许单独审计后人工治理。
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
        _execute_best_effort_ddl(conn, "create index if not exists video_created_at_idx on video(created_at)")

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
    with engine.connect() as conn:
        if dialect == "postgresql":
            conn.execute(text("select pg_advisory_lock(hashtext(:k))").bindparams(k=_CREATE_ALL_LOCK_KEY))
            # Advisory lock 是 session 级锁；先提交获取锁产生的隐式事务，
            # 后续才能显式划分 schema 与历史回填事务。
            conn.commit()
        try:
            # DDL 必须先独立提交。历史回填可能持续数分钟，如果把两者放进同一事务，
            # PostgreSQL 会一直持有 ALTER/CREATE 的关系锁并阻塞运行中的 worker 写入。
            with conn.begin():
                Base.metadata.create_all(bind=conn)
                _migrate_schema(conn)

            # V2 历史回填只读取仍保留的 ready 快照并写入新增表；不暂停、取消或删除任何任务与媒体数据。
            from raelyn.services.event_map_snapshot import bootstrap_v2_event_map_history

            history_session = Session(bind=conn, autoflush=False, expire_on_commit=False)
            try:
                bootstrap_v2_event_map_history(history_session)
                history_session.commit()
            except Exception:
                history_session.rollback()
                raise
            finally:
                history_session.close()
        finally:
            if dialect == "postgresql":
                if conn.in_transaction():
                    conn.rollback()
                conn.execute(text("select pg_advisory_unlock(hashtext(:k))").bindparams(k=_CREATE_ALL_LOCK_KEY))
                conn.commit()


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
