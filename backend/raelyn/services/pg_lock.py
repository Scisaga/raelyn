from __future__ import annotations

import hashlib
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session


def lock_key(name: str) -> int:
    digest = hashlib.sha1(name.encode("utf-8")).digest()
    # 取前 8 字节作为 64-bit key（signed int64）
    value = int.from_bytes(digest[:8], byteorder="big", signed=False)
    return value % (2**63)


def try_lock(session: Session | Connection, name: str) -> bool:
    key = lock_key(name)
    row = session.execute(text("select pg_try_advisory_lock(:k) as ok").bindparams(k=key)).mappings().one()
    return bool(row["ok"])


def try_xact_lock(session: Session, name: str) -> bool:
    """获取 PostgreSQL 事务级 advisory lock，随当前事务 commit/rollback 自动释放。"""
    key = lock_key(name)
    row = session.execute(text("select pg_try_advisory_xact_lock(:k) as ok").bindparams(k=key)).mappings().one()
    return bool(row["ok"])


def unlock(session: Session | Connection, name: str) -> None:
    key = lock_key(name)
    session.execute(text("select pg_advisory_unlock(:k)").bindparams(k=key))


@contextmanager
def advisory_lock(session: Session, name: str):
    with advisory_lock_any(session, [name]) as acquired:
        yield acquired is not None


@contextmanager
def advisory_lock_any(session: Session, names: list[str]):
    """按顺序领取一个会话锁，并在同一物理连接上释放。"""
    # 业务 Session 可能提交、回滚或进入 aborted 状态。独立借用池中连接
    # 持有门控锁，使解锁不依赖业务事务，也不会因换连接遗留会话锁。
    with session.get_bind().engine.connect() as connection:
        acquired: str | None = None
        for raw_name in names or []:
            name = str(raw_name or "").strip()
            if not name:
                continue
            if try_lock(connection, name):
                acquired = name
                break
        try:
            yield acquired
        finally:
            if acquired:
                unlock(connection, acquired)
            connection.commit()
