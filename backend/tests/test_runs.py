"""U1: fault primitives, offline runner, sandbox runs (real engine, no server)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.schemas import FaultSpec, Decoy, Incident
from app.sim.faults import (
    AFFECTED, ALL_KINDS, APP_SERVICES, PRESETS, affected_services, valid_kinds, validate_spec,
)
from app.sim.generator import simulate
from app.sim.runner import blind_specs_for_eval, random_blind_spec, simulate_spec
from app.sim.topology import callers_closure, service_ids
from app.store import RunStore, Store
from tests.conftest import load_fixture

START = datetime(2026, 11, 9, 8, 30, 0, tzinfo=timezone.utc)


def bad(events):
    return [e for e in events if e.severity != "info"]


def dump(events):
    return [e.model_dump() for e in events]


# ---------------------------------------------------------------- primitives
def test_valid_kinds_rules():
    assert "pool_exhaustion" in valid_kinds("postgres")
    for s in service_ids():
        ks = valid_kinds(s)
        assert {"latency", "error_spike", "down"} <= set(ks)
        assert ("pool_exhaustion" in ks) == (s == "postgres")
        assert ("bad_deploy" in ks) == (s in APP_SERVICES)


@pytest.mark.parametrize("ftype", sorted(PRESETS))
def test_presets_match_old_fault_affected_sets(ftype):
    spec = PRESETS[ftype]
    assert affected_services(spec.target, spec.kind) == AFFECTED[ftype]


def test_preset_specs():
    assert PRESETS["db_pool_exhaustion"] == FaultSpec(target="postgres", kind="pool_exhaustion")
    assert PRESETS["red_herring"].decoy == Decoy(service="ledger-svc", offset_s=-30.0)
    assert (PRESETS["psp_slow"].target, PRESETS["psp_slow"].kind) == ("psp-adapter", "latency")
    assert (PRESETS["redis_down"].target, PRESETS["redis_down"].kind) == ("redis-session", "down")
    assert (PRESETS["bad_deploy"].target, PRESETS["bad_deploy"].kind) == ("payments-svc", "bad_deploy")


@pytest.mark.parametrize("target", service_ids())
def test_every_valid_primitive_degrades_target_and_callers_only(target):
    for kind in valid_kinds(target):
        spec = FaultSpec(target=target, kind=kind, seed=3)
        ev, at = simulate_spec(spec, baseline_s=30, after_s=60, start=START)
        touched = {e.service for e in bad(ev)}
        assert touched == {target} | callers_closure(target), (target, kind)
        first = min(bad(ev), key=lambda e: e.ts)
        assert first.service == target  # root degrades first (propagation along CALLERS)
        late = [e for e in bad(ev) if e.kind == "metric" and e.ts >= "2026-11-09T08:31:20.000Z"]
        assert {e.service for e in late} == touched  # sustained


def test_invalid_kind_rejected():
    with pytest.raises(ValueError):
        validate_spec(FaultSpec(target="redis-session", kind="bad_deploy"))
    with pytest.raises(ValueError):
        validate_spec(FaultSpec(target="auth-svc", kind="pool_exhaustion"))
    with pytest.raises(ValueError):
        validate_spec(FaultSpec(target="postgres", kind="down", decoy=Decoy(service="postgres", offset_s=0)))
    validate_spec(FaultSpec(target="ledger-svc", kind="bad_deploy", decoy=Decoy(service="psp-adapter", offset_s=-60)))


# ---------------------------------------------------------------- runner
def test_simulate_spec_deterministic_per_seed():
    spec = FaultSpec(target="postgres", kind="pool_exhaustion", onset_jitter_s=7.5,
                     decoy=Decoy(service="ledger-svc", offset_s=-20), seed=5)
    a, ta = simulate_spec(spec, start=START)
    b, tb = simulate_spec(spec, start=START)
    c, _ = simulate_spec(spec.model_copy(update={"seed": 6}), start=START)
    assert dump(a) == dump(b) and ta == tb
    assert dump(a) != dump(c)
    assert [e.ts for e in a] == sorted(e.ts for e in a)
    assert (ta - START).total_seconds() == pytest.approx(67.5)
    assert not [e for e in bad(a) if e.ts < "2026-11-09T08:31:07.500Z"]


def test_random_blind_spec_valid_and_deterministic_over_200_seeds():
    decoys = 0
    for seed in range(200):
        s = random_blind_spec(seed)
        assert s == random_blind_spec(seed)
        assert s.seed == seed
        assert s.target in service_ids()
        assert s.kind in valid_kinds(s.target)
        assert 0 <= s.onset_jitter_s <= 20
        validate_spec(s)
        if s.decoy:
            decoys += 1
            assert s.decoy.service != s.target
            assert -60 <= s.decoy.offset_s <= 30
    assert 70 <= decoys <= 130
    assert {random_blind_spec(s).kind for s in range(200)} == set(ALL_KINDS)


def test_blind_specs_for_eval_twenty_per_kind_interleaved():
    specs = blind_specs_for_eval()
    assert len(specs) == 100 and [s.seed for s in specs] == list(range(100))
    for k in ALL_KINDS:
        assert sum(s.kind == k for s in specs) == 20
        assert sum(s.kind == k for s in specs[50:]) == 10  # a 50/50 split has every kind
    assert all(s.kind in valid_kinds(s.target) for s in specs)
    assert [s.model_dump() for s in specs] == [s.model_dump() for s in blind_specs_for_eval()]


def test_decoy_is_one_change_and_its_service_stays_healthy():
    spec = FaultSpec(target="postgres", kind="pool_exhaustion",
                     decoy=Decoy(service="psp-adapter", offset_s=-45), seed=2)
    ev, at = simulate_spec(spec, start=START)
    dep = [e for e in ev if e.kind == "change"]
    assert len(dep) == 1 and dep[0].service == "psp-adapter" and dep[0].name == "deploy"
    assert dep[0].attrs["version"]
    assert not [e for e in bad(ev) if e.service == "psp-adapter"]
    root_onset = min(e.ts for e in bad(ev) if e.service == "postgres")
    gap = (datetime.fromisoformat(root_onset.replace("Z", "+00:00"))
           - datetime.fromisoformat(dep[0].ts.replace("Z", "+00:00"))).total_seconds()
    assert 44.5 <= gap <= 45.5


def test_live_presets_unchanged_red_herring_timing():
    # the live Chaos Lab path goes through PRESETS too; decoy may not be written in the past
    ev = simulate(150, [(30, "red_herring")], START, seed=4)
    dep = [e for e in ev if e.kind == "change"]
    assert len(dep) == 1 and dep[0].service == "ledger-svc" and dep[0].attrs["version"] == "v4.1.8"
    assert dep[0].ts == "2026-11-09T08:30:30.000Z"


# ---------------------------------------------------------------- sandbox runs
def test_run_isolation_and_reveal():
    st = Store()
    st.start()
    live_before = [e.id for e in st.snapshot()]
    inc_before = dict(st.incidents)
    spec = PRESETS["db_pool_exhaustion"].model_copy(update={"seed": 1})
    r1 = st.runs.create(spec, "blind")
    r2 = st.runs.create(FaultSpec(target="redis-session", kind="down", seed=2), "designed")
    assert [e.id for e in st.snapshot()] == live_before and st.incidents == inc_before
    assert r1.spec_hidden and r1.spec is None and r1.mode == "blind"
    assert not r2.spec_hidden and r2.spec.target == "redis-session"
    assert r1.incidents and r1.incidents[0].root_cause.service == "postgres"
    assert r1.incidents[0].mttd_s is not None and 0 < r1.incidents[0].mttd_s < 30
    # each run owns its own events
    e1, e2 = st.runs.get(r1.run_id).events, st.runs.get(r2.run_id).events
    assert {e.service for e in bad(e1)} == AFFECTED["db_pool_exhaustion"]
    assert {e.service for e in bad(e2)} == AFFECTED["redis_down"]
    rv = st.runs.reveal(r1.run_id)
    assert rv.spec == spec and rv.expected_root == "postgres"
    assert rv.our_top1 == "postgres" and not rv.abstained and rv.matched
    assert st.runs.get(r1.run_id).run.spec_hidden is False
    # lookups across live and sandbox
    iid = r2.incidents[0].id
    assert st.get_incident(iid) == r2.incidents[0]
    evs = st.events_for_incident(iid)
    assert evs and {e.id for e in evs} <= {e.id for e in e2}
    assert st.get_incident("inc_nope") is None and st.events_for_incident("inc_nope") == []


def test_reveal_mismatch_and_abstain():
    inc = Incident.model_validate(load_fixture("incident_ambiguous.json"))
    rs = RunStore(lambda ev, topo: [inc], Store().topology)
    run = rs.create(FaultSpec(target="ledger-svc", kind="error_spike", seed=1), "blind")
    rv = rs.reveal(run.run_id)
    assert rv.abstained and not rv.matched and rv.our_top1 == inc.candidates[0].service


def test_run_store_lru_cap():
    rs = RunStore(lambda ev, topo: [], Store().topology, max_runs=3)
    ids = [rs.create(FaultSpec(target="postgres", kind="down", seed=i), "designed").run_id for i in range(5)]
    assert len(rs) == 3 and rs.get(ids[0]) is None and rs.get(ids[4]) is not None


def test_live_mttd_filled_by_store():
    st = Store()
    st.start()
    st.recompute(force=True)
    assert not st.incidents
    st.inject("db_pool_exhaustion")
    now = st.sim.t
    for k in range(1, 40):
        st.tick(now + timedelta(seconds=k))
        st.recompute(force=True)
        if st.incidents:
            break
    inc = next(iter(st.incidents.values()))
    assert inc.mttd_s is not None and 0 < inc.mttd_s <= k + 1
