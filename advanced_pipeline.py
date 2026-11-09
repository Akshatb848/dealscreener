"""Advanced multi-agent + simulation orchestration.

Runs after the core (Market/Financial/Founder → synthesis → deterministic
scoring) stage. It is additive and **degrades gracefully**: any failure is
caught by the caller so core scoring is never compromised. The AI never decides;
escalations route to human partners via ReviewItems.

Flow:
  semantic deconstruction → macro / ESG / portfolio agents
  → resilience profile → synthetic cohort → stochastic shock simulation
  → risk flags → Human-Synapse Interceptor → ReviewItems / Slack
  → IC memo (persisted)
"""

from __future__ import annotations

import json
import logging
import os

from sqlalchemy.orm import Session

import crm_event_service
import review_service
import slack_service
from agent_schemas_advanced import ICMemoSimulationSummary
from agents.esg_governance_agent import ESGGovernanceAgent
from agents.human_synapse_interceptor import HumanSynapseInterceptor
from agents.ic_memo_agent import ICMemoAgent
from agents.macro_trend_whisperer import MacroTrendWhisperer
from agents.portfolio_monitoring_agent import PortfolioMonitoringAgent
from agents.semantic_matrix_deconstructor import SemanticMatrixDeconstructor
from models import AgentFinding, Company, EvaluationRun, SimulationRun, SimulationScenario, SyntheticCohort
from scoring_logic import SCORING_VERSION
from simulation import DealResilienceProfile, StochasticShockSimulator, SyntheticCohortInjector
from workflow_states import WorkflowState

logger = logging.getLogger(__name__)


def advanced_enabled() -> bool:
    return os.environ.get("ENABLE_ADVANCED_PIPELINE", "true").lower() in {"1", "true", "yes"}


def _sim_config() -> dict:
    return {
        "seed": int(os.environ.get("SIMULATION_SEED", "1337")),
        "iterations": int(os.environ.get("SIMULATION_ITERATIONS", "200")),
        "horizon_years": int(os.environ.get("SIMULATION_HORIZON_YEARS", "5")),
        "cohort_size": int(os.environ.get("SYNTHETIC_COHORT_SIZE", "25")),
    }


def _persist_finding(session: Session, evaluation_id: str, name: str, model) -> None:
    session.add(AgentFinding(
        evaluation_run_id=evaluation_id, agent_name=name, payload_json=model.model_dump_json(),
    ))


def _resilience_profile(semantic, esg, financial) -> DealResilienceProfile:
    dims = semantic.thesis_dimensions if semantic else {}

    def d(name: str, default: float = 0.5) -> float:
        return float(dims.get(name, default))

    reg_ready = 0.65 if (esg and (esg.compliance_risks or "compliance" in (esg.esg_summary or "").lower())) else 0.4
    platform_indep = 0.45 if (semantic and semantic.product and (
        "api" in semantic.product.lower() or "platform" in semantic.product.lower())) else 0.55

    return DealResilienceProfile(
        moat=d("moat_defensibility"),
        capital_buffer=d("capital_efficiency"),
        capital_efficiency=d("capital_efficiency"),
        founder_adaptability=d("founder_capability"),
        market_demand=d("market_attractiveness"),
        platform_independence=platform_indep,
        regulatory_readiness=reg_ready,
        sales_motion_strength=d("traction_evidence"),
    )


def _detect_contradictions(evaluation, semantic, simulation) -> list[str]:
    out: list[str] = []
    if evaluation.recommendation == "Yes" and simulation and simulation.resilience_score < 45.0:
        out.append("Recommendation is 'Yes' but simulated resilience is fragile (<45).")
    if semantic and not semantic.traction and evaluation.recommendation in {"Yes", "Maybe"}:
        out.append("Positive recommendation without quantified traction evidence.")
    return out


def run_advanced_pipeline(
    session: Session,
    *,
    company: Company,
    evaluation_run: EvaluationRun,
    evaluation,
    chunks,
    market,
    financial,
    founder,
    correlation_id: str | None = None,
) -> dict:
    cfg = _sim_config()
    context = {
        "deal_id": company.deal_id,
        "domain": company.domain,
        "is_portfolio_company": (company.status == "portfolio"),
    }

    # --- Analytic agents ---
    semantic = SemanticMatrixDeconstructor().run(chunks, context=context)
    macro = MacroTrendWhisperer().run(chunks, context=context)
    esg = ESGGovernanceAgent().run(chunks, context=context)
    portfolio = PortfolioMonitoringAgent().run(chunks, context=context)

    _persist_finding(session, evaluation_run.id, "semantic_matrix_deconstructor", semantic)
    _persist_finding(session, evaluation_run.id, "macro_trend_whisperer", macro)
    _persist_finding(session, evaluation_run.id, "esg_governance_agent", esg)
    _persist_finding(session, evaluation_run.id, "portfolio_monitoring_agent", portfolio)

    # --- Synthetic cohort + stochastic shock simulation ---
    profile = _resilience_profile(semantic, esg, financial)
    cohort_assumptions = {
        "category": (semantic.market if semantic else None) or company.domain or "unspecified",
        "geography": context.get("domain") or "global",
        "funding_stage": "seed",
        "tech_stack": (semantic.product if semantic else None) or "generic",
        "gtm_model": (semantic.customer if semantic else None) or "unspecified",
        "moat_strength": profile.moat,
        "capital_strength": profile.capital_buffer,
    }
    cohort_data = SyntheticCohortInjector().generate(
        assumptions=cohort_assumptions, seed=cfg["seed"], size=cfg["cohort_size"],
    )
    cohort = SyntheticCohort(
        company_id=company.id, evaluation_run_id=evaluation_run.id,
        seed=cfg["seed"], cohort_size=cohort_data["size"], category=cohort_data["category"],
        assumptions_json=json.dumps(cohort_data["assumptions"]),
        members_json=json.dumps(cohort_data["members"]),
    )
    session.add(cohort)
    session.flush()

    sim = StochasticShockSimulator().run(
        profile=profile, seed=cfg["seed"], iterations=cfg["iterations"],
        horizon_years=cfg["horizon_years"], cohort=cohort_data,
    )
    sim_run = SimulationRun(
        company_id=company.id, evaluation_run_id=evaluation_run.id, cohort_id=cohort.id,
        seed=sim.seed, engine_version=sim.engine_version, iterations=sim.iterations,
        horizon_years=sim.horizon_years, resilience_score=sim.resilience_score,
        failure_probability=sim.failure_probability, moat_sensitivity=sim.moat_sensitivity,
        founder_adaptability_index=sim.founder_adaptability_index,
        capital_efficiency_under_stress=sim.capital_efficiency_under_stress,
        assumptions_json=json.dumps(sim.assumptions, default=str),
        metrics_json=sim.model_dump_json(),
        risk_narrative=sim.risk_narrative,
    )
    session.add(sim_run)
    session.flush()
    for o in sim.scenario_outcomes:
        session.add(SimulationScenario(
            simulation_run_id=sim_run.id, scenario_key=o.scenario_key,
            scenario_label=o.scenario_label, survival_rate=o.survival_rate, severity=o.severity,
            parameters_json=json.dumps(o.parameters, default=str),
            outcome_json=json.dumps({"mean_shock": o.mean_shock}),
        ))

    # --- Risk flags ---
    flags: list[dict] = []
    for r in (esg.red_flags or []):
        flags.append({"source_agent": "esg_governance_agent", "category": "governance",
                      "severity": "high", "description": r})
    for r in (portfolio.red_flags or []):
        flags.append({"source_agent": "portfolio_monitoring_agent", "category": "portfolio",
                      "severity": "medium", "description": r})
    for risk in (semantic.risks or []):
        flags.append({"source_agent": "semantic_matrix_deconstructor", "category": "thesis",
                      "severity": "low", "description": risk})
    for o in sim.scenario_outcomes:
        if o.survival_rate < 0.35:
            flags.append({"source_agent": "stochastic_shock_simulator", "category": "resilience",
                          "severity": o.severity, "verified": True,
                          "description": f"Low simulated survival in '{o.scenario_label}' "
                                         f"({o.survival_rate*100:.0f}%).",
                          "evidence_ref": f"simulation:{sim_run.id}:{o.scenario_key}"})
    review_service.persist_risk_flags(
        session, company_id=company.id, evaluation_run_id=evaluation_run.id, flags=flags,
    )

    # --- Human-Synapse Interceptor ---
    missing = list(dict.fromkeys(
        list(evaluation.missing_information) + list(semantic.missing_data) + list(esg.missing_data)
    ))
    red_for_escalation = (esg.red_flags or []) + (portfolio.red_flags or [])
    contradictions = _detect_contradictions(evaluation, semantic, sim)
    decision = HumanSynapseInterceptor().evaluate(
        confidence_score=evaluation.confidence_score,
        missing_information=missing,
        red_flags=red_for_escalation,
        recommendation=evaluation.recommendation,
        resilience_score=sim.resilience_score,
        failure_probability=sim.failure_probability,
        governance_risk_score=esg.governance_risk_score,
        contradictions=contradictions,
        needs_review_upstream=(evaluation_run.status == WorkflowState.NEEDS_REVIEW.value),
    )
    review_ids = review_service.create_review_items_from_decision(
        session, company_id=company.id, evaluation_run_id=evaluation_run.id,
        decision=decision, correlation_id=correlation_id,
    )
    if decision.escalate:
        evaluation_run.status = WorkflowState.NEEDS_REVIEW.value
        company.status = WorkflowState.NEEDS_REVIEW.value
        alert = slack_service.send_alert(
            f"{company.name}: {len(review_ids)} review item(s) — {decision.highest_severity} severity.",
            severity=decision.highest_severity or "medium",
            context={"company_id": company.id, "evaluation_id": evaluation_run.id,
                     "reasons": [r.reason_code for r in decision.reasons]},
        )
        crm_event_service.record_event(
            session, channel="slack", event_type="human_synapse_alert",
            company_id=company.id, evaluation_run_id=evaluation_run.id,
            payload={"delivered": alert.get("delivered"), "reasons": [r.reason_code for r in decision.reasons]},
            correlation_id=correlation_id,
        )

    # --- IC memo ---
    sim_summary = ICMemoSimulationSummary(
        engine_version=sim.engine_version, seed=sim.seed, iterations=sim.iterations,
        resilience_score=sim.resilience_score, failure_probability=sim.failure_probability,
        moat_sensitivity=sim.moat_sensitivity, founder_adaptability_index=sim.founder_adaptability_index,
        capital_efficiency_under_stress=sim.capital_efficiency_under_stress,
        weakest_scenarios=sim.weakest_scenarios, risk_narrative=sim.risk_narrative,
    )
    sub_scores = dict(semantic.thesis_dimensions)
    sub_scores["governance_risk_score"] = round(esg.governance_risk_score, 2)
    sub_scores["resilience_score"] = sim.resilience_score
    memo = ICMemoAgent().build(
        company_id=company.id, company_name=company.name, evaluation_id=evaluation_run.id,
        semantic=semantic, market_summary=evaluation.market_moat_summary,
        financial_summary=evaluation.financial_metrics_summary,
        founder_summary=evaluation.founder_market_fit_summary,
        macro=macro, esg=esg, scoring_version=SCORING_VERSION,
        final_score=evaluation.deterministic_score, recommendation=evaluation.recommendation,
        recommendation_reason=evaluation.recommendation_reason,
        confidence_score=evaluation.confidence_score, sub_scores=sub_scores,
        key_risks=list(dict.fromkeys((semantic.risks or []) + red_for_escalation)),
        review_flags=[r.reason_code for r in decision.reasons], simulation_summary=sim_summary,
    )
    _persist_finding(session, evaluation_run.id, "ic_memo_agent", memo)

    review_service._audit(
        session, "advanced_pipeline_completed", entity_type="evaluation_run",
        entity_id=evaluation_run.id, correlation_id=correlation_id,
        payload={"resilience_score": sim.resilience_score, "escalated": decision.escalate,
                 "review_items": len(review_ids), "risk_flags": len(flags)},
    )

    return {
        "simulation_run_id": sim_run.id,
        "cohort_id": cohort.id,
        "resilience_score": sim.resilience_score,
        "escalated": decision.escalate,
        "review_item_ids": review_ids,
        "risk_flag_count": len(flags),
        "ic_memo": True,
    }
