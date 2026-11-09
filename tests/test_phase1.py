"""Phase 1 tests: database, hashing, parser, workflow states."""

import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db import init_db
from ingestion_service import compute_sha256, ingest_document
from models import Base, DocumentChunk
from parser_layer import ParsedChunk, parse_document
from workflow_states import WorkflowState, is_valid_workflow_state


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


class TestDatabase:
    def test_tables_can_be_created(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            init_db(f"sqlite:///{path}")
            engine = create_engine(f"sqlite:///{path}")
            tables = set(Base.metadata.tables.keys())
            engine.dispose()
            expected = {
                "companies",
                "documents",
                "document_chunks",
                "evaluation_runs",
                "agent_findings",
                "scoring_results",
                "investor_overrides",
                "model_versions",
                "training_examples",
                "job_runs",
                "audit_logs",
            }
            assert expected.issubset(tables)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


class TestHashing:
    def test_sha256_deterministic(self):
        data = b"pitch deck content"
        assert compute_sha256(data) == compute_sha256(data)
        assert len(compute_sha256(data)) == 64

    def test_duplicate_payloads_same_hash(self):
        a = b"same content"
        b = b"same content"
        assert compute_sha256(a) == compute_sha256(b)

    def test_different_payloads_different_hash(self):
        assert compute_sha256(b"a") != compute_sha256(b"b")


class TestParser:
    def test_plain_text_chunk_structure(self, tmp_path):
        f = tmp_path / "deck.txt"
        f.write_text("Hello world.\nLine two.", encoding="utf-8")
        chunks = parse_document(str(f))
        assert len(chunks) >= 1
        chunk = chunks[0]
        assert isinstance(chunk, ParsedChunk)
        assert chunk.source_file == "deck.txt"
        assert chunk.page_index == 0
        assert "Hello world" in chunk.raw_text
        assert chunk.extraction_method == "plain_text"
        assert 0.0 <= chunk.confidence_score <= 1.0

    def test_empty_input_handled(self):
        assert parse_document(b"") == []
        assert parse_document(b"   ") == []

    def test_invalid_path_handled(self):
        assert parse_document("/nonexistent/file.xyz") == []


class TestWorkflowStates:
    def test_valid_states(self):
        for state in WorkflowState:
            assert is_valid_workflow_state(state.value)

    def test_invalid_state(self):
        assert not is_valid_workflow_state("unknown")


class TestIngestion:
    def test_ingest_and_dedup(self, db_session, tmp_path):
        storage = str(tmp_path / "storage")
        data = b"Sample pitch deck text for testing."
        r1 = ingest_document(
            db_session,
            file_bytes=data,
            filename="pitch.txt",
            company_name="Acme Inc",
            deal_id="100",
            storage_dir=storage,
        )
        assert r1.duplicate_detected is False
        assert r1.status in {WorkflowState.COMPLETED.value, WorkflowState.FAILED.value}

        r2 = ingest_document(
            db_session,
            file_bytes=data,
            filename="pitch_copy.txt",
            company_name="Other Inc",
            storage_dir=storage,
        )
        assert r2.duplicate_detected is True
        assert r2.document_id == r1.document_id

    def test_empty_upload_rejected(self, db_session):
        with pytest.raises(ValueError, match="Empty"):
            ingest_document(
                db_session,
                file_bytes=b"",
                filename="empty.txt",
                company_name="X",
            )

    def test_chunks_persisted(self, db_session):
        data = b"Revenue is 5 Cr INR."
        result = ingest_document(
            db_session,
            file_bytes=data,
            filename="metrics.txt",
            company_name="Metrics Co",
        )
        chunks = db_session.query(DocumentChunk).filter_by(document_id=result.document_id).all()
        assert len(chunks) >= 1
