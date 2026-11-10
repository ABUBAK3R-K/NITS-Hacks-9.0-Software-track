"""Live Chaos Lab hygiene: faults expire after FAULT_TTL_S, idle live store auto-resets.
Uses an injectable clock and explicit tick times; nothing sleeps."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import FaultSpec
from app.store import Store, store

T0 = datetime(2026, 11, 10, 6, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += timedelta(seconds=s)


def _no_engine(events, topo):
    return []


def test_fault_expires_after_ttl_without_fix_applied():
    clk = Clock()
    st = Store(analyze_fn=_no_engine, clock=clk, fault_ttl_s=180, idle_reset_s=0)
    st.start()
    st.inject("redis_down")
    f = st.sim.faults[-1]
    st.tick(f.start + timedelta(seconds=179))
    assert st.status().active_faults == ["redis_down"]
    st.tick(f.start + timedelta(seconds=181))
    assert st.status().active_faults == []
    assert f.expired and f.fix_id is None and f.fixed_at == f.start + timedelta(seconds=180)
    st.tick(f.start + timedelta(seconds=260))
    evs = st.snapshot()
    assert not any(e.name == "fix_applied" for e in evs)
    # recovered: redis-session metrics are back at info after the heal window
    late = [e for e in evs if e.service == "redis-session" and e.kind == "metric"
            and e.ts >= (f.start + timedelta(seconds=200)).isoformat().replace("+00:00", "Z")]
    assert late and all(e.severity == "info" for e in late)
    # a fresh fault of the same type can be injected again
    st.inject("redis_down")
    assert st.status().active_faults == ["redis_down"]


def test_expired_fault_resolves_in_engine_without_mitigating():
    """The real engine sees the recovery (RESOLVED) but never a fix (no MITIGATING)."""
    clk = Clock()
    st = Store(clock=clk, fault_ttl_s=40, idle_reset_s=0)
    st.start()
    st.inject("redis_down")
    t0 = st.sim.t
    seen = None
    for k in range(5, 200, 5):
        st.tick(t0 + timedelta(seconds=k))
        st.recompute(force=True)
        for inc in st.incidents.values():
            seen = inc
        if seen and (seen.status == "RESOLVED" or seen.lifecycle == "RESOLVED"):
            break
    assert seen is not None and (seen.status == "RESOLVED" or seen.lifecycle == "RESOLVED")
    assert "MITIGATING" not in [h.state for h in seen.lifecycle_history]


def test_idle_reset_after_no_api_requests():
    clk = Clock()
    st = Store(analyze_fn=_no_engine, clock=clk, fault_ttl_s=0, idle_reset_s=600)
    st.start()
    st.inject("bad_deploy")
    clk.advance(599)
    assert st.maybe_idle_reset() is False
    st.touch()
    clk.advance(599)
    assert st.maybe_idle_reset() is False and st.status().active_faults == ["bad_deploy"]
    clk.advance(2)
    assert st.maybe_idle_reset() is True
    assert st.status().active_faults == [] and st.running  # fresh baseline, still running
    clk.advance(700)
    assert st.maybe_idle_reset() is False  # nothing left to clear


def test_sandbox_runs_do_not_expire():
    clk = Clock()
    st = Store(analyze_fn=_no_engine, clock=clk, fault_ttl_s=1, idle_reset_s=0)
    st.start()
    run = st.runs.create(FaultSpec(target="redis-session", kind="down", seed=3), "designed")
    clk.advance(3600)
    st.tick(clk())
    rec = st.runs.get(run.run_id)
    assert rec.fault.fixed_at is None and not rec.fault.expired


def test_api_requests_touch_the_live_store():
    with TestClient(app) as c:
        store._last_touch = T0 - timedelta(days=1)
        assert c.get("/health").status_code == 200
        assert store._last_touch == T0 - timedelta(days=1)  # not an /api route
        assert c.get("/api/topology").status_code == 200
        assert store._last_touch > T0 - timedelta(days=1)
