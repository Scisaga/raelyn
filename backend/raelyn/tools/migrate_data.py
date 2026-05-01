from __future__ import annotations

import argparse
import concurrent.futures
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import threading
import sys
import tempfile
import time
from typing import Any, Iterable

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config as BotoConfig
import psycopg
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from raelyn.models import Base  # noqa: E402


_REQUIRED_DB_KEYS = ("DATABASE_URL",)
_REQUIRED_S3_KEYS = (
    "S3_ENDPOINT",
    "S3_ACCESS_KEY",
    "S3_SECRET_KEY",
    "S3_REGION",
    "S3_BUCKET",
    "S3_USE_SSL",
)


def _parse_bool(v: str) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        key = k.strip()
        val = v.strip()
        if not key:
            continue
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        out[key] = val
    return out


@dataclass(frozen=True)
class EnvConfig:
    database_url: str
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    s3_region: str
    s3_bucket: str
    s3_use_ssl: bool

    @staticmethod
    def from_env_file(*, path: Path, require_db: bool, require_s3: bool) -> "EnvConfig":
        env = _parse_env_file(path)
        required: tuple[str, ...] = ()
        if require_db:
            required += _REQUIRED_DB_KEYS
        if require_s3:
            required += _REQUIRED_S3_KEYS
        missing = [k for k in required if not str(env.get(k, "")).strip()]
        if missing:
            raise ValueError(f"{path}: missing required keys: {', '.join(missing)}")
        return EnvConfig(
            database_url=str(env.get("DATABASE_URL") or "").strip(),
            s3_endpoint=str(env.get("S3_ENDPOINT") or "").strip(),
            s3_access_key=str(env.get("S3_ACCESS_KEY") or "").strip(),
            s3_secret_key=str(env.get("S3_SECRET_KEY") or "").strip(),
            s3_region=str(env.get("S3_REGION") or "").strip(),
            s3_bucket=str(env.get("S3_BUCKET") or "").strip(),
            s3_use_ssl=_parse_bool(str(env.get("S3_USE_SSL") or "")),
        )


def _sanitize_dsn(url: str) -> str:
    try:
        u = make_url(url)
        if u.password:
            u = u.set(password="***")
        return str(u)
    except Exception:
        return re.sub(r":[^:@/]+@", ":***@", url)


def _sqlalchemy_url_to_psycopg_dsn(url: str) -> str:
    # psycopg.connect doesn't understand SQLAlchemy's "+driver" suffixes.
    for suffix in ("+psycopg", "+psycopg2"):
        if url.startswith(f"postgresql{suffix}://"):
            return "postgresql://" + url.split("://", 1)[1]
    return url


def _client(cfg: EnvConfig):
    return boto3.client(
        "s3",
        endpoint_url=cfg.s3_endpoint,
        aws_access_key_id=cfg.s3_access_key,
        aws_secret_access_key=cfg.s3_secret_key,
        region_name=cfg.s3_region,
        use_ssl=cfg.s3_use_ssl,
        config=BotoConfig(s3={"addressing_style": "path"}),
    )


def _ensure_bucket(client, bucket: str) -> None:
    try:
        client.head_bucket(Bucket=bucket)
        return
    except Exception:
        pass
    client.create_bucket(Bucket=bucket)


def _clear_bucket(client, bucket: str) -> int:
    deleted = 0
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        contents = resp.get("Contents") or []
        keys = [{"Key": o.get("Key")} for o in contents if o.get("Key")]
        if keys:
            client.delete_objects(Bucket=bucket, Delete={"Objects": keys, "Quiet": True})
            deleted += len(keys)
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
        if not token:
            break
    return deleted


def _iter_bucket_objects(client, bucket: str) -> Iterable[tuple[str, int]]:
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        contents = resp.get("Contents") or []
        for o in contents:
            key = o.get("Key")
            if key:
                yield key, int(o.get("Size") or 0)
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
        if not token:
            break


def _format_bytes(num: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(max(0, int(num)))
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)}{unit}"
            return f"{value:.1f}{unit}"
        value /= 1024.0


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
        try:
            conn.execute(text(statement))
        except Exception:
            pass


def _migrate_schema(conn) -> None:
    """
    Copied from backend/raelyn/db.py::_migrate_schema to avoid importing raelyn.db (which binds to .env at import).
    """
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

    if "playlist" in tables:
        cols = {c.get("name") for c in insp.get_columns("playlist")}
        if "avatar_s3_key" not in cols:
            conn.execute(text("alter table playlist add column avatar_s3_key varchar"))
        if "background_s3_key" not in cols:
            conn.execute(text("alter table playlist add column background_s3_key varchar"))
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
                conn.execute(text("update playlist set brief_prompt = :p where brief_prompt is null").bindparams(p=daily_prompt))
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
        _create_job_query_indexes(conn)

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


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _src_public_tables(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("select tablename from pg_tables where schemaname = 'public' order by tablename")
        return [r[0] for r in cur.fetchall()]


def _fk_edges(conn) -> list[tuple[str, str]]:
    # Returns (parent, child).
    sql = """
select
  conrelid::regclass::text as child,
  confrelid::regclass::text as parent
from pg_constraint
where contype = 'f'
  and connamespace = 'public'::regnamespace
"""
    edges: list[tuple[str, str]] = []
    with conn.cursor() as cur:
        cur.execute(sql)
        for child, parent in cur.fetchall():
            c = (child or "").split(".", 1)[-1]
            p = (parent or "").split(".", 1)[-1]
            if not c or not p:
                continue
            if c == p:
                continue
            edges.append((p, c))
    return edges


def _topo_sort(tables: list[str], edges: list[tuple[str, str]]) -> list[str]:
    table_set = set(tables)
    out_edges: dict[str, set[str]] = {t: set() for t in tables}
    indeg: dict[str, int] = {t: 0 for t in tables}
    for parent, child in edges:
        if parent not in table_set or child not in table_set:
            continue
        if child in out_edges[parent]:
            continue
        out_edges[parent].add(child)
        indeg[child] += 1

    q = [t for t in tables if indeg[t] == 0]
    q.sort()
    order: list[str] = []
    while q:
        n = q.pop(0)
        order.append(n)
        for m in sorted(out_edges.get(n) or ()):
            indeg[m] -= 1
            if indeg[m] == 0:
                q.append(m)
                q.sort()
    if len(order) != len(tables):
        # Cycle or missing nodes; fall back to deterministic order.
        return sorted(tables)
    return order


def _table_columns(conn, table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
select column_name
from information_schema.columns
where table_schema = 'public' and table_name = %s
order by ordinal_position
""",
            (table,),
        )
        return [r[0] for r in cur.fetchall()]


def _table_row_count(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"select count(*) from public.{_quote_ident(table)}")
        return int(cur.fetchone()[0] or 0)


def _copy_binary_stream(*, src, dst, copy_to_sql: str, copy_from_sql: str) -> None:
    with src.cursor() as scur, dst.cursor() as dcur:
        with scur.copy(copy_to_sql) as cto, dcur.copy(copy_from_sql) as cfrom:
            for buf in cto:
                cfrom.write(buf)


def _copy_table(*, src, dst, table: str, columns: list[str], select_sql: str | None = None) -> None:
    cols = ", ".join(_quote_ident(c) for c in columns)
    src_table = f'public.{_quote_ident(table)}'
    dst_table = f'public.{_quote_ident(table)}'
    if select_sql is None:
        select_sql = f"select {cols} from {src_table}"
    copy_to = f"COPY ({select_sql}) TO STDOUT WITH (FORMAT BINARY)"
    copy_from = f"COPY {dst_table} ({cols}) FROM STDIN WITH (FORMAT BINARY)"
    _copy_binary_stream(src=src, dst=dst, copy_to_sql=copy_to, copy_from_sql=copy_from)


def _build_select_sql(*, table: str, columns: list[str], overrides: dict[str, str] | None = None) -> str:
    parts: list[str] = []
    overrides = overrides or {}
    for c in columns:
        expr = overrides.get(c)
        if expr is None:
            expr = _quote_ident(c)
        parts.append(f"{expr} as {_quote_ident(c)}")
    return f"select {', '.join(parts)} from public.{_quote_ident(table)}"


def _column_overrides_for_migration(*, table: str, columns: list[str], src_cfg: EnvConfig, dst_cfg: EnvConfig) -> dict[str, str]:
    overrides: dict[str, str] = {}
    if "s3_bucket" in columns and src_cfg.s3_bucket != dst_cfg.s3_bucket:
        dst_bucket = dst_cfg.s3_bucket.replace("'", "''")
        overrides["s3_bucket"] = f"'{dst_bucket}'"
    return overrides


def _truncate_all_tables(dst, tables: list[str]) -> None:
    # TRUNCATE too many tables in one statement can be unwieldy; batch.
    batch: list[str] = []
    for t in tables:
        batch.append(f'public.{_quote_ident(t)}')
        if len(batch) >= 50:
            sql = "TRUNCATE TABLE " + ", ".join(batch) + " RESTART IDENTITY CASCADE"
            with dst.cursor() as cur:
                cur.execute(sql)
            batch.clear()
    if batch:
        sql = "TRUNCATE TABLE " + ", ".join(batch) + " RESTART IDENTITY CASCADE"
        with dst.cursor() as cur:
            cur.execute(sql)


def _fix_sequences(dst, tables: list[str]) -> None:
    # Find serial/identity sequences via column default and pg_get_serial_sequence.
    with dst.cursor() as cur:
        cur.execute(
            """
select table_name, column_name,
       pg_get_serial_sequence(format('public.%%I', table_name), column_name) as seq
from information_schema.columns
where table_schema = 'public'
  and table_name = any(%s)
  and column_default like 'nextval(%%'
""",
            (tables,),
        )
        rows = cur.fetchall()

    for table_name, column_name, seq in rows:
        if not seq:
            continue
        with dst.cursor() as cur:
            cur.execute(f"select max({_quote_ident(column_name)}) from public.{_quote_ident(table_name)}")
            mx = cur.fetchone()[0]
            if mx is None:
                cur.execute("select setval(%s, 1, false)", (seq,))
            else:
                cur.execute("select setval(%s, %s, true)", (seq, mx))


def _ensure_target_schema(dst_database_url: str) -> None:
    engine = create_engine(dst_database_url, pool_pre_ping=True)
    with engine.begin() as conn:
        Base.metadata.create_all(bind=conn)
        _migrate_schema(conn)


def _migrate_db(*, src_cfg: EnvConfig, dst_cfg: EnvConfig, yes: bool) -> None:
    if not yes:
        print("[dry-run] db: would create schema, truncate target tables, then COPY all public tables")
        return

    for which, url in (("src", src_cfg.database_url), ("dst", dst_cfg.database_url)):
        try:
            driver = make_url(url).drivername
        except Exception as e:
            raise RuntimeError(f"{which} db: invalid DATABASE_URL: {e}") from e
        if not driver.startswith("postgresql"):
            raise RuntimeError(f"{which} db: only PostgreSQL is supported (driver={driver})")

    _ensure_target_schema(dst_cfg.database_url)

    src_dsn = _sqlalchemy_url_to_psycopg_dsn(src_cfg.database_url)
    dst_dsn = _sqlalchemy_url_to_psycopg_dsn(dst_cfg.database_url)
    with psycopg.connect(src_dsn) as src, psycopg.connect(dst_dsn) as dst:
        with src.cursor() as cur:
            cur.execute("select current_database(), version()")
            _ = cur.fetchone()
        with dst.cursor() as cur:
            cur.execute("select current_database(), version()")
            _ = cur.fetchone()

        src_tables = _src_public_tables(src)
        dst_tables = _src_public_tables(dst)
        if not dst_tables:
            raise RuntimeError("dst db: no public tables found after create_all; aborting")

        # Truncate destination tables (all public tables).
        print(f"[db] truncate target tables: {len(dst_tables)}")
        _truncate_all_tables(dst, dst_tables)
        dst.commit()

        # Build dependency order from source schema, copy only tables that exist on both sides.
        common = [t for t in src_tables if t in set(dst_tables)]
        edges = _fk_edges(src)
        order = _topo_sort(common, edges)

        # Preflight column compatibility.
        cols_by_table: dict[str, list[str]] = {}
        for t in order:
            src_cols = _table_columns(src, t)
            dst_cols = _table_columns(dst, t)
            src_set = set(src_cols)
            dst_set = set(dst_cols)
            if src_set != dst_set:
                missing_in_src = [c for c in dst_cols if c not in src_set]
                missing_in_dst = [c for c in src_cols if c not in dst_set]
                raise RuntimeError(
                    f"column mismatch on table {t}: "
                    f"missing_in_src={missing_in_src} missing_in_dst={missing_in_dst} "
                    f"src={src_cols} dst={dst_cols}"
                )
            cols_by_table[t] = dst_cols

        row_counts: dict[str, int] = {}
        total_rows = 0
        print(f"[db] count source rows: tables={len(order)}")
        for idx, t in enumerate(order, start=1):
            row_count = _table_row_count(src, t)
            row_counts[t] = row_count
            total_rows += row_count
            print(f"[db] count progress: {idx}/{len(order)} table={t} rows={row_count} total_rows={total_rows}")

        # Self-referential FK handling for job.parent_job_id.
        has_job = "job" in cols_by_table and "parent_job_id" in cols_by_table["job"]
        if has_job:
            with dst.cursor() as cur:
                cur.execute("create temp table if not exists tmp_migrate_job_parent(job_id uuid primary key, parent_job_id uuid)")
            dst.commit()
            print("[db] pre-copy: job parent mapping -> tmp_migrate_job_parent")
            mapping_cols = ["job_id", "parent_job_id"]
            select_map = "select id as job_id, parent_job_id from public.job where parent_job_id is not null"
            copy_to = f"COPY ({select_map}) TO STDOUT WITH (FORMAT BINARY)"
            copy_from = "COPY tmp_migrate_job_parent(job_id, parent_job_id) FROM STDIN WITH (FORMAT BINARY)"
            _copy_binary_stream(src=src, dst=dst, copy_to_sql=copy_to, copy_from_sql=copy_from)
            dst.commit()

        copied_rows = 0
        copy_started = time.time()
        for table_index, t in enumerate(order, start=1):
            cols = cols_by_table[t]
            table_rows = row_counts.get(t, 0)
            table_started = time.time()
            overrides = _column_overrides_for_migration(table=t, columns=cols, src_cfg=src_cfg, dst_cfg=dst_cfg)
            print(
                f"[db] copy start: {table_index}/{len(order)} "
                f"table={t} rows={table_rows} copied_rows={copied_rows}/{total_rows}"
            )
            if t == "job" and has_job:
                overrides = dict(overrides)
                overrides["parent_job_id"] = "NULL::uuid"
                select_sql = _build_select_sql(
                    table=t,
                    columns=cols,
                    overrides=overrides,
                )
                _copy_table(src=src, dst=dst, table=t, columns=cols, select_sql=select_sql)
            else:
                select_sql = _build_select_sql(table=t, columns=cols, overrides=overrides)
                _copy_table(src=src, dst=dst, table=t, columns=cols, select_sql=select_sql)
            dst.commit()
            copied_rows += table_rows
            table_elapsed = time.time() - table_started
            total_elapsed = time.time() - copy_started
            print(
                f"[db] copy done: {table_index}/{len(order)} "
                f"table={t} rows={table_rows} copied_rows={copied_rows}/{total_rows} "
                f"seconds={table_elapsed:.1f} total_seconds={total_elapsed:.1f}"
            )

        if has_job:
            print("[db] post-copy: restore job.parent_job_id from tmp_migrate_job_parent")
            with dst.cursor() as cur:
                cur.execute(
                    """
update public.job j
set parent_job_id = t.parent_job_id
from tmp_migrate_job_parent t
where j.id = t.job_id
"""
                )
                cur.execute("drop table if exists tmp_migrate_job_parent")
            dst.commit()

        print("[db] fix sequences")
        _fix_sequences(dst, [t for t in order if t in set(dst_tables)])
        dst.commit()

        print(f"[db] done: tables={len(order)} copied_rows={copied_rows}/{total_rows}")


def _migrate_s3(*, src_cfg: EnvConfig, dst_cfg: EnvConfig, yes: bool, concurrency: int) -> None:
    src_client = _client(src_cfg)
    dst_client = _client(dst_cfg)
    src_bucket = src_cfg.s3_bucket
    dst_bucket = dst_cfg.s3_bucket
    transfer_config = TransferConfig(
        multipart_threshold=8 * 1024 * 1024,
        multipart_chunksize=8 * 1024 * 1024,
        max_concurrency=1,
        use_threads=False,
    )
    max_attempts = 3

    if not yes:
        print(f"[dry-run] s3: would ensure target bucket and copy ALL objects: src_bucket={src_bucket} dst_bucket={dst_bucket}")
        return

    _ensure_bucket(dst_client, dst_bucket)
    print(f"[s3] clear target bucket: bucket={dst_bucket}")
    deleted = _clear_bucket(dst_client, dst_bucket)
    print(f"[s3] cleared: bucket={dst_bucket} deleted={deleted}")

    objects = list(_iter_bucket_objects(src_client, src_bucket))
    total_bytes = sum(sz for _, sz in objects)
    total_objects = len(objects)
    print(
        f"[s3] source objects: bucket={src_bucket} count={total_objects} bytes={total_bytes} "
        f"human_bytes={_format_bytes(total_bytes)}"
    )

    failures: list[tuple[str, str]] = []

    def _copy_one(key: str) -> None:
        suffix = Path(key).suffix
        fd, tmp_path = tempfile.mkstemp(prefix="migrate-s3-", suffix=suffix)
        os.close(fd)
        try:
            for attempt in range(1, max_attempts + 1):
                try:
                    head = src_client.head_object(Bucket=src_bucket, Key=key)
                    extra: dict[str, Any] = {}
                    ct = head.get("ContentType")
                    if ct:
                        extra["ContentType"] = ct
                    md = head.get("Metadata")
                    if isinstance(md, dict) and md:
                        extra["Metadata"] = md

                    src_client.download_file(src_bucket, key, tmp_path, Config=transfer_config)
                    dst_client.upload_file(tmp_path, dst_bucket, key, ExtraArgs=extra or None, Config=transfer_config)
                    return
                except Exception:
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except Exception:
                        pass
                    fd, tmp_path = tempfile.mkstemp(prefix="migrate-s3-", suffix=suffix)
                    os.close(fd)
                    if attempt >= max_attempts:
                        raise
                    wait_seconds = min(5.0, float(2 ** (attempt - 1)))
                    print(
                        f"[s3] retry: key={key} attempt={attempt}/{max_attempts} wait={wait_seconds:.1f}s",
                        file=sys.stderr,
                    )
                    time.sleep(wait_seconds)
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

    started = time.time()
    progress_lock = threading.Lock()
    copied_objects = 0
    copied_bytes = 0
    last_report_at = 0.0
    report_every = 25
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(concurrency))) as ex:
        fut_to_obj = {ex.submit(_copy_one, key): (key, size) for key, size in objects}
        for fut in concurrent.futures.as_completed(fut_to_obj):
            key, size = fut_to_obj[fut]
            try:
                fut.result()
                with progress_lock:
                    copied_objects += 1
                    copied_bytes += size
                    elapsed = time.time() - started
                    should_report = (
                        copied_objects == total_objects
                        or copied_objects == 1
                        or copied_objects % report_every == 0
                        or elapsed - last_report_at >= 5.0
                    )
                    if should_report:
                        last_report_at = elapsed
                        print(
                            f"[s3] copy progress: {copied_objects}/{total_objects} "
                            f"bytes={copied_bytes}/{total_bytes} "
                            f"human_bytes={_format_bytes(copied_bytes)}/{_format_bytes(total_bytes)} "
                            f"failed={len(failures)} seconds={elapsed:.1f}"
                        )
            except Exception as e:
                failures.append((key, str(e)))
                elapsed = time.time() - started
                print(
                    f"[s3] copy failed: {copied_objects}/{total_objects} "
                    f"key={key} failed={len(failures)} seconds={elapsed:.1f}",
                    file=sys.stderr,
                )

    elapsed = time.time() - started
    ok = total_objects - len(failures)
    print(
        f"[s3] copied: ok={ok}/{total_objects} failed={len(failures)} "
        f"bytes={copied_bytes}/{total_bytes} "
        f"human_bytes={_format_bytes(copied_bytes)}/{_format_bytes(total_bytes)} "
        f"seconds={elapsed:.1f}"
    )
    if failures:
        for key, err in failures[:50]:
            print(f"[s3] failed: {key} err={err}", file=sys.stderr)
        raise RuntimeError(f"s3 copy failed: {len(failures)} objects")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Migrate data from source (.env) to destination (.env.migrate).")
    ap.add_argument("--src-env", default=".env", help="Source env file (default: .env)")
    ap.add_argument("--dst-env", default=".env.migrate", help="Destination env file (default: .env.migrate)")
    ap.add_argument("--yes", action="store_true", help="Actually perform the migration (will CLEAR destination DB + S3)")
    ap.add_argument("--db", action="store_true", help="Migrate DB only")
    ap.add_argument("--s3", action="store_true", help="Migrate S3 only")
    ap.add_argument("--s3-concurrency", type=int, default=8, help="S3 copy concurrency (default: 8)")
    ap.add_argument(
        "--allow-bucket-mismatch",
        action="store_true",
        help="Allow dst S3_BUCKET != src S3_BUCKET and remap copied assets to the destination bucket",
    )
    args = ap.parse_args(argv)

    do_db = args.db or (not args.db and not args.s3)
    do_s3 = args.s3 or (not args.db and not args.s3)

    src_env_path = Path(args.src_env)
    dst_env_path = Path(args.dst_env)

    try:
        src_cfg = EnvConfig.from_env_file(path=src_env_path, require_db=do_db, require_s3=do_s3)
        dst_cfg = EnvConfig.from_env_file(path=dst_env_path, require_db=do_db, require_s3=do_s3)
    except FileNotFoundError as e:
        print(f"env file not found: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"failed to load env: {e}", file=sys.stderr)
        return 2

    if do_s3:
        if not args.allow_bucket_mismatch and src_cfg.s3_bucket != dst_cfg.s3_bucket:
            print(
                f"refusing: S3_BUCKET mismatch (src={src_cfg.s3_bucket} dst={dst_cfg.s3_bucket}). "
                "Use --allow-bucket-mismatch to override.",
                file=sys.stderr,
            )
            return 2
        if args.allow_bucket_mismatch and src_cfg.s3_bucket != dst_cfg.s3_bucket:
            print(
                f"[warn] S3_BUCKET mismatch (src={src_cfg.s3_bucket} dst={dst_cfg.s3_bucket}); "
                f"objects will be copied to dst bucket and DB asset references will be remapped to bucket={dst_cfg.s3_bucket}",
                file=sys.stderr,
            )

    if do_db:
        print("[plan] src db:", _sanitize_dsn(src_cfg.database_url))
        print("[plan] dst db:", _sanitize_dsn(dst_cfg.database_url))
    if do_s3:
        print(f"[plan] src s3: endpoint={src_cfg.s3_endpoint} bucket={src_cfg.s3_bucket} use_ssl={src_cfg.s3_use_ssl}")
        print(f"[plan] dst s3: endpoint={dst_cfg.s3_endpoint} bucket={dst_cfg.s3_bucket} use_ssl={dst_cfg.s3_use_ssl}")
    if not args.yes:
        print("[plan] mode=dry-run (no writes). Add --yes to CLEAR destination and migrate.")
    else:
        print("[plan] mode=execute (will CLEAR destination DB and S3)")

    try:
        if do_db:
            _migrate_db(src_cfg=src_cfg, dst_cfg=dst_cfg, yes=args.yes)
        if do_s3:
            _migrate_s3(src_cfg=src_cfg, dst_cfg=dst_cfg, yes=args.yes, concurrency=args.s3_concurrency)
    except Exception as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1

    print("[done]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
