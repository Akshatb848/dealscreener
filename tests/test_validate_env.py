"""Tests for validate_env.py target modes."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "validate_env",
    _ROOT / "scripts" / "validate_env.py",
)
validate_env = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(validate_env)


def test_validate_host_allows_sqlite_with_warning(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_KEY", "a" * 32)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./dealscreener.db")

    code = validate_env.validate(target="host")
    assert code == 0


def test_validate_docker_requires_postgresql(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("API_KEY", "a" * 32)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./dealscreener.db")
    monkeypatch.setattr("env_config.load_env", lambda **kwargs: None)

    code = validate_env.validate(target="docker")
    assert code == 1
