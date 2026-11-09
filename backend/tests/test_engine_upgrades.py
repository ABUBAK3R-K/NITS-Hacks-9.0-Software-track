"""Upgrades in the engine: U2 ruled-out reasons, noise, fix_id, lifecycle."""
from datetime import timedelta

import pytest

from app.engine.run import analyze
from app.schemas import FIX_IDS, Event
from app.sim.generator import DEFAULT_START, Simulator, simulate
from tests.scenarios import scenario
from tests.test_engine import SEEDS, active, check_invariants


def _check_why(inc):
    known = {e.id for e in inc.evidence_events}
    keep = 2 if inc.status == "ABSTAINED" else 1
    for i, c in enumerate(inc.candidates):
        if i < keep:
            assert c.ruled_out_reason is None and c.ruled_out_evidence == []
        else:
            assert c.ruled_out_reason and c.ruled_out_reason.startswith(c.service)
            assert "ruled out." in c.ruled_out_reason
            assert chr(0x2014) not in c.ruled_out_reason
            assert c.ruled_out_evidence and set(c.ruled_out_evidence) <= known


# ---------------------------------------------------------------- U2
@pytest.mark.parametrize("seed", SEEDS)
def test_red_herring_rules_out_decoy(seed, topology):
    inc = active(analyze(scenario("red_herring", seed=seed), topology))[0]
    check_invariants([inc])
    _check_why(inc)
    if inc.status == "OPEN":
        ledger = next(c for c in inc.candidates if c.service == "ledger-svc")
        r = ledger.ruled_out_reason
        assert "victim penalty" in r or "after postgres" in r, r
        assert "deploy v4.1.4" in r  # names the decoy change


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_real_sim_red_herring_reason(seed, topology):
    inc = active(analyze(simulate(150, [(60, "red_herring")], seed=seed), topology))[0]
    _check_why(inc)
    ledger = next(c for c in inc.candidates if c.service == "ledger-svc")
    assert "victim penalty" in ledger.ruled_out_reason or "after postgres" in ledger.ruled_out_reason


@pytest.mark.parametrize("kind", ["db_pool", "bad_deploy", "psp_slow", "ambiguous", "redis_down"])
def test_ruled_out_reasons_everywhere(kind, topology):
    for inc in analyze(scenario(kind, seed=2), topology):
        _check_why(inc)


def test_ambiguous_keeps_top_two(topology):
    inc = active(analyze(scenario("ambiguous", seed=0), topology))[0]
    assert inc.status == "ABSTAINED"
    assert inc.candidates[0].ruled_out_reason is None and inc.candidates[1].ruled_out_reason is None


# ---------------------------------------------------------------- noise
def test_noise_counts_incidents(topology):
    incs = analyze(scenario("db_pool", seed=1), topology)
    assert len(incs) == 1
    n = incs[0].noise
    assert n.alerts == incs[0].alert_count and n.incidents == 1 and n.ratio == round(n.alerts / 1, 1)
    two = analyze(simulate(200, [(60, "db_pool_exhaustion"), (60, "redis_down")], seed=3), topology)
    for inc in two:
        assert inc.noise.incidents == len(two)
        assert inc.noise.ratio == round(inc.alert_count / len(two), 1)


# ---------------------------------------------------------------- fix_id
@pytest.mark.parametrize("fault,fix", [("db_pool_exhaustion", "raise_pool_postgres"),
                                       ("bad_deploy", "rollback_deploy"),
                                       ("psp_slow", "circuit_breaker_psp"),
                                       ("redis_down", "restart_service")])
def test_fix_id(fault, fix, topology):
    inc = active(analyze(simulate(150, [(60, fault)], seed=1), topology))[0]
    assert inc.status == "OPEN"
    ids = [r.fix_id for r in inc.recommendations]
    assert ids[0] == fix
    assert all(x is None or x in FIX_IDS for x in ids)
    assert ids.count(fix) == 1


def test_no_fix_id_when_abstained(topology):
    inc = active(analyze(scenario("ambiguous", seed=0), topology))[0]
    assert all(r.fix_id is None for r in inc.recommendations)


# ---------------------------------------------------------------- lifecycle
def _fix_event(svc: str, t, n: int) -> Event:
    ts = t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"
    return Event(id=f"evt_fix_{n}", ts=ts, source="sim", service=svc, kind="change", severity="info",
                 name="fix_applied", message="fix applied: raise_pool_postgres",
                 attrs={"fix_id": "raise_pool_postgres"})


def test_lifecycle_spreading_then_cause(topology):
    evs = simulate(150, [(60, "db_pool_exhaustion")], seed=2)
    # just after formation the set is still growing
    t_form = DEFAULT_START + timedelta(seconds=66)
    early = [e for e in evs if e.ts < t_form.strftime("%Y-%m-%dT%H:%M:%S.000Z")]
    incs = analyze(early, topology)
    assert incs and incs[0].lifecycle in ("SPREADING", "DETECTED")
    inc = analyze(evs, topology)[0]
    states = [h.state for h in inc.lifecycle_history]
    assert states[0] == "DETECTED" and "SPREADING" in states
    assert inc.lifecycle == "CAUSE_IDENTIFIED" and states[-1] == "CAUSE_IDENTIFIED"
    tss = [h.ts for h in inc.lifecycle_history]
    assert tss == sorted(tss)
    assert inc.mttd_s is None and inc.mttr_s is None


def test_lifecycle_abstained_never_cause_identified(topology):
    inc = active(analyze(scenario("ambiguous", seed=0), topology))[0]
    assert "CAUSE_IDENTIFIED" not in [h.state for h in inc.lifecycle_history]


def test_lifecycle_mitigating_then_resolved(topology):
    sim = Simulator(DEFAULT_START, seed=5)
    t = DEFAULT_START
    evs = sim.advance(t + timedelta(seconds=60))
    sim.inject("db_pool_exhaustion", t + timedelta(seconds=60))
    evs += sim.advance(t + timedelta(seconds=90))
    fix_at = t + timedelta(seconds=90)
    sim.clear_faults()
    fix = _fix_event("postgres", fix_at, 1)
    mid = analyze(evs + [fix] + sim.advance(fix_at + timedelta(seconds=5)), topology)
    assert mid[0].lifecycle == "MITIGATING"
    # fix_applied is a remediation, never a deploy: no change component on postgres
    pg = next(c for c in mid[0].candidates if c.service == "postgres")
    assert pg.components.change == 0 and fix.id not in pg.evidence
    assert mid[0].root_cause.signature == "connection_pool_exhausted"
    rest = sim.advance(fix_at + timedelta(seconds=40))
    inc = analyze(evs + [fix] + rest, topology)[0]
    states = [h.state for h in inc.lifecycle_history]
    assert inc.lifecycle == "RESOLVED"
    assert states.index("MITIGATING") < states.index("RESOLVED")
    check_invariants([inc])


def test_fix_applied_is_not_a_deploy(topology):
    """A fix_applied change before the onset must not create a change component."""
    evs = scenario("db_pool", seed=1)
    from tests.scenarios import T0
    fix = _fix_event("postgres", T0 + timedelta(seconds=30), 2)
    inc = analyze(evs + [fix], topology)[0]
    pg = next(c for c in inc.candidates if c.service == "postgres")
    assert pg.components.change == 0
    assert inc.lifecycle != "MITIGATING"  # it came before opened_at
