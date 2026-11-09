"""End-to-end pipeline wiring tests: worker → evaluation → scoring, override → training example."""

import asyncio
import json
import os
import tempfile
import uuid

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api_app import app
from db import get_db_session
from ingestion_service import ingest_document
from job_queue import enqueue_job
from models import AuditLog, Base, Company, JobRun, ScoringResult, TrainingExample
from worker import process_job


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


def _ingest_pitch(session, tmp_path, *, deal_id=None):
    content = f"Pitch {uuid.uuid4().hex}. Revenue $2M ARR. Large market. Strong team.".encode()
    return ingest_document(
        session,
        file_bytes=content,
        filename=f"pitch_{uuid.uuid4().hex[:8]}.txt",
        company_name="WireCo",
        deal_id=deal_id,
        storage_dir=str(tmp_path),
    )


def _enqueue_evaluate(session, result):
    job_id = enqueue_job(
        session,
        "evaluate_document",
        {"company_id": result.company_id, "document_id": result.document_id},
        correlation_id=result.correlation_id,
    )
    return session.get(JobRun, job_id)


class TestWorkerEvaluateJob:
    def test_evaluate_job_completes_and_persists_score(self, db_session, tmp_path):
        result = _ingest_pitch(db_session, tmp_path, deal_id="D-100")
        job = _enqueue_evaluate(db_session, result)
        process_job(db_session, job)

        assert job.status == "completed"
        body = json.loads(job.message)
        assert body["evaluation_id"]
        scoring = db_session.query(ScoringResult).all()
        assert len(scoring) == 1
        assert scoring[0].recommendation in {"Yes", "Maybe", "Needs Review", "No"}

    def test_evaluate_job_with_production_autoflush_off(self, tmp_path):
        """Production SessionLocal uses autoflush=False; scoring must still be persisted."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine, autoflush=False)
        session = Session()
        try:
            result = _ingest_pitch(session, tmp_path)
            job = _enqueue_evaluate(session, result)
            process_job(session, job)
            session.commit()
            assert job.status == "completed"
            assert session.query(ScoringResult).count() == 1
        finally:
            session.close()
            engine.dispose()
            try:
                os.remove(path)
            except OSError:
                pass

    def test_unknown_job_type_fails_cleanly(self, db_session):
        job_id = enqueue_job(db_session, "not_a_real_job", {})
        job = db_session.get(JobRun, job_id)
        process_job(db_session, job)
        assert job.status == "failed"
        assert "Unknown job type" in job.message


class TestEvaluationAudit:
    def test_evaluation_completed_audit_logged(self, db_session, tmp_path):
        from evaluation_orchestrator import run_evaluation_for_document

        result = _ingest_pitch(db_session, tmp_path)
        eval_id, status = run_evaluation_for_document(
            db_session,
            company_id=result.company_id,
            document_id=result.document_id,
        )
        audit = (
            db_session.query(AuditLog)
            .filter(
                AuditLog.event_type == "evaluation_completed",
                AuditLog.entity_id == eval_id,
            )
            .all()
        )
        assert len(audit) == 1
        payload = json.loads(audit[0].payload_json)
        assert payload["recommendation"] in {"Yes", "Maybe", "Needs Review", "No"}


class TestOverrideTrainingExample:
    def _client(self, engine):
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

        class Client:
            def post(self, url, **kwargs):
                async def _call():
                    transport = ASGITransport(app=app)
                    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                        return await ac.post(url, **kwargs)

                return asyncio.run(_call())

        return Client()

    def test_override_creates_training_example(self, db_session):
        company = Company(name="OverrideCo")
        db_session.add(company)
        db_session.commit()

        client = self._client(db_session.get_bind())
        try:
            r = client.post(
                "/api/v1/review/override",
                json={
                    "company_id": company.id,
                    "choice": "Yes",
                    "override_reason": "Strong team",
                    "reviewer_name": "Analyst A",
                },
            )
            assert r.status_code == 200
        finally:
            app.dependency_overrides.clear()

        db_session.expire_all()
        examples = db_session.query(TrainingExample).filter_by(company_id=company.id).all()
        assert len(examples) == 1
        assert examples[0].source == "human_override"
        assert examples[0].label == "Yes"
        features = json.loads(examples[0].features_json)
        assert features["override_id"]
        assert examples[0].reviewed_at is not None


class TestLearningLoopDedup:
    def test_override_derived_example_not_double_counted(self, db_session):
        from learning_loop import export_reviewed_cases
        from models import InvestorOverride

        company = Company(name="DedupCo")
        db_session.add(company)
        db_session.flush()

        override = InvestorOverride(company_id=company.id, choice="Yes", override_reason="r")
        db_session.add(override)
        db_session.flush()

        db_session.add(
            TrainingExample(
                company_id=company.id,
                source="human_override",
                label="Yes",
                features_json=json.dumps({"override_id": override.id}),
            )
        )
        db_session.flush()

        cases = export_reviewed_cases(db_session)
        assert len(cases) == 1
