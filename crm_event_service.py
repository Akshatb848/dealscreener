"""CRM event abstraction with retry tracking (channel-agnostic).

Records outbound CRM/Slack notifications as ``CRMEvent`` rows so every push is
auditable and retryable. Any concrete CRM transport plugs in as a ``sender``
callable; the firm-specific CRM adapter is omitted from the public version.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from models import CRMEvent


def record_event(
    session: Session, *, channel: str, event_type: str,
    company_id: str | None = None, evaluation_run_id: str | None = None,
    payload: dict[str, Any] | None = None, correlation_id: str | None = None,
) -> str:
    event = CRMEvent(
        channel=channel, event_type=event_type, status="pending", attempts=0,
        company_id=company_id, evaluation_run_id=evaluation_run_id,
        payload_json=json.dumps(payload, default=str) if payload is not None else None,
        correlation_id=correlation_id,
    )
    session.add(event)
    session.flush()
    return event.id


def _max_attempts() -> int:
    return int(os.environ.get("CRM_PUSH_MAX_ATTEMPTS", "3"))


def attempt_send(session: Session, event_id: str, sender: Callable[[dict], bool]) -> bool:
    """Attempt delivery via ``sender(payload)->bool``. Updates attempts/status.
    Never raises; failures are recorded for retry until max attempts."""
    event = session.get(CRMEvent, event_id)
    if event is None:
        return False
    event.attempts += 1
    event.updated_at = datetime.now(timezone.utc)
    payload = json.loads(event.payload_json or "{}")
    try:
        ok = bool(sender(payload))
    except Exception as exc:
        ok = False
        event.last_error = str(exc)
    if ok:
        event.status = "sent"
        event.last_error = None
        return True
    event.status = "failed" if event.attempts >= _max_attempts() else "pending"
    return False


def retry_pending(session: Session, sender: Callable[[dict], bool], *, channel: str | None = None,
                  limit: int = 100) -> dict[str, int]:
    """Retry pending (non-exhausted) CRM events. Returns a small summary."""
    q = session.query(CRMEvent).filter(CRMEvent.status == "pending")
    if channel:
        q = q.filter(CRMEvent.channel == channel)
    rows = q.order_by(CRMEvent.created_at.asc()).limit(limit).all()
    sent = failed = 0
    for ev in rows:
        if ev.attempts >= _max_attempts():
            ev.status = "failed"
            continue
        ok = attempt_send(session, ev.id, sender)
        sent += int(ok)
        failed += int(not ok)
    return {"retried": len(rows), "sent": sent, "failed": failed}
