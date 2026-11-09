"""Agent 11 — Human-Synapse Interceptor.

Pauses / escalates the workflow when the AI should defer to human partners:
low confidence, missing evidence, agent contradictions, high-severity red flags,
material simulation failure sensitivity, or a mandatory partner checkpoint.

It is the governance gate: AI never makes the final call. The interceptor only
*flags*; ReviewItem + AuditLog persistence is handled by ``review_service``.
"""

from __future__ import annotations

import os

from agent_schemas_advanced import EscalationReason, InterceptorDecision

_SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}


def _conf_threshold() -> float:
    return float(os.environ.get("REVIEW_CONFIDENCE_THRESHOLD", "0.55"))


def _resilience_threshold() -> float:
    return float(os.environ.get("REVIEW_RESILIENCE_THRESHOLD", "45.0"))


class HumanSynapseInterceptor:
    name = "human_synapse_interceptor"

    def evaluate(
        self,
        *,
        confidence_score: float,
        missing_information: list[str],
        red_flags: list[str],
        recommendation: str,
        resilience_score: float | None = None,
        failure_probability: float | None = None,
        governance_risk_score: float | None = None,
        contradictions: list[str] | None = None,
        partner_checkpoint: bool = False,
        needs_review_upstream: bool = False,
    ) -> InterceptorDecision:
        reasons: list[EscalationReason] = []
        contradictions = contradictions or []

        if confidence_score < _conf_threshold():
            reasons.append(EscalationReason(
                reason_code="low_confidence", severity="high",
                summary=f"Confidence {confidence_score:.2f} below threshold {_conf_threshold():.2f}.",
                details={"confidence_score": confidence_score},
            ))

        if missing_information:
            sev = "high" if len(missing_information) >= 4 else "medium"
            reasons.append(EscalationReason(
                reason_code="missing_evidence", severity=sev,
                summary=f"{len(missing_information)} critical fields missing.",
                details={"missing": missing_information[:20]},
            ))

        if red_flags:
            reasons.append(EscalationReason(
                reason_code="red_flags", severity="high",
                summary=f"{len(red_flags)} red flag(s) detected.",
                details={"red_flags": red_flags[:20]},
            ))

        for c in contradictions:
            reasons.append(EscalationReason(
                reason_code="agent_contradiction", severity="medium",
                summary=c, details={},
            ))

        if resilience_score is not None and resilience_score < _resilience_threshold():
            reasons.append(EscalationReason(
                reason_code="low_resilience", severity="high",
                summary=f"Simulated resilience {resilience_score:.1f} below {_resilience_threshold():.1f}.",
                details={"resilience_score": resilience_score, "failure_probability": failure_probability},
            ))

        if governance_risk_score is not None and governance_risk_score >= 70.0:
            reasons.append(EscalationReason(
                reason_code="governance_risk", severity="high",
                summary=f"Governance risk score {governance_risk_score:.0f} is elevated.",
                details={"governance_risk_score": governance_risk_score},
            ))

        if recommendation == "Needs Review" or needs_review_upstream:
            reasons.append(EscalationReason(
                reason_code="scoring_needs_review", severity="medium",
                summary="Deterministic scoring routed this deal to human review.",
                details={"recommendation": recommendation},
            ))

        if partner_checkpoint:
            reasons.append(EscalationReason(
                reason_code="partner_checkpoint", severity="medium",
                summary="Mandatory partner checkpoint for this stage/decision.",
                details={},
            ))

        escalate = bool(reasons)
        highest = None
        if reasons:
            highest = max((r.severity for r in reasons), key=lambda s: _SEVERITY_RANK[s])

        return InterceptorDecision(
            escalate=escalate,
            highest_severity=highest,  # type: ignore[arg-type]
            reasons=reasons,
            confidence=round(confidence_score, 4),
        )
