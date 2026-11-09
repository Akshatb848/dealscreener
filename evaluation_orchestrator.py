"""Evaluation orchestration: parallel agents → synthesis → deterministic scoring."""

from __future__ import annotations

import json
import logging
import os
import uuid

from sqlalchemy.orm import Session

from models import AuditLog, Company, Document, DocumentChunk, EvaluationRun
from orchestrator_service import inference_route_config, run_agents_parallel
from parser_layer import ParsedChunk
from structured_logging import log_event, set_correlation_id
from synthesis_service import persist_evaluation, synthesize_evaluation
from workflow_states import WorkflowState

logger = logging.getLogger(__name__)


def _chunks_from_db(session: Session, document_id: str) -> list[ParsedChunk]:
    rows = session.query(DocumentChunk).filter_by(document_id=document_id).order_by(DocumentChunk.page_index).all()
    doc = session.get(Document, document_id)
    source = doc.filename if doc else "unknown"
    return [
        ParsedChunk(
            source_file=source,
            page_index=r.page_index,
            raw_text=r.raw_text,
            extraction_method=r.extraction_method,
            confidence_score=r.confidence_score,
        )
        for r in rows
    ]


def run_evaluation_for_document(
    session: Session,
    *,
    company_id: str,
    document_id: str,
    correlation_id: str | None = None,
    evaluation_id: str | None = None,
    use_live_llm: bool | None = None,
) -> tuple[str, str]:
    correlation_id = correlation_id or str(uuid.uuid4())
    set_correlation_id(correlation_id)

    company = session.get(Company, company_id)
    if not company:
        raise ValueError(f"Company {company_id} not found")

    if use_live_llm is None:
        use_live_llm = os.environ.get("RUN_LIVE_LLM", "false").lower() in {"1", "true", "yes"}

    run = session.get(EvaluationRun, evaluation_id) if evaluation_id else None
    if run is None:
        run = EvaluationRun(
            id=str(uuid.uuid4()),
            company_id=company_id,
            status=WorkflowState.PROCESSING.value,
            correlation_id=correlation_id,
        )
        session.add(run)
        session.flush()

    company.status = WorkflowState.PROCESSING.value
    log_event(logger, "evaluation_started", company_id=company_id, evaluation_id=run.id)

    chunks = _chunks_from_db(session, document_id)
    if not chunks:
        run.status = WorkflowState.FAILED.value
        run.error_message = "No parsed chunks available for evaluation"
        company.status = WorkflowState.FAILED.value
        log_event(logger, "evaluation_completed", status="failed", reason="no_chunks")
        return run.id, run.status

    market, financial, founder, needs_review = run_agents_parallel(chunks, use_live_llm=use_live_llm)
    route = inference_route_config()
    model_versions = {
        "primary_model": route["primary_model"],
        "fallback_model": route["fallback_model"],
    }

    evaluation = synthesize_evaluation(
        company_id=company_id,
        company_name=company.name,
        document_ids=[document_id],
        evaluation_id=run.id,
        market=market,
        financial=financial,
        founder=founder,
        needs_review=needs_review,
        model_versions_used=model_versions,
    )
    persist_evaluation(session, evaluation, market=market, financial=financial, founder=founder)

    # Advanced multi-agent + simulation layer (additive, fail-safe). A failure
    # here must never compromise the deterministic core result above.
    from advanced_pipeline import advanced_enabled, run_advanced_pipeline

    if advanced_enabled():
        try:
            summary = run_advanced_pipeline(
                session,
                company=company,
                evaluation_run=run,
                evaluation=evaluation,
                chunks=chunks,
                market=market,
                financial=financial,
                founder=founder,
                correlation_id=correlation_id,
            )
            log_event(
                logger,
                "advanced_pipeline_completed",
                evaluation_id=run.id,
                resilience_score=summary.get("resilience_score"),
                escalated=summary.get("escalated"),
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Advanced pipeline failed (core result preserved): %s", exc)
            session.add(
                AuditLog(
                    event_type="advanced_pipeline_failed",
                    entity_type="evaluation_run",
                    entity_id=run.id,
                    correlation_id=correlation_id,
                    payload_json=json.dumps({"error": str(exc)}),
                )
            )

    company.status = run.status
    session.flush()
    log_event(
        logger,
        "scoring_completed",
        evaluation_id=run.id,
        final_score=evaluation.deterministic_score,
        recommendation=evaluation.recommendation,
    )
    session.add(
        AuditLog(
            event_type="evaluation_completed",
            entity_type="evaluation_run",
            entity_id=run.id,
            correlation_id=correlation_id,
            payload_json=json.dumps(
                {
                    "company_id": company_id,
                    "final_score": evaluation.deterministic_score,
                    "recommendation": evaluation.recommendation,
                    "status": run.status,
                }
            ),
        )
    )
    log_event(logger, "evaluation_completed", evaluation_id=run.id, recommendation=evaluation.recommendation)
    return run.id, run.status
