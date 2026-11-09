#!/usr/bin/env python3
"""Drain or recover jobs before restart (single-worker deploy maintenance)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from db import get_session, init_db
from env_config import load_env
from job_queue import drain_queue, recover_orphaned_jobs

load_env()


def main() -> int:
    parser = argparse.ArgumentParser(description="Maintain job queue before/after worker restart")
    parser.add_argument(
        "--drain",
        action="store_true",
        help="Fail all pending/processing jobs (use before smoke/restart)",
    )
    parser.add_argument(
        "--recover",
        action="store_true",
        help="Recover orphaned processing jobs (worker startup semantics)",
    )
    args = parser.parse_args()

    init_db()
    with get_session() as session:
        if args.drain or not args.recover:
            n = drain_queue(session, reason="cleared for maintenance restart")
            print(f"OK drained {n} job(s) (failed/cleared)")
            return 0
        n = recover_orphaned_jobs(session)
        print(f"OK recovered/failed {n} job(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
