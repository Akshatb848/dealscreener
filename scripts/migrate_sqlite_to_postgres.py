#!/usr/bin/env python3
"""Migrate schema + data from SQLite to PostgreSQL."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from env_config import load_env
from models import Base


def _target_has_data(engine) -> bool:
    insp = inspect(engine)
    for name in sorted(insp.get_table_names()):
        if name not in Base.metadata.tables:
            continue
        with engine.connect() as conn:
            count = conn.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar()
            if count and int(count) > 0:
                return True
    return False


def _copy_table(source_session, target_session, table_name: str) -> int:
    rows = source_session.execute(
        Base.metadata.tables[table_name].select()
    ).mappings().all()
    if not rows:
        return 0
    payload = [dict(row) for row in rows]
    target_session.execute(Base.metadata.tables[table_name].insert(), payload)
    return len(payload)


def migrate(
    source_url: str,
    target_url: str,
    *,
    data: bool = True,
    if_empty: bool = False,
) -> None:
    source_engine = create_engine(source_url, connect_args={"check_same_thread": False})
    target_engine = create_engine(target_url, pool_pre_ping=True)

    Base.metadata.create_all(bind=target_engine)

    if data and if_empty and _target_has_data(target_engine):
        print("Target PostgreSQL already has data — skipping copy (--if-empty)")
        return

    Source = sessionmaker(bind=source_engine)
    Target = sessionmaker(bind=target_engine)

    with Source() as src, Target() as tgt:
        if data:
            tables = [
                t for t in inspect(source_engine).get_table_names()
                if t in Base.metadata.tables
            ]
            counts = {}
            for name in tables:
                counts[name] = _copy_table(src, tgt, name)
            tgt.commit()
            print("Migrated rows:", counts)
        else:
            tgt.commit()
            print("PostgreSQL schema created (no data copy)")


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="Migrate SQLite → PostgreSQL")
    parser.add_argument(
        "--source",
        default=os.environ.get("SQLITE_SOURCE_URL", "sqlite:///./dealscreener.db"),
    )
    parser.add_argument(
        "--target",
        default=os.environ.get("DATABASE_URL", ""),
        help="PostgreSQL DATABASE_URL target",
    )
    parser.add_argument("--schema-only", action="store_true")
    parser.add_argument(
        "--if-empty",
        action="store_true",
        help="Skip data copy when target tables already contain rows",
    )
    args = parser.parse_args()

    if not args.target or not args.target.startswith("postgresql"):
        print("ERROR: set DATABASE_URL to a postgresql:// URL or pass --target", file=sys.stderr)
        return 1

    migrate(
        args.source,
        args.target,
        data=not args.schema_only,
        if_empty=args.if_empty,
    )
    print("Migration complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
