"""Shared pytest fixtures."""

from __future__ import annotations

import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api_app import app
from db import get_db_session
from models import Base


@pytest.fixture(autouse=True)
def isolate_production_env(monkeypatch):
    """Tests must not require live Redis/Ollama from developer .env."""
    monkeypatch.setenv("REDIS_REQUIRED", "false")
    monkeypatch.setenv("ASYNC_JOBS", "false")
    monkeypatch.setenv("RUN_LIVE_LLM", "false")
    # Advanced multi-agent/simulation pipeline is exercised by its own tests;
    # keep the legacy suite focused on core behaviour (and fast).
    monkeypatch.setenv("ENABLE_ADVANCED_PIPELINE", "false")
    monkeypatch.setenv("SIMULATION_ITERATIONS", "20")
    monkeypatch.delenv("API_KEY", raising=False)

    from env_config import integration_status
    from redis_client import reset_redis_cache

    reset_redis_cache()
    integration_status.cache_clear()
    yield
    reset_redis_cache()
    integration_status.cache_clear()


@pytest.fixture
def client():
    """FastAPI test client backed by an isolated temp SQLite database."""
    from tests.asgi_client import SyncASGIClient

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)

    def override_get_db():
        session = TestSession()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_get_db
    yield SyncASGIClient(app)
    app.dependency_overrides.clear()
    engine.dispose()
    try:
        os.remove(path)
    except OSError:
        pass
