"""清理重复星域归档，保留现存投影和简报实际引用的对象。

运行示例：
PYTHONPATH=backend ./.venv/bin/python -m raelyn.tools.compact_event_map_history \
    --backup-dir /mnt/data1/raelyn-maintenance/event-map-history
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text

from raelyn.db import engine


ARCHIVES = (
    ("event_map_canonical_history_revision", "canonical", "canonical_id"),
    ("event_map_story_history_revision", "story", "story_identity_id"),
)
DERIVED = ("event_map_canonical_history_member", "event_map_story_history_evidence", "event_map_change")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", required=True, type=Path)
    args = parser.parse_args()
    backup_dir = args.backup_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {"backup_dir": str(backup_dir), "tables": {}}
    with engine.connect() as conn:
        conn.execute(text("set statement_timeout = '15min'"))
        active = conn.scalar(text("select count(*) from job where status = 'running' and type = 'playlist.build_event_map_snapshot'"))
        if active:
            raise RuntimeError("请先停止星域构建任务")
        snapshots = list(conn.scalars(text("select id from event_map_snapshot where status = 'ready'")))
        counts_before = {
            table: conn.scalar(text(f"select count(*) from {table}"))
            for table in ("video", "event_map_record_revision", "brief", "brief_reference", "story_read_state")
        }
        staged_tables: dict[str, str] = {}
        for table, object_type, object_column in ARCHIVES:
            staged = f"cleanup_{table}"
            conn.execute(text(f"create temporary table {staged} (like {table})"))
            for snapshot_id in snapshots:
                conn.execute(text(f"insert into {staged} select * from {table} where snapshot_id = :id"), {"id": snapshot_id})
                conn.commit()
            conn.execute(text(f"""insert into {staged}
select archive.* from (
    select distinct snapshot_id, object_id from brief_reference
    where object_type = :object_type and snapshot_id is not null
      and snapshot_id not in (select id from event_map_snapshot where status = 'ready')
) reference
cross join lateral (
    select * from {table} where snapshot_id = reference.snapshot_id
      and {object_column} = reference.object_id limit 1
) archive"""), {"object_type": object_type})
            conn.execute(text(f"create unique index on {staged} (snapshot_id, {object_column})"))
            missing = conn.scalar(text(f"""select count(*) from brief_reference reference
where reference.object_type = :object_type and reference.snapshot_id is not null
and not exists (select 1 from {staged} archive where archive.snapshot_id = reference.snapshot_id
    and archive.{object_column} = reference.object_id)"""), {"object_type": object_type})
            if missing:
                raise RuntimeError(f"{object_type} 引用缺少原归档：{missing}，未执行删除")
            manifest["tables"][table] = {"retained_rows": conn.scalar(text(f"select count(*) from {staged}")),
                "previous_bytes": conn.scalar(text("select pg_total_relation_size(cast(:name as regclass))"), {"name": table})}
            staged_tables[table] = staged
            conn.commit()
            print(json.dumps({"已核对": table, **manifest["tables"][table]}, ensure_ascii=False), flush=True)

        exports = {**staged_tables, "brief": "brief", "brief_reference": "brief_reference", "story_read_state": "story_read_state"}
        raw_connection = conn.connection.driver_connection
        for target, source in exports.items():
            destination = backup_dir / f"{target}.csv.gz"
            with closing(raw_connection.cursor()) as cursor, gzip.open(destination, "wb", compresslevel=3) as output:
                with cursor.copy(f"copy {source} to stdout with (format csv, header true)") as copy:
                    for block in copy:
                        output.write(block)
            digest = hashlib.sha256()
            with destination.open("rb") as content:
                for block in iter(lambda: content.read(1024 * 1024), b""):
                    digest.update(block)
            manifest.setdefault("exports", {})[target] = {"bytes": destination.stat().st_size, "sha256": digest.hexdigest()}
        conn.commit()
        (backup_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        print(json.dumps({"引用备份已完成": str(backup_dir)}, ensure_ascii=False), flush=True)

        conn.execute(text("set local lock_timeout = '15s'"))
        tables = [table for table, _, _ in ARCHIVES] + list(DERIVED)
        conn.execute(text("truncate table " + ", ".join(tables)))
        for table, staged in staged_tables.items():
            conn.execute(text(f"insert into {table} select * from {staged}"))
        counts_after = {table: conn.scalar(text(f"select count(*) from {table}")) for table in counts_before}
        if counts_after != counts_before:
            raise RuntimeError("原始数据或引用数量发生变化，事务回滚")
        conn.commit()
        released = sum(int(data["previous_bytes"]) - int(conn.scalar(text(
            "select pg_total_relation_size(cast(:name as regclass))"), {"name": name}))
            for name, data in manifest["tables"].items())
        print(json.dumps({"已回收GiB": round(released / 1024**3, 2), "原始数据与引用数量": counts_after}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # 驱动异常可能携带 DSN 或 SQL 参数；终端只报告类型。
        print(json.dumps({"清理失败类型": type(exc).__name__}, ensure_ascii=False), flush=True)
        raise SystemExit(1)
