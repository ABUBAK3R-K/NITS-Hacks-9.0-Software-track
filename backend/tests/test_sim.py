"""Simulator: each fault touches only its expected services; deterministic."""
from datetime import datetime, timezone

import pytest

from app.schemas import Event
from app.sim.faults import AFFECTED
from app.sim.generator import simulate
from app.sim.topology import load_topology

START = datetime(2026, 11, 9, 8, 30, 0, tzinfo=timezone.utc)
EXPECTED = {
    "db_pool_exhaustion": {"postgres", "auth-svc", "ledger-svc", "payments-svc", "api-gateway"},
    "bad_deploy": {"payments-svc", "api-gateway"},
    "redis_down": {"redis-session", "auth-svc", "api-gateway"},
    "red_herring": {"postgres", "auth-svc", "ledger-svc", "payments-svc", "api-gateway"},
    "psp_slow": {"psp-adapter", "payments-svc", "api-gateway"},
}


def bad(events):
    return [e for e in events if e.severity != "info"]


def onset(events, svc):
    return min(e.ts for e in bad(events) if e.service == svc)


def test_topology_loads_from_fixture():
    t = load_topology()
    assert len(t.services) == 7 and len(t.edges) == 7


def test_baseline_is_all_info_and_well_formed():
    ev = simulate(60, [], START, seed=1)
    assert ev and not bad(ev)
    assert [e.id for e in ev] == sorted(e.id for e in ev)
    assert [e.ts for e in ev] == sorted(e.ts for e in ev)
    assert all(e.ts.endswith("Z") and len(e.ts) == 24 for e in ev)
    assert all(e.id.startswith("evt_") for e in ev)
    names = {(e.kind, e.name) for e in ev}
    for n in ("latency_p95_ms", "error_rate", "db_pool_in_use_pct", "pg_lock_wait_ms"):
        assert ("metric", n) in names
    assert ("request", "http_request") in names and ("log", "log") in names
    pay = [e for e in ev if e.kind == "request" and e.service == "payments-svc"]
    assert pay and all({"amount_inr", "order_id", "user_id", "status", "route"} <= set(e.attrs) for e in pay)
    pool = [e for e in ev if e.name == "db_pool_in_use_pct"]
    assert all(e.attrs["pool_max"] == 100 for e in pool)
    # metrics every 2 s per service: about 30 latency samples per service in 60 s
    gw = [e for e in ev if e.service == "api-gateway" and e.name == "latency_p95_ms"]
    assert 29 <= len(gw) <= 31


@pytest.mark.parametrize("fault", sorted(EXPECTED))
def test_fault_touches_only_expected_services(fault):
    ev = simulate(150, [(60, fault)], START, seed=3)
    touched = {e.service for e in bad(ev)}
    assert touched == EXPECTED[fault]
    assert AFFECTED[fault] == EXPECTED[fault]
    # nothing degraded before injection
    assert all(e.ts >= "2026-11-09T08:31:00.000Z" for e in bad(ev))
    # alerts fire and use the [FIRING] format
    alerts = [e for e in ev if e.kind == "alert"]
    assert alerts and all(e.message == f"[FIRING] {e.name} on {e.service}" for e in alerts)
    # metrics stay elevated until the end (sustained, not a blip)
    late = [e for e in bad(ev) if e.kind == "metric" and e.ts >= "2026-11-09T08:32:20.000Z"]
    assert {e.service for e in late} == EXPECTED[fault]


def test_redis_down_leaves_payments_ledger_postgres_healthy():
    ev = simulate(150, [(30, "redis_down")], START, seed=5)
    for svc in ("payments-svc", "ledger-svc", "postgres", "psp-adapter"):
        assert not [e for e in bad(ev) if e.service == svc]
    fails = [e for e in ev if e.kind == "request" and e.attrs["status"] >= 500]
    assert fails and {e.service for e in fails} == {"auth-svc"}


def test_db_pool_story():
    ev = simulate(120, [(60, "db_pool_exhaustion")], START, seed=7)
    order = sorted(EXPECTED["db_pool_exhaustion"], key=lambda s: onset(ev, s))
    assert order[0] == "postgres" and order[-1] == "api-gateway"
    assert any(e.service == "postgres" and e.kind == "log" and e.message.startswith("FATAL: remaining connection slots")
               for e in ev)
    assert any(e.name == "db_pool_in_use_pct" and e.value == 100.0 for e in ev)
    fails = [e for e in ev if e.kind == "request" and e.attrs["status"] == 503]
    assert {e.service for e in fails} == {"auth-svc", "payments-svc"}


def test_onset_lag_per_hop_is_realistic():
    from datetime import datetime as dt
    ev = simulate(120, [(60, "psp_slow")], START, seed=9)
    t = {s: dt.fromisoformat(onset(ev, s).replace("Z", "+00:00")) for s in EXPECTED["psp_slow"]}
    hop1 = (t["payments-svc"] - t["psp-adapter"]).total_seconds()
    hop2 = (t["api-gateway"] - t["payments-svc"]).total_seconds()
    assert 0.3 <= hop1 <= 1.5 and 0.3 <= hop2 <= 1.5


def test_gateway_usually_pages_first():
    first = []
    for seed in range(10):
        ev = simulate(100, [(60, "db_pool_exhaustion")], START, seed=seed)
        first.append(next(e for e in ev if e.kind == "alert").service)
    assert first.count("api-gateway") >= 6
    assert "postgres" not in first


def test_bad_deploy_change_then_spike():
    ev = simulate(120, [(60, "bad_deploy")], START, seed=2)
    dep = [e for e in ev if e.kind == "change"]
    assert len(dep) == 1 and dep[0].service == "payments-svc" and dep[0].attrs["version"] == "v2.3.1"
    assert dep[0].name == "deploy"
    gap = (datetime.fromisoformat(onset(ev, "payments-svc").replace("Z", "+00:00"))
           - datetime.fromisoformat(dep[0].ts.replace("Z", "+00:00"))).total_seconds()
    assert 5 <= gap <= 10.5
    assert onset(ev, "payments-svc") < onset(ev, "api-gateway")


def test_red_herring_deploy_30s_before_postgres():
    ev = simulate(150, [(30, "red_herring")], START, seed=4)
    dep = [e for e in ev if e.kind == "change"]
    assert len(dep) == 1 and dep[0].service == "ledger-svc"
    first_bad = bad(ev)[0]
    assert first_bad.service == "postgres"
    gap = (datetime.fromisoformat(first_bad.ts.replace("Z", "+00:00"))
           - datetime.fromisoformat(dep[0].ts.replace("Z", "+00:00"))).total_seconds()
    assert 29.5 <= gap <= 31


def test_psp_slow_latency_and_timeouts():
    ev = simulate(120, [(60, "psp_slow")], START, seed=6)
    lat = [e.value for e in ev if e.service == "psp-adapter" and e.name == "latency_p95_ms" and e.severity != "info"]
    assert lat and all(3500 <= v <= 4500 for v in lat)
    assert any("timed out" in (e.message or "") for e in ev if e.service == "psp-adapter")
    assert any(e.kind == "request" and e.attrs["status"] == 504 for e in ev)


def test_simulate_is_deterministic():
    a = simulate(90, [(30, "db_pool_exhaustion")], START, seed=11)
    b = simulate(90, [(30, "db_pool_exhaustion")], START, seed=11)
    c = simulate(90, [(30, "db_pool_exhaustion")], START, seed=12)
    assert [e.model_dump() for e in a] == [e.model_dump() for e in b]
    assert [e.model_dump() for e in a] != [e.model_dump() for e in c]


def test_events_validate_against_contract():
    ev = simulate(80, [(10, "red_herring"), (20, "psp_slow")], START, seed=1)
    for e in ev:
        Event.model_validate(e.model_dump())
    assert len({e.id for e in ev}) == len(ev)
