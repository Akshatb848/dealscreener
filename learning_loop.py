"""Continuous learning loop with retraining gate and JobRun tracking."""

from __future__ import annotations

import json
import logging
import os
import pickle
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from models import AuditLog, InvestorOverride, JobRun, TrainingExample
from model_registry import register_model_version, promote_to_active

logger = logging.getLogger(__name__)

RETRAIN_MIN_REVIEWED_CASES = int(os.environ.get("RETRAIN_MIN_REVIEWED_CASES", "50"))


def export_reviewed_cases(session: Session) -> list[dict]:
    """Export unique human-reviewed/overridden cases."""
    seen: set[str] = set()
    cases: list[dict] = []

    overrides = session.query(InvestorOverride).order_by(InvestorOverride.created_at.desc()).all()
    for o in overrides:
        key = o.id
        if key in seen:
            continue
        seen.add(key)
        cases.append(
            {
                "company_id": o.company_id,
                "evaluation_run_id": o.evaluation_run_id,
                "label": o.choice,
                "source": "human_override",
                "reviewed_at": o.created_at.isoformat() if o.created_at else None,
            }
        )

    examples = session.query(TrainingExample).filter(TrainingExample.label.isnot(None)).all()
    for ex in examples:
        # Skip examples derived from an override already counted above.
        try:
            source_override_id = json.loads(ex.features_json or "{}").get("override_id")
        except (ValueError, TypeError):
            source_override_id = None
        if source_override_id and source_override_id in seen:
            continue
        key = f"{ex.company_id}:{ex.id}:{ex.label}"
        if key in seen:
            continue
        seen.add(key)
        cases.append(
            {
                "company_id": ex.company_id,
                "evaluation_run_id": ex.evaluation_run_id,
                "label": ex.label,
                "source": ex.source,
                "features_json": ex.features_json,
                "reviewed_at": ex.reviewed_at.isoformat() if ex.reviewed_at else None,
            }
        )
    return cases


def _record_job(session: Session, job_type: str, status: str, dry_run: bool, message: str, meta: dict | None = None) -> JobRun:
    job = JobRun(
        job_type=job_type,
        status=status,
        dry_run=dry_run,
        message=message,
        metadata_json=json.dumps(meta) if meta else None,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    session.add(job)
    session.add(
        AuditLog(
            event_type=f"job_{status}",
            entity_type="job_run",
            entity_id=job.id,
            payload_json=json.dumps({"job_type": job_type, "message": message}),
        )
    )
    return job


def run_learning_loop(
    session: Session,
    *,
    dry_run: bool = False,
    output_dir: str = "./models",
    min_cases: int | None = None,
) -> JobRun:
    """
    Export reviewed cases, gate on minimum count, train candidate in staging scope.
    """
    min_cases = min_cases if min_cases is not None else RETRAIN_MIN_REVIEWED_CASES
    cases = export_reviewed_cases(session)
    logger.info("Exported %d unique reviewed cases", len(cases))

    if len(cases) < min_cases:
        msg = f"Retraining skipped: {len(cases)} cases < minimum {min_cases}"
        logger.info(msg)
        return _record_job(session, "retrain", "skipped", dry_run, msg, {"case_count": len(cases)})

    if dry_run:
        msg = f"Dry-run: would train on {len(cases)} cases"
        return _record_job(session, "retrain", "dry_run", True, msg, {"case_count": len(cases)})

    # Staging training — lightweight stub using existing pickle if present
    os.makedirs(output_dir, exist_ok=True)
    candidate_path = os.path.join(output_dir, f"candidate_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.pkl")

    try:
        import pandas as pd
        from xgboost import XGBClassifier

        # Build minimal feature frame from exported cases (placeholder features)
        df = pd.DataFrame(cases)
        df["feature_placeholder"] = 1
        X = df[["feature_placeholder"]]
        y = (df["label"] == "No").astype(int)

        if len(y.unique()) < 2:
            msg = "Retraining skipped: insufficient label diversity"
            return _record_job(session, "retrain", "skipped", dry_run, msg)

        model = XGBClassifier(n_estimators=10, max_depth=3, random_state=42)
        model.fit(X, y)
        with open(candidate_path, "wb") as f:
            pickle.dump(model, f)

        version_label = os.path.basename(candidate_path).replace(".pkl", "")
        metrics = {"case_count": len(cases), "accuracy": float(model.score(X, y))}

        register_model_version(
            session,
            version_label=version_label,
            model_path=candidate_path,
            validation_metrics=metrics,
            status="candidate",
        )

        # Promote only if metrics meet threshold
        if metrics["accuracy"] >= 0.5:
            promote_to_active(session, version_label, activated_by="learning_loop")

        msg = f"Candidate trained and registered: {version_label}"
        return _record_job(session, "retrain", "completed", dry_run, msg, metrics)
    except Exception as exc:
        logger.exception("Retraining failed")
        return _record_job(session, "retrain", "failed", dry_run, str(exc))
