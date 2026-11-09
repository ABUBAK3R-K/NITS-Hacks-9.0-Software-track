"""U1 HTTP routes: /api/runs/*, /api/eval/results, sandbox incident lookups. Real engine."""
import json

import pytest
from fastapi.testclient import TestClient

from app.api import eval_results
from app.main import app
from app.schemas import EvalResults, Incident, Run, RunReveal
from app.store import store
from tests.conftest import load_fixture


@pytest.fixture
def client():
    store.running = False
    store.reset()
    with TestClient(app) as c:
        yield c
    store.running = False
    store.reset()


def test_blind_run_hidden_then_revealed(client):
    live_before = len(store.events)
    r = client.post("/api/runs/blind")
    assert r.status_code == 200
    run = Run.model_validate(r.json())
    assert run.mode == "blind" and run.spec_hidden and run.spec is None
    assert run.run_id.startswith("run_")
    assert len(store.events) == live_before == 0  # live store untouched
    g = Run.model_validate(client.get(f"/api/runs/{run.run_id}").json())
    assert g.run_id == run.run_id and g.spec is None
    rv = RunReveal.model_validate(client.get(f"/api/runs/{run.run_id}/reveal").json())
    assert rv.expected_root == rv.spec.target
    assert rv.matched == (rv.our_top1 == rv.spec.target and not rv.abstained)
    g = Run.model_validate(client.get(f"/api/runs/{run.run_id}").json())
    assert not g.spec_hidden and g.spec == rv.spec


def test_designed_run_and_incident_lookups(client):
    body = {"target": "postgres", "kind": "pool_exhaustion", "onset_jitter_s": 3,
            "decoy": {"service": "ledger-svc", "offset_s": -30}, "seed": 4}
    r = client.post("/api/runs/designed", json=body)
    assert r.status_code == 200
    run = Run.model_validate(r.json())
    assert run.mode == "designed" and not run.spec_hidden and run.spec.target == "postgres"
    assert run.incidents and run.incidents[0].mttd_s is not None
    iid = run.incidents[0].id
    inc = Incident.model_validate(client.get(f"/api/incidents/{iid}").json())
    assert inc.id == iid
    assert iid not in store.incidents  # sandbox only, never the live store
    rv = RunReveal.model_validate(client.get(f"/api/runs/{run.run_id}/reveal").json())
    assert rv.our_top1 == "postgres" and rv.matched


@pytest.mark.parametrize("body", [
    {"target": "redis-session", "kind": "bad_deploy"},
    {"target": "auth-svc", "kind": "pool_exhaustion"},
    {"target": "nope", "kind": "down"},
    {"target": "postgres", "kind": "down", "decoy": {"service": "postgres", "offset_s": 0}},
])
def test_designed_invalid_spec_422(client, body):
    assert client.post("/api/runs/designed", json=body).status_code == 422


def test_unknown_run_404(client):
    assert client.get("/api/runs/run_nope").status_code == 404
    assert client.get("/api/runs/run_nope/reveal").status_code == 404


def test_eval_results_fallback_and_file(client, tmp_path, monkeypatch):
    monkeypatch.setattr(eval_results, "RESULTS", tmp_path / "missing.json")
    res = EvalResults.model_validate(client.get("/api/eval/results").json())
    assert res.n == 0 and res.methods == []
    real = load_fixture("eval_results.json") | {"n": 100, "seeds": list(range(50, 100))}
    p = tmp_path / "results.json"
    p.write_text(json.dumps(real), encoding="utf-8")
    monkeypatch.setattr(eval_results, "RESULTS", p)
    assert client.get("/api/eval/results").json()["n"] == 100


def test_report_router_finds_sandbox_incident(client, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    run = Run.model_validate(client.post("/api/runs/designed",
                                         json={"target": "psp-adapter", "kind": "latency", "seed": 1}).json())
    iid = run.incidents[0].id
    r = client.post(f"/api/incidents/{iid}/report")
    assert r.status_code == 200 and r.json()["incident_id"] == iid
