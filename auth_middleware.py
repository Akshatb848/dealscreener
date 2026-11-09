"""Optional API key auth for production endpoints."""

from __future__ import annotations

import os

from fastapi import Header, HTTPException

from env_config import load_env


def _api_key() -> str | None:
    load_env()
    return os.environ.get("API_KEY") or None


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    expected = _api_key()
    if not expected:
        return
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

