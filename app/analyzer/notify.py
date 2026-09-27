"""Incident notification via an optional webhook.

Free and local-friendly: any HTTP endpoint that accepts a JSON POST works
(Slack/Discord/ntfy/webhook.site, or a local receiver). If no webhook is configured
or the delivery fails, the application continues normally - notification is
best-effort and never blocks or crashes the analysis pipeline.

The payload contains no secrets. Credentials are never hard-coded; the webhook URL
(and any embedded token) comes from configuration and is never logged.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

SENSITIVE_URL_HINTS = ("token=", "key=", "secret=", "password=")


@dataclass
class NotificationResult:
    delivered: bool
    channel: str
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"delivered": self.delivered, "channel": self.channel, "detail": self.detail}


def _safe_detail(text: str | None) -> str | None:
    """Avoid echoing a webhook URL that may embed a token into logs/reports."""
    if not text:
        return text
    lowered = text.lower()
    if any(hint in lowered for hint in SENSITIVE_URL_HINTS):
        return "webhook delivery failed (url redacted)"
    return text


def build_payload(incident: dict[str, Any]) -> dict[str, Any]:
    """Build the notification body from an incident report dict."""
    return {
        "text": (
            f"[{incident.get('severity', 'info').upper()}] Incident {incident.get('incident_id')} "
            f"on {incident.get('service')}: {incident.get('summary')}"
        ),
        "incident_id": incident.get("incident_id"),
        "timestamp": incident.get("timestamp"),
        "service": incident.get("service"),
        "severity": incident.get("severity"),
        "suspected_root_cause": incident.get("suspected_root_cause"),
        "confidence": incident.get("confidence"),
        "analysis_source": incident.get("analysis_source"),
        "recommended_action": incident.get("recommended_action"),
        "evidence_lines": (incident.get("evidence_lines") or [])[:5],
        "report_reference": f"incidents/generated/{incident.get('incident_id')}.json",
        "requires_human_validation": True,
    }


class Notifier:
    """Best-effort webhook notifier."""

    def __init__(self, webhook_url: str = "", timeout_seconds: float = 5.0) -> None:
        self.webhook_url = (webhook_url or "").strip()
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.webhook_url)

    def send(self, incident: dict[str, Any]) -> NotificationResult:
        """Deliver a notification. Never raises; always returns a result."""
        if not self.configured:
            return NotificationResult(False, "none", "no webhook configured; skipping notification")
        payload = build_payload(incident)
        data = json.dumps(payload).encode("utf-8")
        try:
            request = urllib.request.Request(
                self.webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                status = getattr(response, "status", 200)
                if 200 <= status < 300:
                    return NotificationResult(True, "webhook", f"delivered (HTTP {status})")
                return NotificationResult(False, "webhook", f"webhook returned HTTP {status}")
        except urllib.error.HTTPError as exc:
            return NotificationResult(False, "webhook", f"webhook HTTP {exc.code}")
        except urllib.error.URLError as exc:
            return NotificationResult(False, "webhook", _safe_detail(f"webhook unreachable: {exc.reason}"))
        except (OSError, ValueError) as exc:
            return NotificationResult(False, "webhook", _safe_detail(f"webhook failed: {exc}"))
