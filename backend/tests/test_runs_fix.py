"""U5: apply_fix heals the sim (root first, then callers), emits fix_applied, mttr from the engine."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import ApplyFixResult, FaultSpec, Run
from app.sim.faults import fix_matches
from app.sim.generator import Simulator
from app.store import Store, store

START = datetime(2026, 11, 9, 8, 30, 0, tzinfo=timezone.utc)


def ts(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def last_bad(events, svc):
    return max((e.ts for e in events if e.service == svc and e.severity != "info"), default=None)


def test_fix_matches_table():
    assert fix_matches(FaultSpec(target="postgres", kind="pool_exhaustion"), "raise_pool_postgres")
    assert fix_matches(FaultSpec(target="payments-svc", kind="bad_deploy"), "rollback_deploy")
    assert fix_matches(FaultSpec(target="psp-adapter", kind="latency"), "circuit_breaker_psp")
    assert fix_matches(FaultSpec(target="redis-session", kind="down"), "restart_service")
    assert not fix_matches(FaultSpec(target="postgres", kind="pool_exhaustion"), "rollback_deploy")
    assert not fix_matches(FaultSpec(target="psp-adapter", kind="latency"), "restart_service")


def test_sim_heals_root_then_callers_and_emits_fix_applied():
    sim = Simulator(START, seed=3)
    f = sim.inject_spec(FaultSpec(target="postgres", kind="pool_exhaustion"), START + timedelta(seconds=30))
    before = sim.advance(START + timedelta(seconds=90))
    fix_t = sim.t
    sim.apply_fix(f, "raise_pool_postgres")
    after = sim.advance(fix_t + timedelta(seconds=60))
    fx = [e for e in after if e.name == "fix_applied"]
    assert len(fx) == 1 and fx[0].kind == "change" and fx[0].service == "postgres"
    assert fx[0].attrs["fix_id"] == "raise_pool_postgres" and fx[0].ts == ts(fix_t)
    # still degraded at the moment of the fix, back to baseline within ~10 s at the root
    assert last_bad(before, "postgres")
    root_end = last_bad(after, "postgres")
    assert root_end is not None and root_end < ts(fix_t + timedelta(seconds=10.5))
    pool = [e.value for e in after if e.name == "db_pool_in_use_pct"]
    assert pool[0] > 90 and pool[-1] < 50  # eases down, not a jump
    for svc in ("auth-svc", "ledger-svc", "payments-svc", "api-gateway"):
        lb = last_bad(after, svc)
        assert lb is None or lb < ts(fix_t + timedelta(seconds=20))
    # callers recover after the root
    assert f.heal_start["ledger-svc"] >= f.heal_end["postgres"]
    assert f.heal_start["payments-svc"] > f.heal_start["ledger-svc"]
    late = [e for e in after if e.ts >= ts(fix_t + timedelta(seconds=20))]
    assert late and all(e.severity == "info" for e in late)
    assert not [e for e in late if e.kind == "request" and e.attrs["status"] >= 500]
    assert sim.active_faults == []


def test_sandbox_apply_fix_reaches_resolved_via_engine():
    st = Store()
    run = st.runs.create(FaultSpec(target="postgres", kind="pool_exhaustion", seed=1), "designed")
    inc = run.incidents[0]
    assert inc.status != "RESOLVED" and inc.mttr_s is None
    wrong = st.apply_fix(inc.id, "rollback_deploy")
    assert not wrong.applied
    res = st.apply_fix(inc.id, "raise_pool_postgres")
    assert res.applied and res.incident_id == inc.id
    new = st.get_incident(inc.id)
    assert new.status == "RESOLVED" or new.lifecycle == "RESOLVED"
    assert new.mttd_s == inc.mttd_s
    assert new.mttr_s is not None and new.mttr_s > 90  # injection -> fix took 90 s of run time
    assert any(e.name == "fix_applied" for e in st.events_for_incident(inc.id) + st.runs.get(run.run_id).events)
    again = st.apply_fix(inc.id, "raise_pool_postgres")
    assert not again.applied
    assert st.apply_fix("inc_nope", "restart_service") is None


def test_live_apply_fix_then_engine_resolves_and_mttr():
    st = Store()
    st.start()
    st.inject("redis_down")
    t0 = st.sim.t
    for k in range(1, 30):
        st.tick(t0 + timedelta(seconds=k))
        st.recompute(force=True)
        if st.incidents:
            break
    inc = next(iter(st.incidents.values()))
    assert inc.status != "RESOLVED" and inc.mttr_s is None
    assert not st.apply_fix(inc.id, "raise_pool_postgres").applied
    res = st.apply_fix(inc.id, "restart_service")
    assert res.applied and st.status().active_faults == []
    t1 = st.sim.t
    resolved = None
    for k in range(5, 120, 5):
        st.tick(t1 + timedelta(seconds=k))
        st.recompute(force=True)
        cur = st.incidents.get(inc.id)
        if cur and (cur.status == "RESOLVED" or cur.lifecycle == "RESOLVED"):
            resolved = cur
            break
    assert resolved is not None and resolved.mttr_s is not None and resolved.mttr_s > resolved.mttd_s
    # the fault can be injected again once it was fixed
    st.inject("redis_down")
    assert st.status().active_faults == ["redis_down"]


@pytest.fixture
def client():
    store.running = False
    store.reset()
    with TestClient(app) as c:
        yield c
    store.running = False
    store.reset()


def test_api_apply_fix(client):
    run = Run.model_validate(client.post("/api/runs/designed",
                                         json={"target": "redis-session", "kind": "down", "seed": 3}).json())
    iid = run.incidents[0].id
    assert client.post(f"/api/incidents/{iid}/apply_fix", json={"fix_id": "nope"}).status_code == 422
    assert client.post("/api/incidents/inc_nope/apply_fix", json={"fix_id": "restart_service"}).status_code == 404
    r = client.post(f"/api/incidents/{iid}/apply_fix", json={"fix_id": "restart_service"})
    assert r.status_code == 200
    out = ApplyFixResult.model_validate(r.json())
    assert out.applied
    g = Run.model_validate(client.get(f"/api/runs/{run.run_id}").json())
    inc = next(i for i in g.incidents if i.id == iid)
    assert inc.mttr_s is not None
