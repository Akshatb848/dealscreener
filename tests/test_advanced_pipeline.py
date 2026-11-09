"""Integration tests for the advanced pipeline + governance services.

Verifies persistence of simulation/cohort/risk-flag/review entities, escalation
behaviour, reproducibility, and that partner overrides are captured without
mutating the deterministic AI score.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import review_service
from advanced_pipeline import run_advanced_pipeline
from models import (
    AgentFinding,
    Base,
    Company,
    EvaluationRun,
    InvestorOverride,
    ReviewItem,
    RiskFlag,
    ScoringResult,
    SimulationRun,
    SimulationScenario,
    SyntheticCohort,
)
from orchestrator_service import run_agents_parallel
from parser_layer import ParsedChunk
from synthesis_service import persist_evaluation, synthesize_evaluation

PITCH = ("SaaS compliance platform ARR 2M moat patent founder ex-google gdpr "
         "board independent subscription enterprise customers traction growth")


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setenv("ENABLE_ADVANCED_PIPELINE", "true")
    monkeypatch.setenv("SIMULATION_ITERATIONS", "40")
    monkeypatch.setenv("SLACK_ALERTS_ENABLED", "false")
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()
    eng.dispose()
    try:
        os.remove(path)
    except OSError:
        pass


def _prepare(session, text=PITCH):
    c = Company(name="PayFlow", deal_id="42", domain="fintech", status="processing")
    session.add(c)
    session.flush()
    run = EvaluationRun(company_id=c.id, status="processing")
    session.add(run)
    session.flush()
    chunks = [ParsedChunk(source_file="deck.pdf", page_index=0, raw_text=text,
                          extraction_method="pdf", confidence_score=0.8)]
    market, financial, founder, nr = run_agents_parallel(chunks, use_live_llm=False)
    ev = synthesize_evaluation(company_id=c.id, company_name=c.name, document_ids=[],
                               evaluation_id=run.id, market=market, financial=financial,
                               founder=founder, needs_review=nr)
    persist_evaluation(session, ev, market=market, financial=financial, founder=founder)
    return c, run, ev, chunks, market, financial, founder


def test_pipeline_persists_all_entities(session):
    c, run, ev, chunks, market, financial, founder = _prepare(session)
    summary = run_advanced_pipeline(session, company=c, evaluation_run=run, evaluation=ev,
                                    chunks=chunks, market=market, financial=financial,
                                    founder=founder, correlation_id="cid")
    session.flush()
    assert session.query(SyntheticCohort).count() == 1
    assert session.query(SimulationRun).count() == 1
    assert session.query(SimulationScenario).count() == 10
    assert session.query(RiskFlag).count() >= 1
    findings = {f.agent_name for f in session.query(AgentFinding).all()}
    for name in ("semantic_matrix_deconstructor", "macro_trend_whisperer",
                 "esg_governance_agent", "portfolio_monitoring_agent", "ic_memo_agent"):
        assert name in findings
    assert summary["ic_memo"] is True


def test_pipeline_reproducible(session):
    c, run, ev, chunks, market, financial, founder = _prepare(session)
    run_advanced_pipeline(session, company=c, evaluation_run=run, evaluation=ev, chunks=chunks,
                          market=market, financial=financial, founder=founder)
    s1 = session.query(SimulationRun).filter_by(evaluation_run_id=run.id).one()

    c2, run2, ev2, chunks2, m2, f2, fo2 = _prepare(session)
    run_advanced_pipeline(session, company=c2, evaluation_run=run2, evaluation=ev2, chunks=chunks2,
                          market=m2, financial=f2, founder=fo2)
    s2 = session.query(SimulationRun).filter_by(evaluation_run_id=run2.id).one()
    # Same seed + same inputs → identical resilience metrics.
    assert s1.resilience_score == s2.resilience_score
    assert s1.failure_probability == s2.failure_probability


def test_low_resilience_escalates_and_creates_review_items(session):
    c, run, ev, chunks, market, financial, founder = _prepare(session)
    run_advanced_pipeline(session, company=c, evaluation_run=run, evaluation=ev, chunks=chunks,
                          market=market, financial=financial, founder=founder)
    session.flush()
    assert run.status == "needs_review"
    assert session.query(ReviewItem).filter_by(company_id=c.id, status="open").count() >= 1


def test_override_records_without_mutating_score(session):
    c, run, ev, chunks, market, financial, founder = _prepare(session)
    run_advanced_pipeline(session, company=c, evaluation_run=run, evaluation=ev, chunks=chunks,
                          market=market, financial=financial, founder=founder)
    session.flush()
    ai_rec = session.query(ScoringResult).filter_by(evaluation_run_id=run.id).one().recommendation
    item = session.query(ReviewItem).filter_by(company_id=c.id, status="open").first()

    result = review_service.resolve_review_item(
        session, review_item_id=item.id, choice="No", reviewer_name="Partner A",
        notes="weak resilience", correlation_id="cid")
    session.flush()
    assert result["override_id"]
    assert session.query(InvestorOverride).filter_by(company_id=c.id).count() == 1
    # AI recommendation immutable.
    assert session.query(ScoringResult).filter_by(evaluation_run_id=run.id).one().recommendation == ai_rec
    assert session.get(ReviewItem, item.id).status == "resolved"
