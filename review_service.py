"""Human governance services: ReviewItems, RiskFlags, override resolution.

The AI never decides. The Human-Synapse Interceptor flags; this module persists
those flags as ReviewItems + RiskFlags + AuditLog entries, exposes the review
queue, and records partner overrides (which are always captured and audited).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from agent_schemas_advanced import InterceptorDecision
from models import AuditLog, Company, InvestorOverride, ReviewItem, RiskFlag


def _audit(session: Session, event_type: str, *, entity_type: str, entity_id: str,
           correlation_id: str | None = None, actor: str | None = None, payload: dict | None = None) -> None:
    session.add(AuditLog(
        event_type=event_type, entity_type=entity_type, entity_id=entity_id,
        correlation_id=correlation_id, actor=actor,
        payload_json=json.dumps(payload, default=str) if payload is not None else None,
    ))


def persist_risk_flags(
    session: Session, *, company_id: str, evaluation_run_id: str | None, flags: list[dict],
) -> list[str]:
    ids: list[str] = []
    for f in flags:
        rf = RiskFlag(
            company_id=company_id,
            evaluation_run_id=evaluation_run_id,
            source_agent=f.get("source_agent", "unknown"),
            category=f.get("category", "general"),
            severity=f.get("severity", "medium"),
            description=f["description"],
            evidence_ref=f.get("evidence_ref"),
            verified=bool(f.get("verified", False)),
        )
        session.add(rf)
        session.flush()
        ids.append(rf.id)
    return ids


def create_review_items_from_decision(
    session: Session, *, company_id: str, evaluation_run_id: str | None,
    decision: InterceptorDecision, correlation_id: str | None = None,
) -> list[str]:
    """One ReviewItem per escalation reason. Returns created item ids."""
    if not decision.escalate:
        return []
    ids: list[str] = []
    for reason in decision.reasons:
        item = ReviewItem(
            company_id=company_id,
            evaluation_run_id=evaluation_run_id,
            reason_code=reason.reason_code,
            severity=reason.severity,
            summary=reason.summary,
            details_json=json.dumps(reason.details, default=str),
            status="open",
            correlation_id=correlation_id,
        )
        session.add(item)
        session.flush()
        ids.append(item.id)
        _audit(session, "review_item_created", entity_type="review_item", entity_id=item.id,
               correlation_id=correlation_id,
               payload={"reason_code": reason.reason_code, "severity": reason.severity})
    return ids


def list_review_queue(session: Session, *, status: str = "open", limit: int = 200) -> list[dict]:
    q = session.query(ReviewItem)
    if status:
        q = q.filter(ReviewItem.status == status)
    rows = q.order_by(ReviewItem.created_at.desc()).limit(limit).all()
    out = []
    for r in rows:
        company = session.get(Company, r.company_id)
        out.append({
            "review_item_id": r.id,
            "company_id": r.company_id,
            "company_name": company.name if company else None,
            "evaluation_id": r.evaluation_run_id,
            "reason_code": r.reason_code,
            "severity": r.severity,
            "summary": r.summary,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return out


def resolve_review_item(
    session: Session, *, review_item_id: str, choice: str | None,
    reviewer_name: str | None, notes: str | None, correlation_id: str | None = None,
) -> dict:
    """Partner resolves a review item. If a Yes/No/Maybe choice is given, an
    InvestorOverride is recorded. AI score/recommendation are never mutated."""
    item = session.get(ReviewItem, review_item_id)
    if item is None:
        raise ValueError(f"ReviewItem {review_item_id} not found")

    item.status = "resolved"
    item.resolved_at = datetime.now(timezone.utc)
    item.resolution_choice = choice
    item.resolution_notes = notes
    item.assigned_to = reviewer_name

    override_id = None
    if choice in {"Yes", "No", "Maybe"}:
        override = InvestorOverride(
            company_id=item.company_id,
            evaluation_run_id=item.evaluation_run_id,
            choice=choice,
            override_reason=f"Resolved review item {item.reason_code}",
            notes=notes,
            reviewer_name=reviewer_name,
        )
        session.add(override)
        session.flush()
        override_id = override.id

    _audit(session, "review_item_resolved", entity_type="review_item", entity_id=item.id,
           correlation_id=correlation_id, actor=reviewer_name,
           payload={"choice": choice, "override_id": override_id, "notes": notes})

    return {"review_item_id": item.id, "status": item.status,
            "choice": choice, "override_id": override_id}
