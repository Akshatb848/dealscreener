"""Tests for the advanced analytic agents + Human-Synapse Interceptor.

Covers schema validity, determinism, missing-data handling, and that macro
outputs are marked unverified (no hallucinated, source-unbacked claims).
"""

from __future__ import annotations

from agent_schemas_advanced import (
    ESGGovernanceOutput,
    InterceptorDecision,
    MacroTrendOutput,
    PortfolioMonitoringOutput,
    SemanticMatrixOutput,
    THESIS_DIMENSIONS,
)
from agents.esg_governance_agent import ESGGovernanceAgent
from agents.human_synapse_interceptor import HumanSynapseInterceptor
from agents.macro_trend_whisperer import MacroTrendWhisperer
from agents.portfolio_monitoring_agent import PortfolioMonitoringAgent
from agents.semantic_matrix_deconstructor import SemanticMatrixDeconstructor
from parser_layer import ParsedChunk

PITCH = (
    "Our SaaS platform solves a costly compliance problem for enterprise customers. "
    "ARR $2M and growing. Patent-pending data moat with network effects. "
    "Founder is ex-Google PhD. GDPR compliant. Board has two independent directors. "
    "Subscription pricing. Strong traction and retention."
)


def _chunks(text=PITCH):
    return [ParsedChunk(source_file="deck.pdf", page_index=0, raw_text=text,
                        extraction_method="pdf", confidence_score=0.8)]


class TestSemanticMatrix:
    def test_schema_and_dimensions(self):
        out = SemanticMatrixDeconstructor().run(_chunks())
        assert isinstance(out, SemanticMatrixOutput)
        assert set(out.thesis_dimensions) == set(THESIS_DIMENSIONS)
        assert all(0.0 <= v <= 1.0 for v in out.thesis_dimensions.values())
        assert out.moat  # moat/patent terms present

    def test_deterministic(self):
        a = SemanticMatrixDeconstructor().run(_chunks())
        b = SemanticMatrixDeconstructor().run(_chunks())
        assert a.model_dump() == b.model_dump()

    def test_empty_lists_missing(self):
        out = SemanticMatrixDeconstructor().run([])
        assert "founder" in out.missing_data
        assert out.thesis_dimensions["moat_defensibility"] == 0.0


class TestMacroTrend:
    def test_unverified_without_connector(self):
        out = MacroTrendWhisperer().run(_chunks())
        assert isinstance(out, MacroTrendOutput)
        assert out.unverified is True  # no live data → must be flagged unverified
        assert "gdpr" in out.regulatory_signals

    def test_deterministic(self):
        assert MacroTrendWhisperer().run(_chunks()).model_dump() == \
            MacroTrendWhisperer().run(_chunks()).model_dump()


class TestESG:
    def test_score_range_and_gaps(self):
        out = ESGGovernanceAgent().run(_chunks())
        assert isinstance(out, ESGGovernanceOutput)
        assert 0.0 <= out.governance_risk_score <= 100.0

    def test_empty_high_uncertainty(self):
        out = ESGGovernanceAgent().run([])
        assert out.missing_data


class TestPortfolio:
    def test_flag_and_schema(self):
        out = PortfolioMonitoringAgent().run(_chunks(), context={"is_portfolio_company": True})
        assert isinstance(out, PortfolioMonitoringOutput)
        assert out.is_portfolio_company is True

    def test_runway_risk_detected(self):
        out = PortfolioMonitoringAgent().run(_chunks("We are running out of cash, short runway, bridge needed."))
        assert out.runway_risk == "high"
        assert out.red_flags


class TestInterceptor:
    def test_escalates_on_low_confidence_and_resilience(self):
        d = HumanSynapseInterceptor().evaluate(
            confidence_score=0.3, missing_information=["x", "y", "z", "w"], red_flags=[],
            recommendation="Maybe", resilience_score=20.0, governance_risk_score=80.0,
        )
        assert isinstance(d, InterceptorDecision)
        assert d.escalate is True
        codes = {r.reason_code for r in d.reasons}
        assert {"low_confidence", "missing_evidence", "low_resilience", "governance_risk"} <= codes
        assert d.highest_severity in {"high", "critical"}

    def test_no_escalation_when_clean(self):
        d = HumanSynapseInterceptor().evaluate(
            confidence_score=0.9, missing_information=[], red_flags=[],
            recommendation="Yes", resilience_score=80.0, governance_risk_score=20.0,
        )
        assert d.escalate is False
        assert d.reasons == []
