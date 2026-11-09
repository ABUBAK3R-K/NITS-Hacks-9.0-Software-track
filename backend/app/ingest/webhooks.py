"""Alertmanager and Grafana webhook bodies -> Event (U6). Pure functions, no I/O.

Detection is by shape: both have "alerts"; Grafana also carries "orgId" or
"title", Alertmanager carries "receiver"/"groupKey" and no "orgId".
Each alert becomes one kind="alert" event: service from labels.service (else
labels.job), ts from startsAt, name from labels.alertname, message from
annotations.summary, severity from labels.severity (warning -> warn). A
resolved alert becomes an info event. The id is a hash of fingerprint +
startsAt + status, so re-sending the same webhook is deduplicated by the store.
"""
from __future__ import annotations

import hashlib
from typing import Any, Literal, Optional

from app.schemas import Event

from .parsers import norm_ts

WebhookKind = Literal["alertmanager", "grafana"]

_SEVERITY = {
    "info": "info", "none": "info", "low": "info",
    "warning": "warn", "warn": "warn", "medium": "warn",
    "error": "error", "high": "error", "major": "error",
    "critical": "critical", "page": "critical", "fatal": "critical",
}


def detect_webhook(body: Any) -> Optional[WebhookKind]:
    if not isinstance(body, dict) or not isinstance(body.get("alerts"), list):
        return None
    if "orgId" in body or "title" in body:
        return "grafana"
    if "receiver" in body or "groupKey" in body:
        return "alertmanager"
    return None


def _event_id(alert: dict, starts_at: str, status: str) -> str:
    fp = str(alert.get("fingerprint") or "")
    if not fp:  # no fingerprint: hash the sorted labels instead
        labels = alert.get("labels") or {}
        fp = ",".join(f"{k}={labels[k]}" for k in sorted(labels))
    h = hashlib.sha1(f"{fp}|{starts_at}|{status}".encode("utf-8")).hexdigest()[:16]
    return f"evt_wh{h}"


def _alert_to_event(alert: dict, source_kind: WebhookKind) -> Event:
    labels = alert.get("labels") or {}
    ann = alert.get("annotations") or {}
    service = labels.get("service") or labels.get("job")
    if not service:
        raise ValueError("alert has no labels.service or labels.job")
    starts = alert.get("startsAt")
    if not starts:
        raise ValueError("alert has no startsAt")
    ts = norm_ts(str(starts))
    status = str(alert.get("status") or "firing").lower()
    severity = _SEVERITY.get(str(labels.get("severity", "")).lower(), "warn")
    if status == "resolved":
        severity = "info"
    attrs: dict[str, Any] = {"status": status, "webhook": source_kind}
    if alert.get("fingerprint"):
        attrs["fingerprint"] = str(alert["fingerprint"])
    ends = alert.get("endsAt")
    if status == "resolved" and ends:
        attrs["ends_at"] = str(ends)
    return Event(
        id=_event_id(alert, ts, status),
        ts=ts,
        source="api",
        service=str(service),
        kind="alert",
        severity=severity,
        name=str(labels.get("alertname") or "alert"),
        message=(str(ann["summary"]) if ann.get("summary") else None),
        attrs=attrs,
    )


def _parse(body: dict, kind: WebhookKind) -> tuple[list[Event], list[str]]:
    events: list[Event] = []
    errors: list[str] = []
    for i, alert in enumerate(body.get("alerts") or []):
        try:
            if not isinstance(alert, dict):
                raise ValueError("alert is not an object")
            events.append(_alert_to_event(alert, kind))
        except Exception as exc:
            errors.append(f"alert {i}: {exc}")
    return events, errors


def parse_alertmanager_with_errors(body: dict) -> tuple[list[Event], list[str]]:
    return _parse(body, "alertmanager")


def parse_grafana_with_errors(body: dict) -> tuple[list[Event], list[str]]:
    return _parse(body, "grafana")


def parse_alertmanager(body: dict) -> list[Event]:
    return _parse(body, "alertmanager")[0]


def parse_grafana(body: dict) -> list[Event]:
    return _parse(body, "grafana")[0]


def parse_webhook(body: Any) -> Optional[tuple[list[Event], list[str]]]:
    """(events, errors) for a recognised webhook body, else None."""
    kind = detect_webhook(body)
    if kind is None:
        return None
    return _parse(body, kind)
