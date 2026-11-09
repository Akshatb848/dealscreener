"""Production architecture integration tests."""

import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from job_queue import claim_next_job, complete_job, drain_queue, enqueue_job, recover_orphaned_jobs
from models import Base, JobRun
from orchestrator_service import inference_route_config, run_agents_parallel
from parser_layer import ParsedChunk


@pytest.fixture
def db_session():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
        session.commit()
    finally:
        session.close()
        engine.dispose()
        try:
            os.remove(path)
        except OSError:
            pass


class TestJobQueue:
    def test_enqueue_and_claim(self, db_session):
        job_id = enqueue_job(db_session, "evaluate_document", {"company_id": "c1", "document_id": "d1"})
        job = claim_next_job(db_session)
        assert job is not None
        assert job.id == job_id
        assert job.status == "processing"
        complete_job(db_session, job, "completed", "ok")
        assert job.status == "completed"

    def test_recover_orphaned_processing_job(self, db_session, monkeypatch):
        from datetime import datetime, timezone

        monkeypatch.setenv("JOB_RECOVER_ON_START", "true")
        job_id = enqueue_job(db_session, "evaluate_document", {"company_id": "c1"})
        job = db_session.get(JobRun, job_id)
        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
        db_session.flush()
        n = recover_orphaned_jobs(db_session)
        assert n == 1
        assert job.status == "pending"
        assert job.started_at is None

    def test_recover_handles_naive_sqlite_timestamps(self, db_session, monkeypatch):
        from datetime import datetime

        monkeypatch.setenv("JOB_RECOVER_ON_START", "true")
        monkeypatch.setenv("JOB_MAX_RUNTIME_SECONDS", "600")
        job_id = enqueue_job(db_session, "evaluate_document", {"company_id": "c1"})
        job = db_session.get(JobRun, job_id)
        job.status = "processing"
        job.started_at = datetime.utcnow()  # naive, as SQLite often returns
        db_session.flush()
        n = recover_orphaned_jobs(db_session)
        assert n == 1
        assert job.status == "pending"

    def test_drain_queue_fails_active_jobs(self, db_session):
        job_id = enqueue_job(db_session, "evaluate_document", {"company_id": "c1"})
        job = db_session.get(JobRun, job_id)
        job.status = "processing"
        db_session.flush()
        n = drain_queue(db_session)
        assert n == 1
        assert job.status == "failed"


class TestOrchestrator:
    def test_parallel_offline_agents(self):
        chunks = [ParsedChunk("deck.txt", 0, "Market is $1B. ARR $2M.", "plain_text", 0.9)]
        market, financial, founder, nr = run_agents_parallel(chunks, use_live_llm=False)
        assert market.market_category == "extracted_offline"
        assert nr is False

    def test_inference_route_config(self):
        cfg = inference_route_config()
        assert "primary_model" in cfg
        assert "fallback_model" in cfg


class TestEnvConfig:
    def test_integration_status_no_secrets(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6380/0")
        monkeypatch.setenv("ASYNC_JOBS", "true")
        monkeypatch.setenv("RUN_LIVE_LLM", "true")
        monkeypatch.setenv("API_KEY", "secret-token")
        monkeypatch.setenv("OLLAMA_PRIMARY_BASE_URL", "http://gpu-ollama:11434")

        from env_config import integration_status

        integration_status.cache_clear()
        status = integration_status()
        assert status["redis_configured"] is True
        assert status["async_jobs"] == "true"
        assert status["live_llm"] == "true"
        assert status["api_key_required"] is True
        assert "secret" not in str(status).lower()
        assert status["primary_ollama"] == "http://gpu-ollama:11434"
        assert status["primary_model"] == os.environ.get("OLLAMA_PRIMARY_MODEL", "")

    def test_config_status_endpoint(self):
        from tests.asgi_client import SyncASGIClient
        from api_app import app

        client = SyncASGIClient(app)
        response = client.get("/api/v1/config/status")
        assert response.status_code == 200
        body = response.json()
        assert "redis_configured" in body
        assert "redis_connected" in body
        assert "api-token" not in str(body)
