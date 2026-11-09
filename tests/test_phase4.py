"""Phase 4 tests: FastAPI endpoints, override, review queue."""

import io
import os
import tempfile

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api_app import app
from db import get_db_session
from models import Base, Company, EvaluationRun, InvestorOverride, ScoringResult
from synthesis_service import persist_evaluation, synthesize_evaluation
from tests.asgi_client import SyncASGIClient
from workflow_states import WorkflowState


class TestHealth:
    def test_root(self, client):
        r = client.get("/")
        assert r.status_code == 200
        body = r.json()
        assert body["service"] == "Deal Screener API"
        assert body["links"]["docs"] == "/docs"

    def test_favicon_no_404(self, client):
        r = client.get("/favicon.ico")
        assert r.status_code == 204

    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_readiness(self, client):
        r = client.get("/readiness")
        assert r.status_code == 200
        body = r.json()
        assert body["database"] in {"ok", "sqlite", "postgresql"}

    def test_ops_summary(self, client):
        r = client.get("/api/v1/ops/summary")
        assert r.status_code == 200
        body = r.json()
        assert "pending_jobs" in body
        assert "redis_queue_depth" in body
        assert "recent_jobs" in body


class TestAuth:
    def test_upload_requires_api_key_when_configured(self, client, monkeypatch):
        monkeypatch.setenv("API_KEY", "secret-key")
        data = b"Sample pitch deck content."
        r = client.post(
            "/api/v1/upload",
            files={"file": ("deck.txt", data, "text/plain")},
            data={"company_name": "AuthTest"},
        )
        assert r.status_code == 401

        r2 = client.post(
            "/api/v1/upload",
            files={"file": ("deck.txt", data, "text/plain")},
            data={"company_name": "AuthTest"},
            headers={"X-API-Key": "secret-key"},
        )
        assert r2.status_code == 200


class TestUpload:
    def test_upload_valid(self, client):
        data = b"Sample pitch deck with revenue of $2M."
        r = client.post(
            "/api/v1/upload",
            data={"company_name": "Acme", "deal_id": "1"},
            files={"file": ("pitch.txt", io.BytesIO(data), "text/plain")},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["duplicate_detected"] is False
        assert "correlation_id" in body
        assert "document_id" in body

    def test_duplicate_idempotent(self, client):
        data = b"Identical content for dedup test."
        r1 = client.post(
            "/api/v1/upload",
            data={"company_name": "A"},
            files={"file": ("a.txt", io.BytesIO(data), "text/plain")},
        )
        r2 = client.post(
            "/api/v1/upload",
            data={"company_name": "B"},
            files={"file": ("b.txt", io.BytesIO(data), "text/plain")},
        )
        assert r1.json()["document_id"] == r2.json()["document_id"]
        assert r2.json()["duplicate_detected"] is True


class TestStatusAndOverride:
    def test_status_structure(self, client):
        data = b"Status test content."
        up = client.post(
            "/api/v1/upload",
            data={"company_name": "StatusCo"},
            files={"file": ("s.txt", io.BytesIO(data), "text/plain")},
        )
        company_id = up.json()["company_id"]
        r = client.get(f"/api/v1/status/{company_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["company_id"] == company_id
        assert "status" in body

    def test_override_stores_without_overwriting_ai(self, client):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        company = Company(name="OverrideCo", status="completed")
        session.add(company)
        session.flush()
        run = EvaluationRun(company_id=company.id, status=WorkflowState.COMPLETED.value)
        session.add(run)
        session.flush()
        evaluation = synthesize_evaluation(
            company_id=company.id,
            company_name=company.name,
            document_ids=[],
            evaluation_id=run.id,
            market=None,
            financial=None,
            founder=None,
        )
        persist_evaluation(session, evaluation)
        session.commit()
        ai_recommendation = evaluation.recommendation
        ai_score = evaluation.deterministic_score

        def override_get_db():
            s = Session()
            try:
                yield s
                s.commit()
            finally:
                s.close()

        app.dependency_overrides[get_db_session] = override_get_db
        api = SyncASGIClient(app)
        r = api.post(
            "/api/v1/review/override",
            json={
                "company_id": company.id,
                "choice": "Yes",
                "override_reason": "Strong team",
                "reviewer_name": "Analyst",
                "evaluation_id": run.id,
            },
        )
        assert r.status_code == 200
        scoring = session.query(ScoringResult).filter_by(evaluation_run_id=run.id).one()
        assert scoring.recommendation == ai_recommendation
        assert scoring.final_score == ai_score
        override = session.query(InvestorOverride).filter_by(company_id=company.id).one()
        assert override.choice == "Yes"
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()
        try:
            os.remove(path)
        except OSError:
            pass


class TestReviewQueue:
    def test_review_queue_needs_review(self, client):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        company = Company(name="ReviewCo", status=WorkflowState.NEEDS_REVIEW.value)
        session.add(company)
        session.flush()
        run = EvaluationRun(company_id=company.id, status=WorkflowState.NEEDS_REVIEW.value)
        session.add(run)
        session.commit()

        def override_get_db():
            s = Session()
            try:
                yield s
                s.commit()
            finally:
                s.close()

        app.dependency_overrides[get_db_session] = override_get_db
        api = SyncASGIClient(app)
        r = api.get("/api/v1/review/queue")
        assert r.status_code == 200
        assert r.json()["count"] >= 1
        app.dependency_overrides.clear()
        session.close()
        engine.dispose()
        try:
            os.remove(path)
        except OSError:
            pass
