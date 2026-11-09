"""Phase 5 tests: learning loop, registry, hotswap."""

import os
import pickle
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from learning_loop import export_reviewed_cases, run_learning_loop
from model_loader import SafeDeterministicClassifier, load_active_model, reload_active_model
from model_registry import (
    register_model_version,
    promote_to_active,
    get_active_model_version,
    get_rollback_model_version,
)
from models import Base, Company, InvestorOverride


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


class TestRetrainingGate:
    def test_skipped_below_50(self, db_session):
        company = Company(name="Co")
        db_session.add(company)
        db_session.flush()
        for i in range(10):
            db_session.add(
                InvestorOverride(company_id=company.id, choice="Yes", override_reason="test")
            )
        job = run_learning_loop(db_session, min_cases=50)
        assert job.status == "skipped"
        assert "skipped" in job.message.lower() or "50" in job.message

    def test_allowed_at_50(self, db_session, tmp_path):
        company = Company(name="Co")
        db_session.add(company)
        db_session.flush()
        for i in range(50):
            db_session.add(
                InvestorOverride(company_id=company.id, choice="Yes", override_reason=f"r{i}")
            )
        job = run_learning_loop(db_session, dry_run=True, min_cases=50)
        assert job.status == "dry_run"
        assert job.dry_run is True


class TestModelRegistry:
    def test_candidate_registration(self, db_session, tmp_path):
        pkl = tmp_path / "model.pkl"
        with open(pkl, "wb") as f:
            pickle.dump({"stub": True}, f)
        mv = register_model_version(
            db_session,
            version_label="v1",
            model_path=str(pkl),
            validation_metrics={"accuracy": 0.8},
        )
        assert mv.status == "candidate"

    def test_active_update_and_rollback(self, db_session, tmp_path):
        p1 = tmp_path / "m1.pkl"
        p2 = tmp_path / "m2.pkl"
        for p in (p1, p2):
            with open(p, "wb") as f:
                pickle.dump({"w": 1}, f)
        register_model_version(db_session, version_label="v1", model_path=str(p1), status="candidate")
        promote_to_active(db_session, "v1")
        register_model_version(db_session, version_label="v2", model_path=str(p2), status="candidate")
        promote_to_active(db_session, "v2")
        active = get_active_model_version(db_session)
        rollback = get_rollback_model_version(db_session)
        assert active.version_label == "v2"
        assert rollback.version_label == "v1"


class TestModelLoader:
    def test_load_active_model(self, db_session, tmp_path):
        pkl = tmp_path / "active.pkl"
        with open(pkl, "wb") as f:
            pickle.dump(SafeDeterministicClassifier(), f)
        register_model_version(db_session, version_label="active_v", model_path=str(pkl))
        promote_to_active(db_session, "active_v")
        model, version = load_active_model(db_session, force_reload=True)
        assert version == "active_v"

    def test_failed_load_fallback(self, db_session, monkeypatch):
        monkeypatch.setenv("CLASSIFIER_MODEL_PATH", "/nonexistent/classifier.pkl")
        register_model_version(
            db_session,
            version_label="bad",
            model_path="/nonexistent/model.pkl",
            status="active",
        )
        model, version = reload_active_model(db_session)
        assert version == "safe_deterministic"
        assert hasattr(model, "predict_proba")

    def test_export_reviewed_unique(self, db_session):
        company = Company(name="X")
        db_session.add(company)
        db_session.flush()
        db_session.add(InvestorOverride(company_id=company.id, choice="Maybe"))
        cases = export_reviewed_cases(db_session)
        assert len(cases) == 1
