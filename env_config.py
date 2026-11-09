"""Centralized environment loading and integration settings."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_LOADED = False
_DOCKER_LOADED = False


def load_env(*, docker: bool = False) -> None:
    """Load .env from project root; optionally overlay .env.docker for containers."""
    global _LOADED, _DOCKER_LOADED
    try:
        from dotenv import load_dotenv

        if not _LOADED:
            load_dotenv(_ROOT / ".env", override=False)
            _LOADED = True
        if (docker or os.environ.get("APP_DOCKER_ENV", "").lower() in {"1", "true", "yes"}) and not _DOCKER_LOADED:
            docker_env = _ROOT / ".env.docker"
            if docker_env.is_file():
                load_dotenv(docker_env, override=True)
            _DOCKER_LOADED = True
    except ImportError:
        pass


def require_env(name: str) -> str:
    load_env()
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


@lru_cache(maxsize=1)
def integration_status() -> dict[str, bool | str | int | None]:
    load_env()
    from redis_client import redis_status

    redis = redis_status()
    return {
        "app_env": os.environ.get("APP_ENV", "development"),
        "api_key_required": bool(os.environ.get("API_KEY")),
        "redis_configured": redis["configured"],
        "redis_connected": redis["connected"],
        "redis_queue_depth": redis["queue_depth"],
        "async_jobs": os.environ.get("ASYNC_JOBS", "false"),
        "live_llm": os.environ.get("RUN_LIVE_LLM", "false"),
        "database": "postgresql" if get_database_url().startswith("postgresql") else "sqlite",
        "primary_ollama": os.environ.get("OLLAMA_PRIMARY_BASE_URL") or os.environ.get("OLLAMA_BASE_URL", ""),
        "primary_model": os.environ.get("OLLAMA_PRIMARY_MODEL", ""),
        "fallback_ollama": os.environ.get("OLLAMA_FALLBACK_BASE_URL", ""),
        "fallback_model": os.environ.get("OLLAMA_FALLBACK_MODEL", ""),
    }


def get_database_url() -> str:
    load_env()
    return os.environ.get("DATABASE_URL", "sqlite:///./dealscreener.db")
