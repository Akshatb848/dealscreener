"""Slack alert abstraction for Human-Synapse escalations.

Mockable and safe by default: when ``SLACK_ALERTS_ENABLED`` is false or no webhook
is configured, alerts are logged only (no network call). No secrets are hardcoded.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request

logger = logging.getLogger(__name__)


def slack_enabled() -> bool:
    return os.environ.get("SLACK_ALERTS_ENABLED", "false").lower() in {"1", "true", "yes"}


def send_alert(text: str, *, severity: str = "medium", context: dict | None = None) -> dict:
    """Send (or mock) a Slack alert. Returns a delivery descriptor; never raises."""
    channel = os.environ.get("SLACK_ALERT_CHANNEL", "#deal-alerts")
    webhook = os.environ.get("SLACK_WEBHOOK_URL", "")
    payload = {"channel": channel, "text": f"[{severity.upper()}] {text}", "context": context or {}}

    if not slack_enabled() or not webhook:
        logger.info("slack_alert_mock channel=%s severity=%s text=%s", channel, severity, text)
        return {"delivered": False, "mode": "mock", "channel": channel}

    try:  # pragma: no cover - real network path
        req = urllib.request.Request(
            webhook, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            ok = 200 <= resp.status < 300
        return {"delivered": ok, "mode": "live", "channel": channel}
    except Exception as exc:
        logger.warning("slack_alert_failed: %s", exc)
        return {"delivered": False, "mode": "error", "channel": channel, "error": str(exc)}
