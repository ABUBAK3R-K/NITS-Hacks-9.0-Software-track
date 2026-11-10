"""OpenTelemetry OTLP/JSON logs and metrics -> Event. Pure functions, no I/O.

Detection is by shape, next to the Alertmanager/Grafana detection:
  logs     {"resourceLogs": [{"resource": {...}, "scopeLogs": [{"logRecords": [...]}]}]}
  metrics  {"resourceMetrics": [{"resource": {...}, "scopeMetrics": [{"metrics": [...]}]}]}

Mapping:
  service   resource attribute service.name
  ts        timeUnixNano (else observedTimeUnixNano), nanoseconds since epoch
  logs      kind=log, name "log", message body.stringValue, severity from
            severityText (else severityNumber: 1-8 info, 9-12 info, 13-16 warn,
            17-20 error, 21-24 critical)
  metrics   kind=metric, name = metric name, value asDouble / asInt, one event per
            gauge or sum data point (histograms are skipped and reported)
Data point / log record attributes are flattened into attrs. Ids are a hash of the
content, so re-sending the same export is deduplicated by the store.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from app.schemas import Event

OtlpKind = Literal["otlp_logs", "otlp_metrics"]

_TEXT_SEVERITY = {
    "trace": "info", "debug": "info", "info": "info", "notice": "info",
    "warn": "warn", "warning": "warn",
    "error": "error", "err": "error",
    "fatal": "critical", "critical": "critical", "crit": "critical", "emergency": "critical",
}


def detect_otlp(body: Any) -> Optional[OtlpKind]:
    if not isinstance(body, dict):
        return None
    if isinstance(body.get("resourceLogs"), list):
        return "otlp_logs"
    if isinstance(body.get("resourceMetrics"), list):
        return "otlp_metrics"
    return None


def _any_value(v: Any) -> Any:
    """OTLP AnyValue {"stringValue": ...} -> a plain scalar (lists/maps as JSON text)."""
    if not isinstance(v, dict):
        return v
    for k in ("stringValue", "boolValue", "doubleValue"):
        if k in v:
            return v[k]
    if "intValue" in v:
        return int(v["intValue"])  # OTLP JSON sends int64 as a string
    if "arrayValue" in v or "kvlistValue" in v:
        return json.dumps(v, sort_keys=True)
    return None


def _attrs(items: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for kv in items or []:
        if isinstance(kv, dict) and "key" in kv:
            out[str(kv["key"])] = _any_value(kv.get("value"))
    return out


def _ts(nanos: Any) -> str:
    n = int(nanos)
    if n <= 0:
        raise ValueError("timeUnixNano is missing or zero")
    t = datetime.fromtimestamp(n / 1e9, tz=timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def _severity(rec: dict) -> str:
    text = str(rec.get("severityText") or "").strip().lower()
    if text in _TEXT_SEVERITY:
        return _TEXT_SEVERITY[text]
    num = int(rec.get("severityNumber") or 0)
    if num >= 21:
        return "critical"
    if num >= 17:
        return "error"
    if num >= 13:
        return "warn"
    return "info"


def _event_id(*parts: Any) -> str:
    h = hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]
    return f"evt_ot{h}"


def _service(resource: Any) -> str:
    svc = _attrs((resource or {}).get("attributes")).get("service.name")
    if not svc:
        raise ValueError("resource has no service.name attribute")
    return str(svc)


def parse_otlp_logs(body: dict) -> tuple[list[Event], list[str]]:
    events: list[Event] = []
    errors: list[str] = []
    for ri, rl in enumerate(body.get("resourceLogs") or []):
        try:
            service = _service(rl.get("resource"))
        except Exception as exc:
            errors.append(f"resourceLogs {ri}: {exc}")
            continue
        for si, sl in enumerate(rl.get("scopeLogs") or []):
            for li, rec in enumerate(sl.get("logRecords") or []):
                where = f"resourceLogs {ri} scopeLogs {si} logRecord {li}"
                try:
                    ts = _ts(rec.get("timeUnixNano") or rec.get("observedTimeUnixNano") or 0)
                    msg = _any_value(rec.get("body"))
                    attrs = _attrs(rec.get("attributes"))
                    attrs["otlp"] = "logs"
                    if rec.get("traceId"):
                        attrs["trace_id"] = str(rec["traceId"])
                    events.append(Event(
                        id=_event_id(service, ts, msg, rec.get("severityNumber"), rec.get("traceId"),
                                     rec.get("spanId")),
                        ts=ts, source="api", service=service, kind="log", severity=_severity(rec),
                        name="log", message=None if msg is None else str(msg), attrs=attrs))
                except Exception as exc:
                    errors.append(f"{where}: {exc}")
    return events, errors


def parse_otlp_metrics(body: dict) -> tuple[list[Event], list[str]]:
    events: list[Event] = []
    errors: list[str] = []
    for ri, rm in enumerate(body.get("resourceMetrics") or []):
        try:
            service = _service(rm.get("resource"))
        except Exception as exc:
            errors.append(f"resourceMetrics {ri}: {exc}")
            continue
        for sm in rm.get("scopeMetrics") or []:
            for metric in sm.get("metrics") or []:
                name = str(metric.get("name") or "")
                data = metric.get("gauge") or metric.get("sum")
                if not name or not isinstance(data, dict):
                    errors.append(f"metric {name or '?'}: only gauge and sum metrics are supported")
                    continue
                for pi, dp in enumerate(data.get("dataPoints") or []):
                    try:
                        if "asDouble" in dp:
                            value = float(dp["asDouble"])
                        elif "asInt" in dp:
                            value = float(int(dp["asInt"]))
                        else:
                            raise ValueError("data point has no asDouble or asInt")
                        ts = _ts(dp.get("timeUnixNano") or 0)
                        attrs = _attrs(dp.get("attributes"))
                        attrs["otlp"] = "metrics"
                        if metric.get("unit"):
                            attrs["unit"] = str(metric["unit"])
                        events.append(Event(
                            id=_event_id(service, name, ts, value, sorted(attrs.items())),
                            ts=ts, source="api", service=service, kind="metric", severity="info",
                            name=name, value=value, attrs=attrs))
                    except Exception as exc:
                        errors.append(f"metric {name} point {pi}: {exc}")
    return events, errors


def parse_otlp(body: Any) -> Optional[tuple[list[Event], list[str]]]:
    """(events, errors) for an OTLP/JSON logs or metrics body, else None."""
    kind = detect_otlp(body)
    if kind == "otlp_logs":
        return parse_otlp_logs(body)
    if kind == "otlp_metrics":
        return parse_otlp_metrics(body)
    return None
