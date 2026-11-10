"""Incident similarity ("seen this before"): formula, ranking, exclusion, history survives reset."""
from app.schemas import FaultSpec, Incident
from app.similar import IncidentHistory, PastIncident, similarity
from app.store import Store
from tests.conftest import load_fixture


def base() -> Incident:
    return Incident.model_validate(load_fixture("incident_db_pool.json"))


def variant(iid: str, root: str | None, signature: str | None, services: list[str],
            opened_at: str = "2026-11-09T08:00:00.000Z") -> Incident:
    inc = base()
    rc = None
    if root is not None:
        rc = inc.root_cause.model_copy(update={"service": root, "signature": signature})
    return inc.model_copy(update={"id": iid, "root_cause": rc, "services": services, "opened_at": opened_at})


def test_formula():
    a = PastIncident.of(variant("a", "postgres", "pool_exhaustion", ["postgres", "auth-svc"]))
    b = PastIncident.of(variant("b", "postgres", "pool_exhaustion", ["postgres", "auth-svc", "api-gateway"]))
    assert similarity(a, b) == round(0.5 + 0.3 + 0.2 * 2 / 3, 2)
    c = PastIncident.of(variant("c", "redis-session", "down", ["redis-session"]))
    assert similarity(a, c) == 0.0


def test_matching_root_and_signature_ranks_first_and_unrelated_excluded():
    cur = base()
    root, sig = cur.root_cause.service, cur.root_cause.signature
    h = IncidentHistory()
    h.record("live0", variant("inc_same_root", root, "other_sig", cur.services))         # 0.7
    h.record("live0", variant("inc_best", root, sig, cur.services))                     # 1.0
    h.record("live0", variant("inc_unrelated", "redis-session", "down", ["redis-session"]))
    h.record("run_x", variant("inc_partial", root, None, [root]))                        # 0.5 + small
    out = h.similar_to("live1", cur)
    ids = [s.incident_id for s in out]
    assert ids[0] == "inc_best" and out[0].similarity == 1.0
    assert ids[1] == "inc_same_root"
    assert "inc_unrelated" not in ids
    assert len(out) <= 3 and all(s.similarity >= 0.5 for s in out)
    assert [s.similarity for s in out] == sorted((s.similarity for s in out), reverse=True)


def test_never_compared_with_itself_and_bounded():
    cur = base()
    h = IncidentHistory(max_items=50)
    h.record("live0", cur)
    assert h.similar_to("live0", cur) == []  # same namespace + id is itself
    assert [s.incident_id for s in h.similar_to("live1", cur)] == [cur.id]  # same id after a reset is a past one
    for i in range(80):
        h.record("run", variant(f"inc_{i}", "postgres", "x", ["postgres"]))
    assert len(h) == 50


def test_history_survives_reset_and_fills_similar():
    inc = base()
    st = Store(analyze_fn=lambda e, t: [inc], fault_ttl_s=0, idle_reset_s=0)
    st.recompute(force=True)
    assert st.incidents[inc.id].similar == []
    st.reset()
    assert len(st.history) == 1
    st.recompute(force=True)
    sim = st.incidents[inc.id].similar
    assert len(sim) == 1 and sim[0].incident_id == inc.id and sim[0].similarity == 1.0
    assert sim[0].root_service == inc.root_cause.service


def test_sandbox_run_incidents_join_history():
    st = Store(fault_ttl_s=0, idle_reset_s=0)
    spec = FaultSpec(target="redis-session", kind="down", seed=3)
    first = st.runs.create(spec, "designed")
    assert first.incidents and all(i.similar == [] for i in first.incidents)
    second = st.runs.create(spec.model_copy(update={"seed": 4}), "designed")
    top = second.incidents[0].similar
    assert top and top[0].incident_id == first.incidents[0].id and top[0].similarity >= 0.8
