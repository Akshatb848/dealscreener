"""Worker heartbeat when Redis is configured but unavailable."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from redis_client import reset_redis_cache, set_worker_heartbeat, worker_heartbeat_status


@pytest.fixture(autouse=True)
def _reset_redis(monkeypatch):
    monkeypatch.setenv("REDIS_REQUIRED", "false")
    reset_redis_cache()
    yield
    reset_redis_cache()


def test_file_heartbeat_when_redis_down(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STORAGE_DIR", "storage")

    mock_client = MagicMock()
    mock_client.get.side_effect = ConnectionError("10061 refused")
    mock_client.setex.side_effect = ConnectionError("10061 refused")

    def fake_get_client(*, blocking=False):
        return mock_client

    monkeypatch.setattr("redis_client.get_redis_client", fake_get_client)
    monkeypatch.setattr("redis_client.ping_redis", lambda: (False, "refused"))

    set_worker_heartbeat()
    status = worker_heartbeat_status()

    assert status["alive"] is True
    assert status["backend"] == "file"
    assert Path(status["path"]).is_file()


def test_file_heartbeat_survives_redis_get_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STORAGE_DIR", "storage")

    mock_client = MagicMock()
    mock_client.get.side_effect = ConnectionError("10061 refused")

    monkeypatch.setattr("redis_client.get_redis_client", lambda **kw: mock_client)
    monkeypatch.setattr("redis_client.ping_redis", lambda: (True, "ok"))

    set_worker_heartbeat()
    status = worker_heartbeat_status()

    assert status["alive"] is True
    assert status["backend"] == "file"
