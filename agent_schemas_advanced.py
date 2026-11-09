"""Strict Pydantic schemas for the advanced multi-agent layer.

Every advanced agent returns a schema-valid object. Outputs that cannot be
grounded in source chunks are surfaced via ``missing_data`` / ``unverified``
rather than being fabricated (no hallucinated investment facts).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from agent_schemas import EvidenceItem

Severity = Literal["low", "medium", "high", "critical"]


# --- Agent 1: Semantic Matrix Deconstructor ---------------------------------

# The 12 structured thesis dimensions (the "semantic matrix").
THESIS_DIMENSIONS: tuple[str, ...] = (
    "problem_clarity",
    "product_strength",
    "market_attractiveness",
    "moat_defensibility",
    "customer_pull",
    "revenue_model_quality",
    "traction_evidence",
    "founder_capability",
    "capital_efficiency",
    "timing",
    "competitive_intensity",
    "execution_risk",
)


class SemanticMatrixOutput(BaseModel):
    problem: str | None = None
    product: str | None = None
    market: str | None = None
    moat: str | None = None
    customer: str | None = None
    revenue_model: str | None = None
    traction: str | None = None
    founder: str | None = None
    risks: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    # 12-dimensional thesis vector, each scored 0.0–1.0 deterministically.
    thesis_dimensions: dict[str, float] = Field(default_factory=dict)
    evidence: list[EvidenceItem] = Field(default_factory=list)


# --- Agent 5: Macro Trend Whisperer -----------------------------------------


class MacroTrendOutput(BaseModel):
    macro_signals: list[str] = Field(default_factory=list)
    regulatory_signals: list[str] = Field(default_factory=list)
    technology_shifts: list[str] = Field(default_factory=list)
    sector_tailwinds: list[str] = Field(default_factory=list)
    sector_headwinds: list[str] = Field(default_factory=list)
    supply_chain_signals: list[str] = Field(default_factory=list)
    policy_changes: list[str] = Field(default_factory=list)
    silent_competitors: list[str] = Field(default_factory=list)
    # MVP runs without live external data — outputs are clearly marked unverified.
    unverified: bool = True
    sources: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)


# --- Agent 8: ESG & Governance Agent ----------------------------------------


class ESGGovernanceOutput(BaseModel):
    esg_summary: str | None = None
    environmental_signals: list[str] = Field(default_factory=list)
    social_signals: list[str] = Field(default_factory=list)
    governance_gaps: list[str] = Field(default_factory=list)
    compliance_risks: list[str] = Field(default_factory=list)
    board_readiness: str | None = None
    founder_policies: list[str] = Field(default_factory=list)
    documentation_maturity: str | None = None
    red_flags: list[str] = Field(default_factory=list)
    governance_risk_score: float = Field(default=0.0, ge=0.0, le=100.0)
    missing_data: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)


# --- Agent 9: Portfolio Monitoring Agent ------------------------------------


class PortfolioMonitoringOutput(BaseModel):
    is_portfolio_company: bool = False
    update_quality: str | None = None
    runway_risk: Severity | None = None
    growth_trend: str | None = None
    governance_risk: Severity | None = None
    follow_on_readiness: str | None = None
    exit_readiness: str | None = None
    red_flags: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)


# --- Agent 11: Human-Synapse Interceptor ------------------------------------


class EscalationReason(BaseModel):
    reason_code: str
    severity: Severity = "medium"
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)


class InterceptorDecision(BaseModel):
    escalate: bool = False
    highest_severity: Severity | None = None
    reasons: list[EscalationReason] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


# --- Agent 10: IC Memo Agent ------------------------------------------------


class ICMemoScorecard(BaseModel):
    scoring_version: str
    final_score: float
    recommendation: str
    confidence_score: float
    sub_scores: dict[str, float] = Field(default_factory=dict)


class ICMemoSimulationSummary(BaseModel):
    engine_version: str
    seed: int
    iterations: int
    resilience_score: float
    failure_probability: float
    moat_sensitivity: float
    founder_adaptability_index: float
    capital_efficiency_under_stress: float
    weakest_scenarios: list[str] = Field(default_factory=list)
    risk_narrative: str | None = None


class ICMemoOutput(BaseModel):
    company_id: str
    company_name: str
    evaluation_id: str
    title: str
    executive_summary: str
    business_thesis: str
    market_and_moat: str
    financials: str
    founder_assessment: str
    macro_context: str
    esg_and_governance: str
    key_risks: list[str] = Field(default_factory=list)
    scorecard: ICMemoScorecard
    simulation_summary: ICMemoSimulationSummary | None = None
    review_flags: list[str] = Field(default_factory=list)
    recommendation: str
    recommendation_reason: str
    decision_authority: str = "Investment Committee (human)"
    disclaimer: str = (
        "AI-generated decision-support memo. Scores are deterministic; the AI does not "
        "make the investment decision. Final approval, rejection, or override rests with "
        "the investment partners. Macro/market claims marked unverified require human confirmation."
    )
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
