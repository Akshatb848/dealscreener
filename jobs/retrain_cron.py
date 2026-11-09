#!/usr/bin/env python3
"""Cron entrypoint: retrain classifier when >=50 reviewed cases (Sunday midnight schedule)."""

from __future__ import annotations

import os
import sys

from db import get_session, init_db
from learning_loop import run_learning_loop
from structured_logging import configure_logging


def main() -> int:
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    init_db()
    dry_run = os.environ.get("RETRAIN_DRY_RUN", "false").lower() in {"1", "true", "yes"}
    with get_session() as session:
        job = run_learning_loop(session, dry_run=dry_run)
        print(f"Retrain job status={job.status} message={job.message}")
        return 0 if job.status in {"completed", "dry_run", "skipped"} else 1


if __name__ == "__main__":
    sys.exit(main())
