"""Store: debounce, analyze failures keep incidents, SSE fan-out, topology_state."""
import asyncio
import json
from datetime import datetime, timezone

from app.schemas import FIXTURES, Incident, TopologyState
from app.sim.generator import simulate
from app.sim.topology import load_topology
from app.store import Store, build_topology_state
from tests.conftest import load_fixture

# Fixture incidents come from the real engine, so their id is an engine hash.
HERO_ID = load_fixture("incident_db_pool.json")["id"]


def fixture_incident():
    return Incident.model_validate(load_fixture("incident_db_pool.json"))


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_recompute_debounced_and_publishes():
    calls = []

    def fake(events, topo):
        calls.append(len(events))
        return [fixture_incident()]

    st = Store(analyze_fn=fake)
    q = st.subscribe()
    st.add_events(simulate(10, seed=1))
    assert st.recompute() is True
    assert st.recompute() is False  # not dirty
    st.add_events(simulate(10, seed=2, start=datetime(2026, 11, 9, 9, 0, tzinfo=timezone.utc)))
    assert st.recompute() is False  # dirty, but within 1 s of the last run
    assert len(calls) == 1
    names = [m[0] for m in drain(q)]
    assert "incident_update" in names and "topology_state" in names and "event" in names
    # an unchanged incident is not re-published
    st.recompute(force=True)
    assert [m[0] for m in drain(q)].count("incident_update") == 0


def test_analyze_failure_keeps_previous_incidents():
    state = {"fail": False}

    def flaky(events, topo):
        if state["fail"]:
            raise RuntimeError("boom")
        return [fixture_incident()]

    st = Store(analyze_fn=flaky)
    st.recompute(force=True)
    assert list(st.incidents) == [HERO_ID]
    state["fail"] = True
    st.recompute(force=True)
    assert list(st.incidents) == [HERO_ID] and "boom" in st.last_error


def test_default_analyze_failure_is_safe():
    st = Store()  # real app.engine.run.analyze (raises NotImplementedError until Lane B lands)
    st.add_events(simulate(5, seed=1))
    st.recompute(force=True)
    assert isinstance(st.incidents, dict)


def test_topology_state_matches_fixture():
    inc = fixture_incident()
    raw = (FIXTURES / "sse_sample.txt").read_text(encoding="utf-8")
    block = next(b for b in raw.split("\n\n") if b.startswith("event: topology_state"))
    want = TopologyState.model_validate(json.loads(block.split("\n")[1].removeprefix("data: ")))
    got = build_topology_state([inc], load_topology(), want.ts)
    assert got.model_dump() == want.model_dump()
    resolved = inc.model_copy(update={"status": "RESOLVED"})
    st2 = build_topology_state([resolved], load_topology(), want.ts)
    assert all(s.health == "healthy" for s in st2.services) and st2.hot_edges == []


def test_event_throttle_prefers_alerts():
    st = Store(analyze_fn=lambda e, t: [])
    q = st.subscribe()
    st.add_events(simulate(120, [(30, "db_pool_exhaustion")], seed=1))
    msgs = [m for m in drain(q) if m[0] == "event"]
    assert 0 < len(msgs) <= 20
    kinds = [json.loads(d)["kind"] for _, d in msgs]
    n_alerts = len([e for e in simulate(120, [(30, "db_pool_exhaustion")], seed=1) if e.kind == "alert"])
    assert kinds.count("alert") == min(n_alerts, 20)
    assert "info" not in [json.loads(d)["severity"] for _, d in msgs]


def test_duplicate_ids_rejected_and_count_cap():
    st = Store(analyze_fn=lambda e, t: [], max_events=100)
    ev = simulate(30, seed=1)
    acc, _ = st.add_events(ev[:50])
    acc2, errs2 = st.add_events(ev[:50])
    assert acc == 50 and acc2 == 0 and len(errs2) == 50
    st.add_events(ev[50:])
    assert len(st.events) == 100 and len(st.ids) == 100


def test_age_eviction_keeps_15_minutes():
    st = Store(analyze_fn=lambda e, t: [])
    st.add_events(simulate(1200, seed=1))  # 20 min from 08:30Z
    assert min(e.ts for e in st.events) >= "2026-11-09T08:34:59.000Z"


def test_sim_controls():
    st = Store(analyze_fn=lambda e, t: [])
    assert st.status().running is False
    st.inject("redis_down")  # starts a stopped sim
    s = st.status()
    assert s.running and s.active_faults == ["redis_down"] and s.events_in_store > 100
    st.inject("redis_down")  # duplicate injection is ignored
    assert st.status().active_faults == ["redis_down"]
    st.reset()
    s = st.status()
    assert s.running and s.active_faults == [] and not st.incidents


def test_recompute_async_runs_in_thread():
    st = Store(analyze_fn=lambda e, t: [fixture_incident()])
    st.add_events(simulate(5, seed=1))
    assert asyncio.run(st.recompute_async(force=True)) is True
    assert HERO_ID in st.incidents
