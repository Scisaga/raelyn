from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy import inspect
from sqlalchemy.orm import Session, sessionmaker

from videosync.config import settings


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, class_=Session)

_CREATE_ALL_LOCK_KEY = "videosync.schema.create_all"


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
                conn.execute(text("alter table media add column monitor_enabled boolean not null default true"))
            else:
                conn.execute(text("alter table media add column monitor_enabled boolean not null default 1"))
        if "avatar_s3_key" not in cols:
            conn.execute(text("alter table media add column avatar_s3_key varchar"))

    if "playlist" in tables:
        cols = {c.get("name") for c in insp.get_columns("playlist")}
        if "avatar_s3_key" not in cols:
            conn.execute(text("alter table playlist add column avatar_s3_key varchar"))
        if "background_s3_key" not in cols:
            conn.execute(text("alter table playlist add column background_s3_key varchar"))


def init_db() -> None:
    # Multiple processes (api/worker/scheduler) may call this at startup; serialize to avoid
    # Postgres catalog races during CREATE TABLE.
    from videosync.models import Base  # local import to avoid import cycles at module load

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
