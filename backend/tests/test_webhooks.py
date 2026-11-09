"""Alertmanager + Grafana webhook ingest (U6), using the contract fixtures."""
import pytest
from fastapi.testclient import TestClient

from app.engine import run as engine_run
from app.ingest.webhooks import detect_webhook, parse_alertmanager, parse_grafana
from app.main import app
from app.schemas import Incident
from app.store import store
from tests.conftest import load_fixture

AM = load_fixture("alertmanager_webhook.json")
GF = load_fixture("grafana_webhook.json")


def test_detect():
    assert detect_webhook(AM) == "alertmanager"
    assert detect_webhook(GF) == "grafana"
    assert detect_webhook([]) is None
    assert detect_webhook({"alerts": []}) is None
    assert detect_webhook({"foo": 1}) is None


def test_parse_alertmanager():
    evs = parse_alertmanager(AM)
    assert len(evs) == 3
    pg, auth, gw = evs
    assert (pg.service, pg.kind, pg.severity, pg.source, pg.name) == (
        "postgres", "alert", "critical", "api", "PgConnectionsSaturated")
    assert pg.ts == "2026-11-09T08:32:11.300Z"
    assert pg.message == "postgres connection pool at 100% (100/100)"
    assert auth.service == "auth-svc"  # labels.job fallback
    assert gw.severity == "info" and gw.attrs["status"] == "resolved"  # resolved -> info
    assert [e.id for e in parse_alertmanager(AM)] == [e.id for e in evs]  # deterministic
    assert len({e.id for e in evs}) == 3


def test_parse_grafana():
    evs = parse_grafana(GF)
    assert [(e.service, e.name, e.severity) for e in evs] == [
        ("payments-svc", "PaymentFailureRateHigh", "critical"), ("ledger-svc", "LedgerWriteErrors", "critical")]
    assert evs[0].message == "payments-svc failure rate 38%" and evs[0].source == "api"


def test_warning_maps_to_warn():
    body = {"receiver": "r", "alerts": [{"status": "firing", "labels": {"alertname": "X", "service": "s",
                                                                       "severity": "warning"},
                                         "startsAt": "2026-11-09T08:00:00Z", "fingerprint": "f"}]}
    assert parse_alertmanager(body)[0].severity == "warn"


@pytest.fixture
def client(monkeypatch):
    inc = Incident.model_validate(load_fixture("incident_db_pool.json"))
    monkeypatch.setattr(engine_run, "analyze", lambda events, topo: [inc])
    store.running = False
    store.reset()
    with TestClient(app) as c:
        yield c
    store.running = False
    store.reset()


def test_post_webhooks_as_is(client):
    r = client.post("/api/events", json=AM)
    assert r.status_code == 200 and r.json() == {"accepted": 3, "rejected": 0, "errors": []}
    r = client.post("/api/events", json=GF)
    assert r.json()["accepted"] == 2
    r = client.post("/api/events", json=AM)  # re-sent webhook is deduplicated
    assert r.json()["accepted"] == 0 and r.json()["rejected"] == 3
    assert sum(1 for e in store.events if e.source == "api" and e.kind == "alert") == 5


def test_post_list_still_works_and_bad_bodies_422(client):
    ev = load_fixture("events_db_pool.json")[:2]
    assert client.post("/api/events", json=ev).json()["accepted"] == 2
    assert client.post("/api/events", json=[{"id": "bad"}]).status_code == 422
    assert client.post("/api/events", json={"hello": "world"}).status_code == 422
    bad_alert = dict(AM, alerts=[{"labels": {"alertname": "X"}, "startsAt": "2026-11-09T08:00:00Z"}])
    r = client.post("/api/events", json=bad_alert).json()
    assert r["accepted"] == 0 and r["rejected"] == 1 and "alert 0" in r["errors"][0]
