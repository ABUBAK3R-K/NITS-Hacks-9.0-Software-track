"""Engine against Lane A's real simulator. Skipped until lane-a-sim is merged
(app.sim.generator does not exist on lane-b-engine)."""
import pytest

from app.engine.run import analyze

gen = pytest.importorskip("app.sim.generator")

EXPECTED_ROOT = {
    "db_pool_exhaustion": "postgres",
    "bad_deploy": "payments-svc",
    "redis_down": "redis-session",
    "red_herring": "postgres",
    "psp_slow": "psp-adapter",
}


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
@pytest.mark.parametrize("fault", sorted(EXPECTED_ROOT))
def test_real_sim_fault(fault, seed, topology):
    evs = gen.simulate(150, [(60, fault)], seed=seed)
    act = [i for i in analyze(evs, topology) if i.status != "RESOLVED"]
    assert len(act) == 1, [(i.title, i.services) for i in act]
    inc = act[0]
    if fault == "red_herring":
        assert inc.status in ("OPEN", "ABSTAINED")
        assert not (inc.root_cause and inc.root_cause.service == "ledger-svc")
    else:
        assert inc.status == "OPEN"
    if inc.root_cause:
        assert inc.root_cause.service == EXPECTED_ROOT[fault]
    if fault == "redis_down":
        assert not {"payments-svc", "ledger-svc", "postgres"} & set(inc.services)
    if fault == "bad_deploy":
        assert inc.candidates[0].components.change > 0
    known = {e.id for e in inc.evidence_events}
    assert all(set(c.evidence) <= known for c in inc.candidates)


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_real_sim_steady_state(seed, topology):
    assert analyze(gen.simulate(300, [], seed=seed), topology) == []


def test_real_sim_incremental_ids_stable(topology):
    """Replay the store's call pattern (whole list, once per second) and check
    the incident id never changes once it appears."""
    evs = gen.simulate(110, [(60, "db_pool_exhaustion")], seed=4)
    seen_ids = set()
    for k in range(60, 111, 5):
        cut = [e for e in evs if e.ts < evs[0].ts[:11] + _hms(evs[0].ts, k)]
        seen_ids |= {i.id for i in analyze(cut, topology)}
    assert len(seen_ids) == 1


def _hms(ts0: str, add_s: int) -> str:
    h, m, s = int(ts0[11:13]), int(ts0[14:16]), float(ts0[17:23])
    tot = h * 3600 + m * 60 + s + add_s
    return f"{int(tot // 3600):02d}:{int(tot % 3600 // 60):02d}:{tot % 60:06.3f}Z"
