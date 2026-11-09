"""Job queue: Redis when available, SQLAlchemy JobRun fallback."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from models import JobRun
from redis_client import get_queue_key, get_redis_client

logger = logging.getLogger(__name__)


def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite may return naive datetimes; normalize before comparing."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_redis_idle_timeout(exc: Exception) -> bool:
    import redis

    if isinstance(exc, (TimeoutError, redis.TimeoutError)):
        return True
    message = str(exc).lower()
    return "timeout reading from socket" in message or "timed out" in message


def _db_fallback_enabled() -> bool:
    return os.environ.get("REDIS_DB_FALLBACK", "true").lower() in {"1", "true", "yes"}


def _redis_queue_available() -> bool:
    """True only when Redis accepts a ping (skip dead clients when using DB fallback)."""
    from redis_client import ping_redis

    ok, _ = ping_redis()
    return ok


def enqueue_job(
    session: Session,
    job_type: str,
    payload: dict[str, Any],
    *,
    correlation_id: str | None = None,
    dry_run: bool = False,
) -> str:
    job_id = str(uuid.uuid4())
    correlation_id = correlation_id or str(uuid.uuid4())
    payload_with_meta = {**payload, "correlation_id": correlation_id}
    queue_key = get_queue_key()

    job = JobRun(
        id=job_id,
        job_type=job_type,
        status="pending",
        dry_run=dry_run,
        message=f"Queued {job_type}",
        metadata_json=json.dumps(payload_with_meta),
        started_at=None,
        finished_at=None,
    )
    session.add(job)
    session.flush()

    client = get_redis_client(blocking=False)
    if client is not None and _redis_queue_available():
        try:
            client.lpush(
                queue_key,
                json.dumps({"job_id": job_id, "job_type": job_type, "payload": payload_with_meta}),
            )
        except Exception as exc:
            logger.warning("Redis enqueue failed for job %s (%s); DB queue only", job_id, exc)
    return job_id


def _claim_from_redis(session: Session, *, block_seconds: float | None = None) -> JobRun | None:
    queue_key = get_queue_key()
    use_blocking = bool(block_seconds and block_seconds > 0)
    client = get_redis_client(blocking=use_blocking)
    if client is None:
        return None

    try:
        if use_blocking:
            result = client.brpop(queue_key, timeout=max(1, int(block_seconds)))
            raw = result[1] if result else None
        else:
            raw = client.rpop(queue_key)
    except Exception as exc:
        if use_blocking and _is_redis_idle_timeout(exc):
            return None
        if os.environ.get("REDIS_REQUIRED", "").lower() in {"1", "true", "yes"}:
            logger.warning("Redis claim failed (%s); falling back to DB queue", exc)
        else:
            logger.debug("Redis claim skipped (%s); using DB queue", exc)
        return None

    if not raw:
        return None

    data = json.loads(raw)
    job = session.get(JobRun, data["job_id"])
    if job and job.status == "pending":
        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
        return job
    return None


def claim_next_job(session: Session, *, block_seconds: float | None = None) -> JobRun | None:
    if block_seconds is None:
        block_seconds = float(os.environ.get("REDIS_BLOCK_TIMEOUT", "0"))

    if _redis_queue_available():
        job = _claim_from_redis(session, block_seconds=block_seconds)
        if job is not None:
            return job
        # BRPOP idle (no redis message): skip DB unless fallback enabled
        if block_seconds > 0 and not _db_fallback_enabled():
            return None

    if not _db_fallback_enabled():
        return None

    job = (
        session.query(JobRun)
        .filter_by(status="pending")
        .order_by(JobRun.created_at.asc())
        .first()
    )
    if job:
        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
    return job


def complete_job(session: Session, job: JobRun, status: str, message: str) -> None:
    job.status = status
    job.message = message
    job.finished_at = datetime.now(timezone.utc)


def recover_orphaned_jobs(session: Session, *, stale_seconds: int | None = None) -> int:
    """
    On worker startup: fail jobs that exceeded max runtime; re-queue recent orphans.

    Jobs stuck in ``processing`` longer than ``JOB_MAX_RUNTIME_SECONDS`` are failed
    so they cannot block the single-worker queue forever.
    """
    from models import JobRun

    max_runtime = int(os.environ.get("JOB_MAX_RUNTIME_SECONDS", "600"))
    stale_seconds = stale_seconds or int(os.environ.get("JOB_STALE_SECONDS", "180"))
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)
    now = datetime.now(timezone.utc)

    failed = 0
    recovered = 0
    processing = session.query(JobRun).filter(JobRun.status == "processing").all()
    for job in processing:
        started = _as_utc(job.started_at)
        age = (now - started).total_seconds() if started else max_runtime + 1
        if age > max_runtime:
            logger.warning("Failing stuck job %s type=%s (runtime %.0fs)", job.id, job.job_type, age)
            complete_job(session, job, "failed", f"exceeded max runtime ({int(age)}s)")
            failed += 1
            continue
        recover_all = os.environ.get("JOB_RECOVER_ON_START", "true").lower() in {"1", "true", "yes"}
        if recover_all or (started and started < cutoff):
            logger.warning(
                "Recovering orphaned job %s type=%s (started_at=%s)",
                job.id,
                job.job_type,
                job.started_at,
            )
            job.status = "pending"
            job.started_at = None
            recovered += 1
    return failed + recovered


def drain_queue(
    session: Session,
    *,
    reason: str = "cleared for maintenance",
    statuses: tuple[str, ...] = ("pending", "processing"),
) -> int:
    """Fail all queued/active jobs so a single-worker deploy can start clean."""
    from models import JobRun

    jobs = session.query(JobRun).filter(JobRun.status.in_(statuses)).all()
    for job in jobs:
        logger.warning("Draining job %s type=%s status=%s", job.id, job.job_type, job.status)
        complete_job(session, job, "failed", reason)
    return len(jobs)


def get_job(session: Session, job_id: str) -> JobRun | None:
    return session.get(JobRun, job_id)
