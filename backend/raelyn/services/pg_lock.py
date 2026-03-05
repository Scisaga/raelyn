from __future__ import annotations

import hashlib
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session


def lock_key(name: str) -> int:
    digest = hashlib.sha1(name.encode("utf-8")).digest()
    # 取前 8 字节作为 64-bit key（signed int64）
    value = int.from_bytes(digest[:8], byteorder="big", signed=False)
    return value % (2**63)


def try_lock(session: Session, name: str) -> bool:
    key = lock_key(name)
    row = session.execute(text("select pg_try_advisory_lock(:k) as ok").bindparams(k=key)).mappings().one()
    return bool(row["ok"])


def unlock(session: Session, name: str) -> None:
    key = lock_key(name)
    session.execute(text("select pg_advisory_unlock(:k)").bindparams(k=key))


@contextmanager
def advisory_lock(session: Session, name: str):
    ok = try_lock(session, name)
    try:
        yield ok
    finally:
        if ok:
            unlock(session, name)


@contextmanager
def advisory_lock_any(session: Session, names: list[str]):
    """
    Try to acquire any lock from `names` (in order). Yields the acquired name, or None.
    """
    acquired: str | None = None
    for n in names or []:
        s = str(n or "").strip()
        if not s:
            continue
        if try_lock(session, s):
            acquired = s
            break
    try:
        yield acquired
    finally:
        if acquired:
            unlock(session, acquired)
