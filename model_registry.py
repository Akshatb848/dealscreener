"""Model version registry with active/candidate/rollback tracking."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from models import AuditLog, ModelVersion

STATUS_ACTIVE = "active"
STATUS_CANDIDATE = "candidate"
STATUS_ROLLBACK = "rollback"
STATUS_ARCHIVED = "archived"


def register_model_version(
    session: Session,
    *,
    version_label: str,
    model_path: str,
    model_type: str = "classifier",
    metadata: dict | None = None,
    validation_metrics: dict | None = None,
    status: str = STATUS_CANDIDATE,
) -> ModelVersion:
    existing = session.query(ModelVersion).filter_by(version_label=version_label).first()
    if existing:
        return existing
    mv = ModelVersion(
        version_label=version_label,
        model_path=model_path,
        model_type=model_type,
        metadata_json=json.dumps(metadata) if metadata else None,
        validation_metrics_json=json.dumps(validation_metrics) if validation_metrics else None,
        status=status,
    )
    session.add(mv)
    session.flush()
    return mv


def get_model_by_status(session: Session, status: str) -> ModelVersion | None:
    return session.query(ModelVersion).filter_by(status=status).order_by(ModelVersion.created_at.desc()).first()


def promote_to_active(session: Session, version_label: str, activated_by: str = "system") -> ModelVersion:
    candidate = session.query(ModelVersion).filter_by(version_label=version_label).one()
    current_active = get_model_by_status(session, STATUS_ACTIVE)
    if current_active:
        current_active.status = STATUS_ROLLBACK
    candidate.status = STATUS_ACTIVE
    candidate.activated_at = datetime.now(timezone.utc)
    candidate.activated_by = activated_by
    session.add(
        AuditLog(
            event_type="model_promoted",
            entity_type="model_version",
            entity_id=candidate.id,
            actor=activated_by,
            payload_json=json.dumps({"version_label": version_label}),
        )
    )
    return candidate


def get_active_model_version(session: Session) -> ModelVersion | None:
    env_label = os.environ.get("ACTIVE_MODEL_VERSION")
    if env_label:
        return session.query(ModelVersion).filter_by(version_label=env_label).first()
    return get_model_by_status(session, STATUS_ACTIVE)


def get_rollback_model_version(session: Session) -> ModelVersion | None:
    return get_model_by_status(session, STATUS_ROLLBACK)
