"""Every fixture validates against contracts/schemas.py, and every evidence id resolves."""
import json

from fastapi.testclient import TestClient

from app.main import app
from app.schemas import FIXTURES, Event, Incident, IncidentSummary, Report, Topology, TopologyState
from tests.conftest import load_fixture


def test_topology_fixture():
    t = Topology.model_validate(load_fixture("topology.json"))
    ids = {s.id for s in t.services}
    assert len(ids) == 7
    assert all(e.from_ in ids and e.to in ids for e in t.edges)


def test_event_fixtures():
    for name in ("events_db_pool.json", "events_ambiguous.json"):
        evs = [Event.model_validate(e) for e in load_fixture(name)]
        assert len({e.id for e in evs}) == len(evs)


def test_incident_fixtures_evidence_resolves():
    for name in ("incident_db_pool.json", "incident_ambiguous.json"):
        inc = Incident.model_validate(load_fixture(name))
        known = {e.id for e in inc.evidence_events}
        refs = [i for c in inc.candidates for i in c.evidence]
        refs += [i for t in inc.timeline for i in t.event_ids]
        refs += [i for r in inc.recommendations for i in r.evidence]
        if inc.root_cause:
            refs += inc.root_cause.evidence
        assert set(refs) <= known
        assert all(c.evidence for c in inc.candidates)


def test_statuses():
    assert load_fixture("incident_db_pool.json")["status"] == "OPEN"
    amb = Incident.model_validate(load_fixture("incident_ambiguous.json"))
    assert amb.status == "ABSTAINED" and amb.root_cause is None and amb.disambiguation


def test_list_and_report():
    [IncidentSummary.model_validate(x) for x in load_fixture("incidents_list.json")]
    rep = Report.model_validate(load_fixture("report_db_pool.json"))
    inc = Incident.model_validate(load_fixture("incident_db_pool.json"))
    known = {e.id for e in inc.evidence_events}
    assert all(set(s.cites) <= known for s in rep.sentences)


def test_sse_sample():
    raw = (FIXTURES / "sse_sample.txt").read_text(encoding="utf-8")
    models = {"event": Event, "incident_update": Incident, "topology_state": TopologyState}
    blocks = [b for b in raw.split("\n\n") if b.strip()]
    assert blocks
    for b in blocks:
        name_line, data_line = b.split("\n")
        name = name_line.removeprefix("event: ")
        models[name].model_validate(json.loads(data_line.removeprefix("data: ")))


def test_health():
    r = TestClient(app).get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_upgrade_fixtures():
    from app.schemas import EvalResults, NaiveAnswer, Run, RunReveal

    run = Run.model_validate(load_fixture("run_blind.json"))
    assert run.spec_hidden and run.spec is None and run.incidents
    rev = RunReveal.model_validate(load_fixture("run_reveal.json"))
    assert rev.expected_root == rev.spec.target
    assert rev.matched == (rev.our_top1 == rev.expected_root and not rev.abstained)
    NaiveAnswer.model_validate(load_fixture("naive_answer_db_pool.json"))
    ev = EvalResults.model_validate(load_fixture("eval_results.json"))
    assert ev.n == 0 and not ev.methods  # honest placeholder until eval/run_eval.py runs
    for name in ("alertmanager_webhook.json", "grafana_webhook.json"):
        body = load_fixture(name)
        assert body["alerts"] and all("labels" in a and "startsAt" in a for a in body["alerts"])


def test_ruled_out_reasons_reference_known_evidence():
    for name in ("incident_db_pool.json", "incident_ambiguous.json"):
        inc = Incident.model_validate(load_fixture(name))
        known = {e.id for e in inc.evidence_events}
        keep = 2 if inc.status == "ABSTAINED" else 1
        assert all(c.ruled_out_reason is None for c in inc.candidates[:keep])
        for c in inc.candidates[keep:]:
            assert c.ruled_out_reason and set(c.ruled_out_evidence) <= known
