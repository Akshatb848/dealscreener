"""Production Redis client: pooled connections, health checks, queue helpers."""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

from env_config import load_env

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_KEY = "dealscreener:jobs"


def get_redis_url() -> str | None:
    load_env()
    explicit = os.environ.get("REDIS_URL")
    if explicit:
        return explicit
    password = os.environ.get("REDIS_PASSWORD")
    host = os.environ.get("REDIS_HOST", "127.0.0.1")
    port = os.environ.get("REDIS_PORT", "6380")
    db = os.environ.get("REDIS_DB", "0")
    if password:
        return f"redis://:{quote(password, safe='')}@{host}:{port}/{db}"
    return f"redis://{host}:{port}/{db}"


def get_queue_key() -> str:
    load_env()
    return os.environ.get("REDIS_QUEUE_KEY", DEFAULT_QUEUE_KEY)


def _client_options(*, blocking: bool = False) -> dict[str, Any]:
    """Build redis-py pool options.

    Blocking commands (BRPOP) need socket_timeout=None or > block timeout.
    Short socket_timeout (default 5s) causes spurious 'Timeout reading from socket'
    when BRPOP waits for jobs.
    """
    load_env()
    opts: dict[str, Any] = {
        "decode_responses": True,
        "socket_connect_timeout": float(os.environ.get("REDIS_CONNECT_TIMEOUT", "3")),
        "health_check_interval": int(os.environ.get("REDIS_HEALTH_CHECK_INTERVAL", "30")),
    }
    if blocking:
        block_socket = os.environ.get("REDIS_BLOCK_SOCKET_TIMEOUT", "").strip()
        if block_socket:
            opts["socket_timeout"] = float(block_socket)
        # else: omit socket_timeout so BRPOP can block without socket read timeout
        opts["retry_on_timeout"] = False
    else:
        opts["socket_timeout"] = float(os.environ.get("REDIS_SOCKET_TIMEOUT", "5"))
        opts["retry_on_timeout"] = True
    return opts


@lru_cache(maxsize=2)
def get_redis_pool(*, blocking: bool = False):
    url = get_redis_url()
    if not url:
        return None
    try:
        import redis

        return redis.ConnectionPool.from_url(url, **_client_options(blocking=blocking))
    except Exception as exc:
        logger.warning("Redis pool init failed: %s", exc)
        return None


def get_redis_client(*, blocking: bool = False):
    pool = get_redis_pool(blocking=blocking)
    if pool is None:
        return None
    try:
        import redis

        return redis.Redis(connection_pool=pool)
    except Exception as exc:
        logger.warning("Redis client init failed: %s", exc)
        return None


def reset_redis_cache() -> None:
    get_redis_pool.cache_clear()


LOCK_KEY_CRM_RETRY = "dealscreener:locks:crm_retry"
WORKER_HEARTBEAT_KEY = "dealscreener:worker:heartbeat"


def _heartbeat_file() -> Path:
    load_env()
    raw = os.environ.get("STORAGE_DIR", "storage")
    root = Path(raw)
    if not root.is_absolute():
        root = Path(os.getcwd()) / root
    return root.resolve() / "worker_heartbeat.txt"


def _read_file_heartbeat(*, ttl: int, worker_id: str) -> dict[str, Any] | None:
    import time

    hb = _heartbeat_file()
    if not hb.is_file():
        return None
    try:
        ts = int(hb.read_text(encoding="utf-8").strip())
        age = max(0, int(time.time()) - ts)
        return {
            "configured": True,
            "worker_id": worker_id,
            "alive": age <= ttl,
            "last_seen_seconds_ago": age,
            "backend": "file",
            "path": str(hb),
        }
    except Exception as exc:
        return {
            "configured": True,
            "worker_id": worker_id,
            "alive": False,
            "error": str(exc),
            "backend": "file",
            "path": str(hb),
        }


def set_worker_heartbeat(*, worker_id: str | None = None) -> None:
    """Record worker liveness in a local file and Redis when available."""
    load_env()
    import time

    ts = str(int(time.time()))
    ttl = int(os.environ.get("WORKER_HEARTBEAT_TTL_SECONDS", "120"))
    wid = worker_id or os.environ.get("WORKER_ID", "default")

    try:
        hb = _heartbeat_file()
        hb.parent.mkdir(parents=True, exist_ok=True)
        hb.write_text(ts, encoding="utf-8")
    except Exception as exc:
        logger.warning("Worker heartbeat file failed: %s", exc)

    ok, _ = ping_redis()
    if not ok:
        return
    client = get_redis_client(blocking=False)
    if client is None:
        return
    try:
        client.setex(f"{WORKER_HEARTBEAT_KEY}:{wid}", ttl, ts)
    except Exception as exc:
        logger.debug("Worker heartbeat redis failed: %s", exc)


def worker_heartbeat_status(*, worker_id: str | None = None) -> dict[str, Any]:
    """Return worker heartbeat age for ops/monitoring."""
    load_env()

    wid = worker_id or os.environ.get("WORKER_ID", "default")
    ttl = int(os.environ.get("WORKER_HEARTBEAT_TTL_SECONDS", "120"))
    file_status = _read_file_heartbeat(ttl=ttl, worker_id=wid)

    ok, _ = ping_redis()
    if ok:
        client = get_redis_client(blocking=False)
        if client is not None:
            try:
                raw = client.get(f"{WORKER_HEARTBEAT_KEY}:{wid}")
                if raw:
                    import time

                    ts = int(raw)
                    age = max(0, int(time.time()) - ts)
                    return {
                        "configured": True,
                        "worker_id": wid,
                        "alive": age <= ttl,
                        "last_seen_seconds_ago": age,
                        "backend": "redis",
                    }
            except Exception as exc:
                if file_status is not None:
                    file_status["redis_error"] = str(exc)
                    return file_status
                return {
                    "configured": True,
                    "worker_id": wid,
                    "alive": False,
                    "error": str(exc),
                    "backend": "redis",
                }

    if file_status is not None:
        return file_status

    return {"configured": False, "worker_id": wid, "alive": False, "backend": "none"}


def acquire_lock(key: str, *, ttl_seconds: int = 300) -> bool:
    """
    Best-effort distributed lock (SET NX EX) for multi-worker coordination.
    Returns True when acquired. Degrades open (True) when Redis is absent —
    single-instance DB-fallback mode does not need cross-process locking.
    """
    client = get_redis_client(blocking=False)
    if client is None:
        return True
    try:
        return bool(client.set(key, "1", nx=True, ex=ttl_seconds))
    except Exception as exc:
        logger.warning("Redis lock %s unavailable (%s); proceeding unlocked", key, exc)
        return True


def release_lock(key: str) -> None:
    client = get_redis_client(blocking=False)
    if client is None:
        return
    try:
        client.delete(key)
    except Exception as exc:
        logger.warning("Redis lock release %s failed: %s", key, exc)


def ping_redis() -> tuple[bool, str]:
    client = get_redis_client(blocking=False)
    if client is None:
        return False, "not_configured"
    try:
        client.ping()
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def queue_depth() -> int | None:
    client = get_redis_client(blocking=False)
    if client is None:
        return None
    try:
        return int(client.llen(get_queue_key()))
    except Exception:
        return None


def redis_status() -> dict[str, Any]:
    ok, detail = ping_redis()
    depth = queue_depth() if ok else None
    return {
        "configured": bool(get_redis_url()),
        "connected": ok,
        "detail": detail,
        "queue_key": get_queue_key(),
        "queue_depth": depth,
        "url_host": _safe_redis_host(),
    }


def _safe_redis_host() -> str:
    url = get_redis_url() or ""
    if "@" in url:
        return url.split("@", 1)[1]
    return url.replace("redis://", "")


def redis_required() -> bool:
    load_env()
    explicit = os.environ.get("REDIS_REQUIRED", "").strip().lower()
    if explicit in {"0", "false", "no"}:
        return False
    if explicit in {"1", "true", "yes"}:
        return True
    async_jobs = os.environ.get("ASYNC_JOBS", "false").lower() in {"1", "true", "yes"}
    production = os.environ.get("APP_ENV", "").lower() == "production"
    return async_jobs and production
