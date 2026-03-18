from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy import inspect
from sqlalchemy.orm import Session, sessionmaker

from raelyn.config import settings


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, class_=Session)

_CREATE_ALL_LOCK_KEY = "raelyn.schema.create_all"


def _uuid_column_sql(dialect_name: str) -> str:
    return "uuid" if dialect_name == "postgresql" else "varchar(36)"


def _guess_asset_format_from_key(key: str) -> str:
    ext = Path(str(key or "").strip()).suffix.lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    return ext or "bin"


def _timestamp_sql(dialect_name: str) -> str:
    return "now()" if dialect_name == "postgresql" else "CURRENT_TIMESTAMP"


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
        try:
            conn.execute(text("drop index if exists job_brief_dedupe_active_ux"))
        except Exception:
            pass
        try:
            conn.execute(
                text(
                    "create unique index if not exists job_brief_dedupe_pending_ux "
                    "on job(dedupe_key) "
                    "where dedupe_key is not null and status = 'pending'"
                )
            )
        except Exception:
            # Best-effort: some dialects/versions may not support partial indexes.
            pass

    # Worker heartbeats: add optional metadata columns (role) for UI observability.
    if "worker_heartbeat" in tables:
        cols = {c.get("name") for c in insp.get_columns("worker_heartbeat")}
        if "role" not in cols:
            try:
                conn.execute(text("alter table worker_heartbeat add column role varchar"))
            except Exception:
                pass

    # Query performance indexes (best-effort).
    if "video" in tables:
        try:
            if conn.dialect.name == "postgresql":
                conn.execute(
                    text(
                        "create index if not exists video_media_published_at_idx "
                        "on video(media_id, published_at desc) "
                        "where published_at is not null"
                    )
                )
            else:
                conn.execute(text("create index if not exists video_media_published_at_idx on video(media_id, published_at)"))
        except Exception:
            pass

    if "asset" in tables:
        try:
            conn.execute(text("create index if not exists asset_video_created_at_idx on asset(video_id, created_at desc)"))
        except Exception:
            pass

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
        try:
            if conn.dialect.name == "postgresql":
                conn.execute(
                    text(
                        "create index if not exists asset_transcript_pick_idx "
                        "on asset(video_id, variant, source, language, created_at desc) "
                        "where type = 'transcript' and format = 'txt'"
                    )
                )
        except Exception:
            pass


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
