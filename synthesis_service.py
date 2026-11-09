"""Aggregate agent outputs into CompanyEvaluationSchema and persist scoring."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from agent_schemas import (
    FinancialMetricsAgentOutput,
    FounderMarketFitAgentOutput,
    MarketMoatAgentOutput,
)
from evaluation_schema import CompanyEvaluationSchema
from models import AgentFinding, EvaluationRun, ScoringResult
from scoring_logic import SCORING_VERSION, compute_score
from workflow_states import WorkflowState


def _summarize_market(o: MarketMoatAgentOutput) -> str:
    parts = []
    if o.market_category:
        parts.append(f"Category: {o.market_category}")
    if o.tam:
        parts.append(f"TAM: {o.tam}")
    if o.competitive_landscape:
        parts.append(f"Competition: {o.competitive_landscape}")
    return "; ".join(parts) or "Insufficient market data extracted."


def _summarize_financial(o: FinancialMetricsAgentOutput) -> str:
    parts = []
    for field in ("arr", "mrr", "revenue", "burn", "runway", "funding_ask"):
        val = getattr(o, field, None)
        if val:
            parts.append(f"{field.upper()}: {val}")
    return "; ".join(parts) or "Insufficient financial data extracted."


def _summarize_founder(o: FounderMarketFitAgentOutput) -> str:
    names = ", ".join(o.founder_names) if o.founder_names else "Unknown"
    bg = "; ".join(o.founder_backgrounds[:2]) if o.founder_backgrounds else "No background cited"
    return f"Founders: {names}. {bg}"


def _collect_missing(*outputs) -> list[str]:
    missing: list[str] = []
    for o in outputs:
        if o is not None:
            missing.extend(o.missing_variables)
    return list(dict.fromkeys(missing))


def _collect_evidence(*outputs) -> list:
    evidence = []
    for o in outputs:
        if o is not None:
            evidence.extend(o.evidence)
    return evidence


def _avg_confidence(evidence: list) -> float:
    if not evidence:
        return 0.5
    return sum(e.confidence_score for e in evidence) / len(evidence)


def synthesize_evaluation(
    *,
    company_id: str,
    company_name: str,
    document_ids: list[str],
    evaluation_id: str,
    market: MarketMoatAgentOutput | None,
    financial: FinancialMetricsAgentOutput | None,
    founder: FounderMarketFitAgentOutput | None,
    red_flags: list[str] | None = None,
    model_versions_used: dict[str, str] | None = None,
    needs_review: bool = False,
) -> CompanyEvaluationSchema:
    """Build evaluation schema; recommendation comes from deterministic scoring only."""
    market = market or MarketMoatAgentOutput(missing_variables=["market_moat"])
    financial = financial or FinancialMetricsAgentOutput(missing_variables=["financial"])
    founder = founder or FounderMarketFitAgentOutput(missing_variables=["founder"])

    missing = _collect_missing(market, financial, founder)
    evidence = _collect_evidence(market, financial, founder)
    confidence = _avg_confidence(evidence)

    extracted: dict[str, Any] = {}
    if financial.normalized_values:
        extracted["financial_normalized"] = financial.normalized_values

    breakdown = compute_score(
        confidence_score=confidence,
        missing_variables=missing,
        red_flags=red_flags or [],
        evidence_quality_score=confidence,
    )

    recommendation = breakdown.recommendation
    reason = breakdown.recommendation_reason
    if needs_review and recommendation != "No":
        recommendation = "Needs Review"
        reason = "Structured validation failed; routed to human review."

    return CompanyEvaluationSchema(
        company_id=company_id,
        company_name=company_name,
        document_ids=document_ids,
        evaluation_id=evaluation_id,
        market_moat_summary=_summarize_market(market),
        financial_metrics_summary=_summarize_financial(financial),
        founder_market_fit_summary=_summarize_founder(founder),
        extracted_metrics=extracted,
        red_flags=red_flags or [],
        missing_information=missing,
        evidence=evidence,
        confidence_score=confidence,
        deterministic_score=breakdown.final_score,
        recommendation=recommendation,
        recommendation_reason=reason,
        model_versions_used=model_versions_used or {},
        scoring_version=SCORING_VERSION,
    )


def persist_evaluation(
    session: Session,
    evaluation: CompanyEvaluationSchema,
    *,
    market: MarketMoatAgentOutput | None = None,
    financial: FinancialMetricsAgentOutput | None = None,
    founder: FounderMarketFitAgentOutput | None = None,
) -> ScoringResult:
    """Persist agent findings and scoring result to database."""
    run = session.get(EvaluationRun, evaluation.evaluation_id)
    if run is None:
        raise ValueError(f"EvaluationRun {evaluation.evaluation_id} not found")

    for name, output in [
        ("market_moat", market),
        ("financial_metrics", financial),
        ("founder_market_fit", founder),
    ]:
        if output is not None:
            session.add(
                AgentFinding(
                    evaluation_run_id=evaluation.evaluation_id,
                    agent_name=name,
                    payload_json=output.model_dump_json(),
                )
            )

    breakdown = compute_score(
        confidence_score=evaluation.confidence_score,
        missing_variables=evaluation.missing_information,
        red_flags=evaluation.red_flags,
    )

    scoring = ScoringResult(
        evaluation_run_id=evaluation.evaluation_id,
        scoring_version=evaluation.scoring_version,
        base_score=breakdown.base_score,
        confidence_weight=breakdown.confidence_weight,
        missing_data_penalty=breakdown.missing_data_penalty,
        red_flag_penalty=breakdown.red_flag_penalty,
        evidence_quality_score=breakdown.evidence_quality_score,
        first_call_probability=breakdown.first_call_probability,
        final_score=breakdown.final_score,
        recommendation=evaluation.recommendation,
        recommendation_reason=evaluation.recommendation_reason,
        payload_json=evaluation.model_dump_json(),
    )
    session.add(scoring)

    run.status = (
        WorkflowState.NEEDS_REVIEW.value
        if evaluation.recommendation == "Needs Review"
        else WorkflowState.COMPLETED.value
    )
    return scoring
