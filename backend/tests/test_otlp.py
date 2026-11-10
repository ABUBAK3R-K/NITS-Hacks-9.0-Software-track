"""OTLP/JSON logs and metrics ingest through POST /api/events (detected by shape)."""
import pytest
from fastapi.testclient import TestClient

from app.ingest.otlp import detect_otlp, parse_otlp_logs, parse_otlp_metrics
from app.main import app
from app.store import store
from tests.conftest import load_fixture

LOGS = load_fixture("otlp_logs.json")
METRICS = load_fixture("otlp_metrics.json")


def test_detect():
    assert detect_otlp(LOGS) == "otlp_logs"
    assert detect_otlp(METRICS) == "otlp_metrics"
    assert detect_otlp({"alerts": []}) is None and detect_otlp([1]) is None


def test_parse_logs():
    events, errors = parse_otlp_logs(LOGS)
    assert errors == [] and len(events) == 5
    first = events[0]
    assert first.service == "postgres" and first.kind == "log" and first.name == "log"
    assert first.severity == "critical" and first.ts == "2026-11-10T08:30:00.000Z"
    assert first.message.startswith("FATAL: remaining connection slots")
    by_svc = {e.service: e for e in events}
    assert by_svc["auth-svc"].severity == "error" and by_svc["auth-svc"].attrs["user_id"] == "u_1042"
    assert by_svc["api-gateway"].severity == "warn"
    assert by_svc["ledger-svc"].ts == "2026-11-10T08:30:01.600Z"


def test_severity_number_fallback():
    body = {"resourceLogs": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "redis-session"}}]},
                              "scopeLogs": [{"logRecords": [
                                  {"timeUnixNano": "1793349000000000000", "severityNumber": 18,
                                   "body": {"stringValue": "connection refused"}},
                                  {"timeUnixNano": "1793349001000000000", "severityNumber": 9,
                                   "body": {"stringValue": "ok"}}]}]}]}
    events, errors = parse_otlp_logs(body)
    assert errors == [] and [e.severity for e in events] == ["error", "info"]


def test_parse_metrics():
    events, errors = parse_otlp_metrics(METRICS)
    assert errors == [] and len(events) == 7
    assert all(e.kind == "metric" and e.source == "api" for e in events)
    pool = [e for e in events if e.name == "db_pool_in_use_pct"]
    assert [e.value for e in pool] == [44.0, 100.0, 100.0] and pool[0].service == "postgres"
    total = next(e for e in events if e.name == "http_server_requests_total")
    assert total.value == 1842.0 and total.service == "payments-svc" and total.attrs["http.route"] == "/v1/payments"
    lat = next(e for e in events if e.name == "latency_p95_ms")
    assert lat.service == "api-gateway" and lat.attrs["unit"] == "ms"


def test_missing_service_name_and_histogram_are_reported():
    body = {"resourceMetrics": [
        {"resource": {"attributes": []}, "scopeMetrics": []},
        {"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "postgres"}}]},
         "scopeMetrics": [{"metrics": [{"name": "lat", "histogram": {"dataPoints": []}},
                                       {"name": "x", "gauge": {"dataPoints": [{"timeUnixNano": "1"}]}}]}]}]}
    events, errors = parse_otlp_metrics(body)
    assert events == [] and len(errors) == 3
    assert "service.name" in errors[0] and "gauge and sum" in errors[1] and "asDouble" in errors[2]


@pytest.fixture
def client():
    store.running = False
    store.reset()
    with TestClient(app) as c:
        yield c
    store.running = False
    store.reset()


def test_post_otlp_as_is_and_dedup(client):
    r = client.post("/api/events", json=LOGS)
    assert r.status_code == 200 and r.json() == {"accepted": 5, "rejected": 0, "errors": []}
    r = client.post("/api/events", json=METRICS)
    assert r.json()["accepted"] == 7
    r = client.post("/api/events", json=METRICS)  # re-sent export is deduplicated
    assert r.json()["accepted"] == 0 and r.json()["rejected"] == 7
    assert client.post("/api/events", json={"resourceSpans": []}).status_code == 422
