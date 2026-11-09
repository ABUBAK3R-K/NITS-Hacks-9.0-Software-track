"""HTTP API via TestClient, with analyze monkeypatched to return the fixture incident."""
import json

import pytest
from fastapi.testclient import TestClient

from app.engine import run as engine_run
from app.main import app
from app.schemas import Event, Incident, IncidentSummary, IngestResult, SimStatus, Topology, TopologyState
from app.sim.generator import simulate
from app.store import store
from tests.conftest import load_fixture


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


def parse_sse(text: str):
    msgs = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        lines = [ln for ln in block.split("\n") if ln and not ln.startswith(":")]
        if not lines:
            continue
        name = next(ln.removeprefix("event: ") for ln in lines if ln.startswith("event: "))
        data = next(ln.removeprefix("data: ") for ln in lines if ln.startswith("data: "))
        msgs.append((name, json.loads(data)))
    return msgs


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_topology(client):
    r = client.get("/api/topology")
    assert r.status_code == 200
    body = r.json()
    assert body == load_fixture("topology.json")
    Topology.model_validate(body)


def test_ingest_events_and_incidents(client):
    evs = simulate(20, seed=1)
    payload = [e.model_dump(mode="json") for e in evs]
    r = client.post("/api/events", json=payload)
    res = IngestResult.model_validate(r.json())
    assert res.accepted == len(evs) and res.rejected == 0
    r = client.post("/api/events", json=payload[:3])
    assert r.json()["rejected"] == 3 and r.json()["errors"]
    assert client.post("/api/events", json=[{"id": "bad"}]).status_code == 422

    store.recompute(force=True)
    lst = client.get("/api/incidents").json()
    assert [IncidentSummary.model_validate(x).id for x in lst] == ["inc_1"]
    assert lst == load_fixture("incidents_list.json")[:1]
    one = client.get("/api/incidents/inc_1")
    assert one.status_code == 200 and one.json() == load_fixture("incident_db_pool.json")
    assert client.get("/api/incidents/nope").status_code == 404


def test_upload_log_jsonl_csv(client):
    log = (b"2026-11-09T08:32:05.120Z ERROR auth-svc login failed\n"
           b"garbage line\n"
           b"2026-11-09T08:32:06.000Z FATAL postgres FATAL: remaining connection slots are reserved\n")
    r = client.post("/api/upload", files={"file": ("out.log", log, "text/plain")})
    res = IngestResult.model_validate(r.json())
    assert res.accepted == 2 and res.rejected == 1 and res.errors[0].startswith("line 2:")
    up = [e for e in store.events if e.source == "upload"]
    assert len(up) == 2 and all(e.kind == "log" and e.name == "log" for e in up)

    jl = json.dumps({"ts": "2026-11-09T08:33:00Z", "service": "postgres", "kind": "metric",
                     "severity": "critical", "name": "db_pool_in_use_pct", "value": 100})
    r = client.post("/api/upload", files={"file": ("x.jsonl", jl.encode(), "application/json")})
    assert r.json()["accepted"] == 1

    csv = b"ts,service,kind,severity,name,value\n2026-11-09T08:33:01Z,auth-svc,metric,error,error_rate,0.3\n"
    r = client.post("/api/upload", files={"file": ("x.csv", csv, "text/csv")})
    assert r.json()["accepted"] == 1
    assert len({e.id for e in store.events}) == len(store.events)


def test_upload_sample_file(client):
    from tests.test_ingest import SAMPLE
    r = client.post("/api/upload", files={"file": (SAMPLE.name, SAMPLE.read_bytes(), "text/plain")})
    assert r.json()["rejected"] == 0 and r.json()["accepted"] >= 80


def test_sim_routes(client):
    s = SimStatus.model_validate(client.post("/api/sim/start").json())
    assert s.running and s.events_in_store > 0 and s.active_faults == []
    s = SimStatus.model_validate(client.post("/api/sim/fault", json={"type": "db_pool_exhaustion"}).json())
    assert s.active_faults == ["db_pool_exhaustion"]
    assert client.post("/api/sim/fault", json={"type": "meteor"}).status_code == 422
    s = SimStatus.model_validate(client.post("/api/sim/reset").json())
    assert s.active_faults == [] and client.get("/api/incidents").json() == []


def test_fault_starts_stopped_sim(client):
    s = client.post("/api/sim/fault", json={"type": "psp_slow"}).json()
    assert s["running"] is True and s["active_faults"] == ["psp_slow"]


def test_stream_format(client):
    store.add_events(simulate(5, seed=1))
    store.recompute(force=True)
    with client.stream("GET", "/api/stream?limit=2") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        raw = "".join(r.iter_text())
    assert "\r\n" not in raw
    msgs = parse_sse(raw)
    assert [m[0] for m in msgs] == ["topology_state", "incident_update"]
    ts = TopologyState.model_validate(msgs[0][1])
    assert any(s.is_root_cause and s.service == "postgres" for s in ts.services)
    assert msgs[1][1] == load_fixture("incident_db_pool.json")
    # exact wire framing: "event: <name>\ndata: <compact json>\n\n"
    first = raw.split("\n\n")[0].split("\n")
    assert first[0] == "event: topology_state" and first[1].startswith("data: {\"ts\":")


def test_stream_live_events(client):
    import threading
    import time

    def later():
        time.sleep(0.3)
        store.add_events(simulate(60, [(10, "redis_down")], seed=2))

    threading.Thread(target=later).start()
    with client.stream("GET", "/api/stream?limit=6") as r:
        raw = "".join(r.iter_text())
    msgs = parse_sse(raw)
    assert len(msgs) == 6
    evs = [Event.model_validate(d) for n, d in msgs if n == "event"]
    assert evs and all(e.severity != "info" for e in evs)
    assert "value" in raw and "\"message\":null" not in raw
