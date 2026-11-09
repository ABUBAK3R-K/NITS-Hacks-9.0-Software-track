"""Engine tests: fixtures, synthetic scenarios, determinism, evidence closure, perf."""
import json
import random
import time

import pytest

from app.engine.run import analyze
from app.schemas import Incident
from tests.scenarios import scenario

SEEDS = [0, 1, 2, 3, 4, 5, 6, 7, 11, 23]


def active(incs):
    return [i for i in incs if i.status != "RESOLVED"]


def referenced_ids(inc: Incident) -> set[str]:
    ids = set()
    if inc.root_cause:
        ids |= set(inc.root_cause.evidence)
    for c in inc.candidates:
        ids |= set(c.evidence)
        ce = c.component_evidence
        ids |= set(ce.upstream) | set(ce.precedence) | set(ce.change) | set(ce.severity)
    for t in inc.timeline:
        ids |= set(t.event_ids)
    for r in inc.recommendations:
        ids |= set(r.evidence)
    return ids


def check_invariants(incs):
    for inc in incs:
        Incident.model_validate(inc.model_dump(mode="json", by_alias=True))
        known = {e.id for e in inc.evidence_events}
        assert referenced_ids(inc) <= known
        assert all(c.evidence for c in inc.candidates)
        scores = [c.score for c in inc.candidates]
        assert scores == sorted(scores, reverse=True)
        for c in inc.candidates:
            comp = c.components
            assert comp.upstream <= 0.4 and comp.precedence <= 0.3 and comp.change <= 0.2 and comp.severity <= 0.1
            assert abs(c.score - round((comp.upstream + comp.precedence + comp.change + comp.severity) * c.penalty,
                                       3)) < 1e-9
            assert (c.penalty < 1.0) == (c.victim_of is not None)
        if inc.status == "OPEN":
            assert inc.root_cause and inc.root_cause.service == inc.candidates[0].service
        if inc.status == "ABSTAINED":
            assert inc.root_cause is None and inc.abstain_reason and inc.disambiguation
        tss = [t.ts for t in inc.timeline]
        assert tss == sorted(tss)
        assert chr(0x2014) not in inc.model_dump_json()  # no em dashes in user-facing text


# ---------------------------------------------------------------- fixtures
def test_fixture_db_pool(db_pool_events, topology):
    incs = analyze(db_pool_events, topology)
    check_invariants(incs)
    act = active(incs)
    assert len(act) == 1
    inc = act[0]
    assert inc.status == "OPEN"
    assert inc.root_cause.service == "postgres"
    assert inc.root_cause.signature == "connection_pool_exhausted"
    assert inc.alert_count == 14
    assert set(inc.services) == {"postgres", "auth-svc", "ledger-svc", "payments-svc", "api-gateway"}
    assert inc.candidates[0].service == "postgres"
    assert inc.candidates[0].score - inc.candidates[1].score >= 0.3
    assert inc.title == "postgres: connection_pool_exhausted"
    assert inc.severity == "critical"
    assert inc.impact.failed_payments > 0 and inc.impact.users_affected > 0
    assert inc.timeline[0].service == "postgres"
    assert any("pool" in r.action.lower() for r in inc.recommendations)


def test_fixture_ambiguous(ambiguous_events, topology):
    incs = analyze(ambiguous_events, topology)
    check_invariants(incs)
    act = active(incs)
    assert len(act) == 1
    inc = act[0]
    assert inc.status == "ABSTAINED"
    assert inc.root_cause is None
    assert {c.service for c in inc.candidates[:2]} == {"ledger-svc", "postgres"}
    assert inc.disambiguation
    assert any("version" in d for d in inc.disambiguation)
    assert "(undecided)" in inc.title
    # cheap reversible check first: canary rollback of the deployed service
    assert "Roll back" in inc.recommendations[0].action and "ledger-svc" in inc.recommendations[0].action


# ---------------------------------------------------------------- synthetic scenarios
@pytest.mark.parametrize("seed", SEEDS)
def test_db_pool(seed, topology):
    incs = analyze(scenario("db_pool", seed=seed), topology)
    check_invariants(incs)
    act = active(incs)
    assert len(act) == 1
    assert act[0].status == "OPEN" and act[0].root_cause.service == "postgres"
    assert act[0].root_cause.signature == "connection_pool_exhausted"


@pytest.mark.parametrize("seed", SEEDS)
def test_bad_deploy(seed, topology):
    incs = analyze(scenario("bad_deploy", seed=seed), topology)
    check_invariants(incs)
    act = active(incs)
    assert len(act) == 1
    inc = act[0]
    assert inc.status == "OPEN" and inc.root_cause.service == "payments-svc"
    top = inc.candidates[0]
    assert top.components.change > 0 and top.component_evidence.change
    assert inc.root_cause.signature == "error_spike_after_deploy"
    assert any("v2.3.0" in r.action for r in inc.recommendations)


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("variant", ["", "silent"])
def test_redis_down_does_not_overgroup(seed, variant, topology):
    incs = analyze(scenario("redis_down", seed=seed, variant=variant), topology)
    check_invariants(incs)
    act = active(incs)
    assert len(act) == 1
    inc = act[0]
    assert inc.root_cause is not None and inc.root_cause.service == "redis-session"
    assert not {"payments-svc", "ledger-svc", "postgres"} & set(inc.services)
    assert len(inc.services) <= 3
    if variant == "silent":
        assert inc.root_cause.signature == "dependency_down"


@pytest.mark.parametrize("seed", SEEDS)
def test_red_herring_never_blames_ledger(seed, topology):
    incs = analyze(scenario("red_herring", seed=seed), topology)
    check_invariants(incs)
    act = active(incs)
    assert len(act) == 1
    inc = act[0]
    assert inc.status in ("OPEN", "ABSTAINED")
    if inc.status == "OPEN":
        assert inc.root_cause.service == "postgres"
    assert not (inc.root_cause and inc.root_cause.service == "ledger-svc")
    ledger = next(c for c in inc.candidates if c.service == "ledger-svc")
    assert ledger.components.change > 0  # the deploy is seen, it just does not win


@pytest.mark.parametrize("seed", SEEDS)
def test_psp_slow(seed, topology):
    incs = analyze(scenario("psp_slow", seed=seed), topology)
    check_invariants(incs)
    act = active(incs)
    assert len(act) == 1
    assert act[0].status == "OPEN" and act[0].root_cause.service == "psp-adapter"
    assert any("circuit breaker" in r.action.lower() or "fail over" in r.action.lower()
               for r in act[0].recommendations)


@pytest.mark.parametrize("seed", SEEDS)
def test_ambiguous_abstains(seed, topology):
    incs = analyze(scenario("ambiguous", seed=seed), topology)
    check_invariants(incs)
    act = active(incs)
    assert len(act) == 1
    assert act[0].status == "ABSTAINED"
    assert {c.service for c in act[0].candidates[:2]} == {"ledger-svc", "postgres"}


@pytest.mark.parametrize("seed", SEEDS)
def test_steady_state_no_false_positives(seed, topology):
    incs = analyze(scenario(None, seed=seed, baseline_s=60, after_s=60), topology)
    assert incs == []


def test_empty_input(topology):
    assert analyze([], topology) == []


# ---------------------------------------------------------------- lifecycle
def test_resolved_after_recovery(topology):
    """Fault data followed by 2 min of nothing anomalous: the incident is RESOLVED,
    judged against the latest event ts, not the wall clock."""
    evs = scenario("db_pool", seed=3, baseline_s=40, after_s=20)
    steady = scenario(None, seed=4, baseline_s=0, after_s=200)
    # shift the steady stream after the fault, renaming ids so they stay unique
    from datetime import datetime, timedelta
    shifted = []
    end = max(datetime.fromisoformat(e.ts.replace("Z", "+00:00")) for e in evs)
    for e in steady:
        if e.kind == "change":
            continue
        t = datetime.fromisoformat(e.ts.replace("Z", "+00:00")) - datetime.fromisoformat(
            steady[0].ts.replace("Z", "+00:00")) + end + timedelta(seconds=1)
        d = e.model_dump()
        d["id"] = "evt_s" + e.id[4:]
        d["ts"] = t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"
        shifted.append(type(e).model_validate(d))
    incs = analyze(evs + shifted, topology)
    check_invariants(incs)
    assert incs and all(i.status == "RESOLVED" for i in incs)
    assert incs[0].candidates[0].service == "postgres"


def test_ids_stable_as_stream_grows(topology):
    """The store re-runs analyze on a growing list; incident ids must not change."""
    evs = scenario("db_pool", seed=5)  # fault at 40 s of an 80 s stream
    full = analyze(evs, topology)
    assert len(full) == 1
    for frac in (0.6, 0.75, 0.9):
        part = analyze(evs[: int(len(evs) * frac)], topology)
        assert [i.id for i in part] == [i.id for i in full]
        assert part[0].opened_at == full[0].opened_at


# ---------------------------------------------------------------- determinism + perf
@pytest.mark.parametrize("kind", ["db_pool", "red_herring", "ambiguous"])
def test_shuffled_input_identical_output(kind, topology):
    evs = scenario(kind, seed=9)
    a = [i.model_dump_json(by_alias=True) for i in analyze(evs, topology)]
    shuffled = list(evs)
    random.Random(1).shuffle(shuffled)
    b = [i.model_dump_json(by_alias=True) for i in analyze(shuffled, topology)]
    assert a == b
    # duplicates (store re-sends) change nothing either
    c = [i.model_dump_json(by_alias=True) for i in analyze(evs + evs[:50], topology)]
    assert a == c


def test_fixture_shuffled_identical(db_pool_events, topology):
    a = json.dumps([i.model_dump(mode="json", by_alias=True) for i in analyze(db_pool_events, topology)])
    rev = list(reversed(db_pool_events))
    b = json.dumps([i.model_dump(mode="json", by_alias=True) for i in analyze(rev, topology)])
    assert a == b


def test_perf_20k_events(topology):
    evs = scenario("db_pool", seed=2, baseline_s=500, after_s=100, rate=26.0)
    assert len(evs) >= 20000
    evs = evs[-20000:]
    analyze(evs, topology)  # warm
    t = time.perf_counter()
    incs = analyze(evs, topology)
    dt = time.perf_counter() - t
    assert dt < 1.0, f"analyze took {dt:.3f} s on 20k events"
    assert any(i.root_cause and i.root_cause.service == "postgres" for i in incs)


# ---------------------------------------------------------------- uploads (logs only, unknown services)
def _log(n, sec, svc, sev, msg):
    from app.schemas import Event
    return Event.model_validate({"id": f"evt_up{n:04d}", "ts": f"2026-11-09T10:00:{sec:06.3f}Z", "source": "upload",
                                 "service": svc, "kind": "log", "severity": sev, "name": "log", "message": msg})


def test_upload_unknown_services_are_isolated(topology):
    evs = []
    for k in range(4):
        evs.append(_log(k, 1.0 + 3 * k, "checkout-svc", "error", "checkout failed: cart service 500"))
        evs.append(_log(10 + k, 1.5 + 3 * k, "fraud-svc", "error", "fraud score timeout"))
    evs.append(_log(99, 30.0, "checkout-svc", "info", "heartbeat"))
    incs = analyze(evs, topology)
    check_invariants(incs)
    assert sorted(i.services[0] for i in incs) == ["checkout-svc", "fraud-svc"]
    for inc in incs:
        assert len(inc.services) == 1 and inc.candidates[0].service == inc.services[0]
        if inc.status == "ABSTAINED":
            assert any("not in the topology" in d for d in inc.disambiguation)


def test_two_stray_error_logs_do_not_page(topology):
    evs = [_log(1, 1.0, "auth-svc", "error", "transient"), _log(2, 4.0, "auth-svc", "error", "transient"),
           _log(3, 40.0, "auth-svc", "info", "ok")]
    assert analyze(evs, topology) == []


def test_upload_style_db_pool_logs(topology):
    lines = [(3.6, "postgres", "critical", "FATAL: remaining connection slots are reserved"),
             (4.4, "auth-svc", "error", "login failed: could not acquire DB connection"),
             (4.9, "ledger-svc", "error", "ledger write failed: timeout acquiring connection from pool"),
             (5.6, "payments-svc", "error", "POST /v1/payments -> ledger-svc 503"),
             (6.5, "api-gateway", "error", "upstream timeout: 504 on POST /v1/payments"),
             (9.1, "auth-svc", "error", "login failed: could not acquire DB connection"),
             (10.9, "postgres", "error", "FATAL: sorry, too many clients already"),
             (13.5, "api-gateway", "error", "upstream timeout: 504 on POST /v1/payments"),
             (14.9, "ledger-svc", "error", "ledger write failed: timeout acquiring connection from pool"),
             (15.5, "payments-svc", "error", "POST /v1/payments -> ledger-svc 503"),
             (16.0, "auth-svc", "error", "HikariPool-1 - Connection is not available"),
             (17.0, "ledger-svc", "error", "QueuePool limit of size 20 overflow 10 reached"),
             (18.0, "payments-svc", "error", "payment intent rolled back: ledger commit failed"),
             (19.0, "api-gateway", "critical", "SLO burn rate 14.2x on checkout availability")]
    evs = [_log(n, s, svc, sev, m) for n, (s, svc, sev, m) in enumerate(lines)]
    incs = analyze(evs, topology)
    check_invariants(incs)
    assert len(incs) == 1
    assert incs[0].status == "OPEN" and incs[0].root_cause.service == "postgres"
    assert incs[0].root_cause.signature == "connection_pool_exhausted"
