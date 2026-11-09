"""FastAPI application — v1 endpoints + health/readiness."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from auth_middleware import require_api_key
from db import get_db_session, init_db
from env_config import get_database_url, integration_status, load_env
from redis_client import redis_status
from evaluation_schema import CompanyEvaluationSchema
from job_queue import enqueue_job, get_job
from evaluation_orchestrator import run_evaluation_for_document
from ingestion_service import ingest_document
from models import (
    AuditLog,
    Company,
    EvaluationRun,
    InvestorOverride,
    ScoringResult,
    TrainingExample,
)
from structured_logging import configure_logging, log_event, set_correlation_id
from synthesis_service import synthesize_evaluation
from workflow_states import WorkflowState

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_env()
    configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
    init_db()
    storage = os.environ.get("STORAGE_DIR", "./storage")
    os.makedirs(storage, exist_ok=True)
    logger.info("Deal Screener API started - docs at /docs")
    yield


app = FastAPI(
    title="Deal Screener API",
    version="1.0.0",
    lifespan=lifespan,
    swagger_ui_parameters={"persistAuthorization": True},
)

_PROTECTED_PREFIXES = (
    "/api/v1/upload",
    "/api/v1/status/",
    "/api/v1/evaluation/",
    "/api/v1/review/",
    "/api/v1/ops/",
    "/api/v1/jobs/",
    "/api/v1/deals/",
    "/api/v1/review-queue",
)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["X-API-Key"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "Set API_KEY in .env (leave empty for local development)",
    }
    for path, methods in schema.get("paths", {}).items():
        if any(path.startswith(p) for p in _PROTECTED_PREFIXES):
            for op in methods.values():
                if isinstance(op, dict):
                    op["security"] = [{"X-API-Key": []}]
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi

_cors_origins = os.environ.get("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request/response schemas ---


class UploadResponse(BaseModel):
    correlation_id: str
    company_id: str
    document_id: str
    status: str
    duplicate_detected: bool
    message: str
    job_id: str | None = None
    evaluation_id: str | None = None


class JobStatusResponse(BaseModel):
    job_id: str
    job_type: str
    status: str
    message: str | None = None
    correlation_id: str | None = None


class StatusResponse(BaseModel):
    company_id: str
    status: str
    latest_evaluation_id: str | None = None
    updated_at: datetime | None = None


class OverrideRequest(BaseModel):
    company_id: str
    choice: str = Field(pattern="^(Yes|No|Maybe)$")
    override_reason: str | None = None
    notes: str | None = None
    reviewer_name: str | None = None
    timestamp: datetime | None = None
    evaluation_id: str | None = None


class OverrideResponse(BaseModel):
    override_id: str
    company_id: str
    choice: str
    message: str


class ErrorResponse(BaseModel):
    error_code: str
    message: str
    correlation_id: str | None = None
    details: dict[str, Any] | None = None


# --- Exception handler ---


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    from structured_logging import correlation_id_var

    body = ErrorResponse(
        error_code=f"HTTP_{exc.status_code}",
        message=str(exc.detail),
        correlation_id=correlation_id_var.get(),
    )
    return JSONResponse(status_code=exc.status_code, content=body.model_dump())


@app.exception_handler(Exception)
async def generic_exception_handler(request, exc: Exception):
    from structured_logging import correlation_id_var

    logger.exception("API error")
    body = ErrorResponse(
        error_code="INTERNAL_ERROR",
        message=str(exc),
        correlation_id=correlation_id_var.get(),
    )
    return JSONResponse(status_code=500, content=body.model_dump())


# --- Endpoints ---


@app.get("/")
def root():
    """Landing page — browser GET / otherwise returns 404."""
    return {
        "service": "Deal Screener API",
        "version": "1.0.0",
        "status": "running",
        "message": "Use /docs for interactive API explorer.",
        "links": {
            "docs": "/docs",
            "redoc": "/redoc",
            "openapi": "/openapi.json",
            "health": "/health",
            "readiness": "/readiness",
            "upload": "POST /api/v1/upload",
            "deal": "GET /api/v1/deals/{deal_id}",
            "ic_memo": "GET /api/v1/deals/{deal_id}/ic-memo",
            "jobs": "GET /api/v1/jobs/{job_id}",
            "review_queue": "/api/v1/review/queue",
        },
        "worker": "Run python worker.py (or docker compose service 'worker')",
        "architecture": "Parallel agents -> deterministic scoring -> human override -> retrain loop",
    }


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/readiness")
def readiness(db: Session = Depends(get_db_session)):
    checks: dict[str, str] = {"database": "ok", "redis": "skipped"}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "postgresql" if get_database_url().startswith("postgresql") else "sqlite"
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database not ready: {exc}")

    redis = redis_status()
    if redis["configured"]:
        if redis["connected"]:
            depth = redis["queue_depth"]
            checks["redis"] = f"ok (depth={depth})"
        else:
            checks["redis"] = f"error: {redis['detail']}"
            from redis_client import redis_required

            if redis_required():
                raise HTTPException(status_code=503, detail=f"Redis not ready: {redis['detail']}")
    if os.environ.get("RUN_LIVE_LLM", "false").lower() in {"1", "true", "yes"}:
        checks["ollama_primary"] = _ollama_check(
            os.environ.get("OLLAMA_PRIMARY_BASE_URL") or os.environ.get("OLLAMA_BASE_URL")
        )
        checks["ollama_fallback"] = _ollama_check(os.environ.get("OLLAMA_FALLBACK_BASE_URL"))
    return {"status": "ready", **checks}


def _ollama_check(base_url: str | None) -> str:
    if not base_url:
        return "not_configured"
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/api/tags", timeout=3) as resp:
            return "ok" if resp.status == 200 else f"http_{resp.status}"
    except Exception as exc:
        return f"error: {exc}"


@app.get("/api/v1/config/status")
def config_status():
    """Non-secret integration status for ops."""
    return integration_status()


@app.get("/api/v1/ops/summary", dependencies=[Depends(require_api_key)])
def ops_summary(db: Session = Depends(get_db_session)):
    """Operational summary for monitoring dashboards."""
    from models import JobRun
    from redis_client import worker_heartbeat_status

    redis = redis_status()
    pending = db.query(JobRun).filter_by(status="pending").count()
    processing = db.query(JobRun).filter_by(status="processing").count()
    recent = (
        db.query(JobRun)
        .order_by(JobRun.created_at.desc())
        .limit(5)
        .all()
    )
    return {
        **integration_status(),
        "pending_jobs": pending,
        "processing_jobs": processing,
        "redis_queue_depth": redis.get("queue_depth"),
        "worker_heartbeat": worker_heartbeat_status(),
        "recent_jobs": [
            {
                "id": j.id,
                "type": j.job_type,
                "status": j.status,
                "message": (j.message or "")[:120],
                "created_at": j.created_at.isoformat() if j.created_at else None,
            }
            for j in recent
        ],
    }


def _async_jobs_enabled() -> bool:
    return os.environ.get("ASYNC_JOBS", "false").lower() in {"1", "true", "yes"}


@app.post("/api/v1/upload", response_model=UploadResponse, dependencies=[Depends(require_api_key)])
async def upload_pitch(
    file: UploadFile = File(...),
    company_name: str = Form(...),
    deal_id: str | None = Form(None),
    domain: str | None = Form(None),
    async_processing: bool = Query(False, description="Queue evaluation to background worker"),
    db: Session = Depends(get_db_session),
):
    correlation_id = str(uuid.uuid4())
    set_correlation_id(correlation_id)
    log_event(logger, "upload_received", filename=file.filename, company=company_name)

    try:
        data = await file.read()
        log_event(logger, "checksum_computed", size=len(data))
        storage_dir = os.environ.get("STORAGE_DIR", "./storage")
        result = ingest_document(
            db,
            file_bytes=data,
            filename=file.filename or "upload.bin",
            company_name=company_name,
            deal_id=deal_id,
            domain=domain,
            correlation_id=correlation_id,
            storage_dir=storage_dir,
        )
        if result.duplicate_detected:
            log_event(logger, "duplicate_detected", document_id=result.document_id)
            return UploadResponse(
                correlation_id=result.correlation_id,
                company_id=result.company_id,
                document_id=result.document_id,
                status=result.status,
                duplicate_detected=True,
                message=result.message,
            )

        log_event(logger, "parser_selected", document_id=result.document_id)

        use_async = async_processing or _async_jobs_enabled()
        if use_async:
            job_id = enqueue_job(
                db,
                "evaluate_document",
                {
                    "company_id": result.company_id,
                    "document_id": result.document_id,
                },
                correlation_id=correlation_id,
            )
            company = db.get(Company, result.company_id)
            if company:
                company.status = WorkflowState.PENDING.value
            return UploadResponse(
                correlation_id=result.correlation_id,
                company_id=result.company_id,
                document_id=result.document_id,
                status=WorkflowState.PENDING.value,
                duplicate_detected=False,
                message="Document ingested; evaluation queued for background worker.",
                job_id=job_id,
            )

        evaluation_id, eval_status = run_evaluation_for_document(
            db,
            company_id=result.company_id,
            document_id=result.document_id,
            correlation_id=correlation_id,
        )
        company = db.get(Company, result.company_id)
        if company:
            company.status = eval_status

        return UploadResponse(
            correlation_id=result.correlation_id,
            company_id=result.company_id,
            document_id=result.document_id,
            status=eval_status,
            duplicate_detected=result.duplicate_detected,
            evaluation_id=evaluation_id,
            message=f"Document ingested; evaluation {evaluation_id} status={eval_status}.",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/v1/status/{company_id}", response_model=StatusResponse, dependencies=[Depends(require_api_key)])
def get_status(company_id: str, db: Session = Depends(get_db_session)):
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    latest = (
        db.query(EvaluationRun)
        .filter_by(company_id=company_id)
        .order_by(EvaluationRun.created_at.desc())
        .first()
    )
    return StatusResponse(
        company_id=company.id,
        status=company.status,
        latest_evaluation_id=latest.id if latest else None,
        updated_at=company.updated_at,
    )


@app.get("/api/v1/evaluation/{evaluation_id}", response_model=CompanyEvaluationSchema, dependencies=[Depends(require_api_key)])
def get_evaluation(evaluation_id: str, db: Session = Depends(get_db_session)):
    scoring = db.query(ScoringResult).filter_by(evaluation_run_id=evaluation_id).first()
    if scoring and scoring.payload_json:
        return CompanyEvaluationSchema.model_validate_json(scoring.payload_json)

    run = db.get(EvaluationRun, evaluation_id)
    if not run:
        raise HTTPException(status_code=404, detail="Evaluation not found")

    company = db.get(Company, run.company_id)
    docs = company.documents if company else []
    evaluation = synthesize_evaluation(
        company_id=run.company_id,
        company_name=company.name if company else "Unknown",
        document_ids=[d.id for d in docs],
        evaluation_id=evaluation_id,
        market=None,
        financial=None,
        founder=None,
        needs_review=run.status == WorkflowState.NEEDS_REVIEW.value,
    )
    return evaluation


@app.post("/api/v1/review/override", response_model=OverrideResponse, dependencies=[Depends(require_api_key)])
def submit_override(body: OverrideRequest, db: Session = Depends(get_db_session)):
    correlation_id = str(uuid.uuid4())
    set_correlation_id(correlation_id)

    company = db.get(Company, body.company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    override = InvestorOverride(
        company_id=body.company_id,
        evaluation_run_id=body.evaluation_id,
        choice=body.choice,
        override_reason=body.override_reason,
        notes=body.notes,
        reviewer_name=body.reviewer_name,
        created_at=body.timestamp or datetime.now(timezone.utc),
    )
    db.add(override)
    db.flush()

    db.add(
        AuditLog(
            event_type="override_submitted",
            entity_type="investor_override",
            entity_id=override.id,
            correlation_id=correlation_id,
            actor=body.reviewer_name,
            payload_json=json.dumps(body.model_dump(), default=str),
        )
    )
    db.add(
        TrainingExample(
            company_id=body.company_id,
            evaluation_run_id=body.evaluation_id,
            source="human_override",
            label=body.choice,
            features_json=json.dumps(
                {"override_id": override.id, "override_reason": body.override_reason}
            ),
            reviewed_at=datetime.now(timezone.utc),
        )
    )
    db.flush()
    log_event(logger, "override_submitted", company_id=body.company_id, choice=body.choice)
    log_event(logger, "training_example_created", company_id=body.company_id, label=body.choice)

    return OverrideResponse(
        override_id=override.id,
        company_id=body.company_id,
        choice=body.choice,
        message="Override recorded; AI recommendation unchanged.",
    )


@app.get("/api/v1/review/queue", dependencies=[Depends(require_api_key)])
def review_queue(db: Session = Depends(get_db_session)):
    items = []
    runs = (
        db.query(EvaluationRun)
        .filter(EvaluationRun.status.in_([WorkflowState.NEEDS_REVIEW.value, WorkflowState.FAILED.value]))
        .order_by(EvaluationRun.updated_at.desc())
        .limit(100)
        .all()
    )
    for run in runs:
        scoring = db.query(ScoringResult).filter_by(evaluation_run_id=run.id).first()
        company = db.get(Company, run.company_id)
        items.append(
            {
                "company_id": run.company_id,
                "company_name": company.name if company else None,
                "evaluation_id": run.id,
                "status": run.status,
                "confidence_score": scoring.evidence_quality_score if scoring else None,
                "missing_critical_information": (
                    json.loads(scoring.payload_json).get("missing_information")
                    if scoring and scoring.payload_json
                    else None
                ),
                "recommendation": scoring.recommendation if scoring else None,
            }
        )
    return {"items": items, "count": len(items)}


@app.get("/api/v1/jobs/{job_id}", response_model=JobStatusResponse, dependencies=[Depends(require_api_key)])
def get_job_status(job_id: str, db: Session = Depends(get_db_session)):
    job = get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    meta = json.loads(job.metadata_json or "{}")
    return JobStatusResponse(
        job_id=job.id,
        job_type=job.job_type,
        status=job.status,
        message=job.message,
        correlation_id=meta.get("correlation_id"),
    )


# ---------------------------------------------------------------------------
# Multi-agent / simulation / governance endpoints.
# Deal == Company. ``deal_id`` accepts either the Company UUID or the external
# (upstream CRM) deal_id. All routes are API-key protected; no raw confidential files exposed.
# ---------------------------------------------------------------------------


def _resolve_company(db: Session, deal_id: str):
    company = db.get(Company, deal_id)
    if company is None:
        company = db.query(Company).filter_by(deal_id=deal_id).order_by(Company.created_at.desc()).first()
    return company


def _latest_evaluation(db: Session, company_id: str):
    return (
        db.query(EvaluationRun)
        .filter_by(company_id=company_id)
        .order_by(EvaluationRun.created_at.desc())
        .first()
    )


@app.get("/api/v1/deals/{deal_id}", dependencies=[Depends(require_api_key)])
def get_deal(deal_id: str, db: Session = Depends(get_db_session)):
    from models import RiskFlag, ReviewItem, SimulationRun

    company = _resolve_company(db, deal_id)
    if not company:
        raise HTTPException(status_code=404, detail="Deal not found")
    run = _latest_evaluation(db, company.id)
    scoring = (
        db.query(ScoringResult).filter_by(evaluation_run_id=run.id).first() if run else None
    )
    sim = (
        db.query(SimulationRun).filter_by(evaluation_run_id=run.id)
        .order_by(SimulationRun.created_at.desc()).first()
        if run else None
    )
    risk_flags = (
        db.query(RiskFlag).filter_by(company_id=company.id).order_by(RiskFlag.created_at.desc()).limit(50).all()
    )
    open_reviews = (
        db.query(ReviewItem).filter_by(company_id=company.id, status="open").count()
    )
    return {
        "deal_id": company.id,
        "external_deal_id": company.deal_id,
        "name": company.name,
        "domain": company.domain,
        "status": company.status,
        "latest_evaluation_id": run.id if run else None,
        "recommendation": scoring.recommendation if scoring else None,
        "final_score": scoring.final_score if scoring else None,
        "decision_authority": "Investment Committee (human)",
        "simulation": (
            {
                "simulation_run_id": sim.id,
                "resilience_score": sim.resilience_score,
                "failure_probability": sim.failure_probability,
                "engine_version": sim.engine_version,
                "seed": sim.seed,
            }
            if sim else None
        ),
        "open_review_items": open_reviews,
        "risk_flags": [
            {"id": rf.id, "source_agent": rf.source_agent, "category": rf.category,
             "severity": rf.severity, "description": rf.description, "verified": rf.verified}
            for rf in risk_flags
        ],
    }


@app.get("/api/v1/deals/{deal_id}/ic-memo", dependencies=[Depends(require_api_key)])
def get_ic_memo(deal_id: str, db: Session = Depends(get_db_session)):
    from models import AgentFinding

    company = _resolve_company(db, deal_id)
    if not company:
        raise HTTPException(status_code=404, detail="Deal not found")
    run = _latest_evaluation(db, company.id)
    if not run:
        raise HTTPException(status_code=404, detail="No evaluation for deal")
    memo = (
        db.query(AgentFinding)
        .filter_by(evaluation_run_id=run.id, agent_name="ic_memo_agent")
        .order_by(AgentFinding.created_at.desc())
        .first()
    )
    if not memo:
        raise HTTPException(status_code=404, detail="IC memo not generated (advanced pipeline disabled or no chunks)")
    return json.loads(memo.payload_json)


@app.get("/api/v1/deals/{deal_id}/simulation-runs", dependencies=[Depends(require_api_key)])
def get_simulation_runs(deal_id: str, db: Session = Depends(get_db_session)):
    from models import SimulationRun, SimulationScenario

    company = _resolve_company(db, deal_id)
    if not company:
        raise HTTPException(status_code=404, detail="Deal not found")
    runs = (
        db.query(SimulationRun).filter_by(company_id=company.id)
        .order_by(SimulationRun.created_at.desc()).limit(20).all()
    )
    out = []
    for r in runs:
        scenarios = db.query(SimulationScenario).filter_by(simulation_run_id=r.id).all()
        out.append({
            "simulation_run_id": r.id,
            "evaluation_id": r.evaluation_run_id,
            "seed": r.seed,
            "engine_version": r.engine_version,
            "iterations": r.iterations,
            "horizon_years": r.horizon_years,
            "resilience_score": r.resilience_score,
            "failure_probability": r.failure_probability,
            "moat_sensitivity": r.moat_sensitivity,
            "founder_adaptability_index": r.founder_adaptability_index,
            "capital_efficiency_under_stress": r.capital_efficiency_under_stress,
            "risk_narrative": r.risk_narrative,
            "survival_matrix": {s.scenario_key: s.survival_rate for s in scenarios},
        })
    return {"deal_id": company.id, "count": len(out), "simulation_runs": out}


@app.get("/api/v1/review-queue", dependencies=[Depends(require_api_key)])
def governance_review_queue(status: str = Query("open"), db: Session = Depends(get_db_session)):
    """Human-Synapse review queue (ReviewItems from the interceptor)."""
    import review_service

    items = review_service.list_review_queue(db, status=status)
    return {"items": items, "count": len(items)}


class ReviewOverrideRequest(BaseModel):
    choice: str | None = Field(default=None, pattern="^(Yes|No|Maybe)$")
    reviewer_name: str | None = None
    notes: str | None = None


@app.post("/api/v1/review-queue/{item_id}/override", dependencies=[Depends(require_api_key)])
def override_review_item(item_id: str, body: ReviewOverrideRequest, db: Session = Depends(get_db_session)):
    """Partner resolves/overrides a review item. AI score is never mutated."""
    import review_service

    correlation_id = str(uuid.uuid4())
    set_correlation_id(correlation_id)
    try:
        result = review_service.resolve_review_item(
            db, review_item_id=item_id, choice=body.choice,
            reviewer_name=body.reviewer_name, notes=body.notes, correlation_id=correlation_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    log_event(logger, "review_item_overridden", review_item_id=item_id, choice=body.choice)
    return {**result, "correlation_id": correlation_id,
            "message": "Review resolved; AI recommendation unchanged."}


@app.post("/api/v1/jobs/{job_id}/run", dependencies=[Depends(require_api_key)])
def run_job_now(job_id: str, db: Session = Depends(get_db_session)):
    """Synchronously execute a pending/failed job (manual trigger / re-run)."""
    from datetime import datetime, timezone

    from job_queue import get_job
    from worker import process_job

    job = get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == "processing":
        raise HTTPException(status_code=409, detail="Job already processing")
    job.status = "processing"
    job.started_at = datetime.now(timezone.utc)
    db.flush()
    process_job(db, job)
    db.refresh(job)
    return {"job_id": job.id, "job_type": job.job_type, "status": job.status,
            "message": job.message}
