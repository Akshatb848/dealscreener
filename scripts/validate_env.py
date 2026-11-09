#!/usr/bin/env python3
"""Validate production environment variables before deploy."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

FAIL = 1
OK = 0
errors: list[str] = []
warnings: list[str] = []

WEAK_PATTERNS = (
    r"^change-me",
    r"^dealscreener$",
    r"^password$",
    r"^secret$",
    r"^test$",
    r"^1234",
)


def _check_secret(name: str, *, required: bool = True, min_len: int = 16) -> None:
    value = os.environ.get(name, "")
    if not value:
        if required:
            errors.append(f"{name} is required in production")
        else:
            warnings.append(f"{name} is not set")
        return
    if len(value) < min_len:
        errors.append(f"{name} must be at least {min_len} characters")
    for pat in WEAK_PATTERNS:
        if re.search(pat, value, re.I):
            errors.append(f"{name} looks like a placeholder — rotate before deploy")


def validate(*, target: str = "host") -> int:
    from env_config import load_env

    global errors, warnings
    errors = []
    warnings = []

    load_env(docker=(target == "docker"))
    app_env = os.environ.get("APP_ENV", "development")
    production = app_env.lower() == "production"
    db_url = os.environ.get("DATABASE_URL", "")

    if production or target == "docker":
        _check_secret("API_KEY")
        if target == "docker" or target == "host":
            if not db_url.startswith("postgresql"):
                if target == "docker":
                    errors.append("DATABASE_URL must use PostgreSQL in Docker production")
                else:
                    warnings.append(
                        "Host DATABASE_URL is SQLite — OK when using Docker Postgres "
                        "(run: python scripts/validate_env.py --target docker)"
                    )
        if os.environ.get("REDIS_REQUIRED", "").lower() not in {"1", "true", "yes"}:
            warnings.append("REDIS_REQUIRED should be true in production")
        pg_pass = os.environ.get("POSTGRES_PASSWORD", "")
        if pg_pass and pg_pass in {"dealscreener", "change-me"}:
            errors.append("POSTGRES_PASSWORD is a default placeholder — change it")

    if db_url.startswith("postgresql") and "@" in db_url:
        if "change-me" in db_url.lower() or "REPLACE_" in db_url:
            errors.append("DATABASE_URL contains placeholder password")

    for msg in warnings:
        print(f"WARN: {msg}")
    for msg in errors:
        print(f"ERROR: {msg}")

    if errors:
        return FAIL
    print(f"Environment validation passed (target={target})")
    return OK


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Deal Screener environment")
    parser.add_argument(
        "--target",
        choices=("host", "docker"),
        default="host",
        help="host=local scripts; docker=container production (.env.docker)",
    )
    args = parser.parse_args()
    return validate(target=args.target)


if __name__ == "__main__":
    raise SystemExit(main())
