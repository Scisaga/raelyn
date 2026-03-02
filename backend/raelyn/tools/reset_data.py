from __future__ import annotations

import argparse
import sys

from raelyn.db import engine
from raelyn.models import Base
from raelyn.services.s3 import s3_clear_bucket, s3_ensure_bucket


def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Danger: delete ALL local dev data (DB + S3 bucket objects).")
    ap.add_argument("--yes", action="store_true", help="Actually perform the destructive reset")
    ap.add_argument("--db", action="store_true", help="Reset database (drop_all + create_all)")
    ap.add_argument("--s3", action="store_true", help="Clear S3 bucket objects")
    ap.add_argument("--prefix", default="", help="Only clear this S3 prefix (default: entire bucket)")
    args = ap.parse_args(argv)

    do_db = args.db or (not args.db and not args.s3)
    do_s3 = args.s3 or (not args.db and not args.s3)

    if not args.yes:
        print("Refusing to run without --yes (this will DELETE data).", file=sys.stderr)
        return 2

    if do_db:
        print("[reset] database: drop_all + create_all")
        reset_db()

    if do_s3:
        print("[reset] s3: clear bucket objects")
        s3_ensure_bucket()
        r = s3_clear_bucket(prefix=args.prefix)
        if not r.get("ok"):
            print(f"[reset] s3: failed: {r.get('error')}", file=sys.stderr)
            return 1
        print(f"[reset] s3: deleted={r.get('deleted')} bucket={r.get('bucket')}")

    print("[reset] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

