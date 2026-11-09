#!/usr/bin/env python3
"""Background worker: drains job queue and runs ingestion/evaluation pipelines."""

from __future__ import annotations

import json
import logging
import os
import threading
import time

from db import get_session, init_db
from env_config import load_env
from evaluation_orchestrator import run_evaluation_for_document
from job_queue import claim_next_job, complete_job, recover_orphaned_jobs
from redis_client import redis_status, set_worker_heartbeat
from models import JobRun
from structured_logging import configure_logging, set_correlation_id

logger = logging.getLogger(__name__)

POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "2"))
REDIS_BLOCK_TIMEOUT = float(os.environ.get("REDIS_BLOCK_TIMEOUT", "5"))
HEARTBEAT_INTERVAL_SECONDS = float(os.environ.get("WORKER_HEARTBEAT_INTERVAL_SECONDS", "30"))


def _heartbeat_loop(stop: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_INTERVAL_SECONDS):
        set_worker_heartbeat()


def process_job(session, job) -> None:
    meta = json.loads(job.metadata_json or "{}")
    correlation_id = meta.get("correlation_id")
    set_correlation_id(correlation_id)
    stop_hb = threading.Event()
    hb_thread = threading.Thread(target=_heartbeat_loop, args=(stop_hb,), daemon=True)
    hb_thread.start()

    try:
        if job.job_type == "evaluate_document":
            eval_id, status = run_evaluation_for_document(
                session,
                company_id=meta["company_id"],
                document_id=meta["document_id"],
                correlation_id=correlation_id,
                evaluation_id=meta.get("evaluation_id"),
            )
            # NOTE: the private deployment pushed results back to the firm's CRM here.
            # That integration is omitted from the public version.
            complete_job(
                session,
                job,
                "completed",
                json.dumps({"evaluation_id": eval_id, "status": status}),
            )
        else:
            complete_job(session, job, "failed", f"Unknown job type: {job.job_type}")
    except Exception as exc:
        logger.exception("Job %s failed", job.id)
        complete_job(session, job, "failed", str(exc))
    finally:
        stop_hb.set()
        hb_thread.join(timeout=2)
        set_worker_heartbeat()


def main() -> None:
    load_env()
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    init_db()
    set_worker_heartbeat()
    try:
        with get_session() as session:
            recovered = recover_orphaned_jobs(session)
            if recovered:
                logger.warning("Recovered %s orphaned processing job(s) on startup", recovered)
    except Exception as exc:
        logger.exception("Job recovery on startup failed: %s", exc)

    redis = redis_status()
    logger.info(
        "Deal Screener worker started (poll=%ss, redis=%s, queue_depth=%s)",
        POLL_SECONDS,
        "connected" if redis["connected"] else redis["detail"],
        redis["queue_depth"],
    )

    while True:
        set_worker_heartbeat()
        job_id: str | None = None
        with get_session() as session:
            redis = redis_status()
            block = REDIS_BLOCK_TIMEOUT if redis["connected"] else 0
            job = claim_next_job(session, block_seconds=block)
            if job is None:
                if not redis["connected"]:
                    time.sleep(POLL_SECONDS)
                continue
            job_id = job.id
            logger.info("Processing job %s type=%s", job.id, job.job_type)
            # Commit "processing" before long-running work so /jobs/{id} reflects live state.

        with get_session() as session:
            job = session.get(JobRun, job_id) if job_id else None
            if job is not None:
                process_job(session, job)


if __name__ == "__main__":
    main()
