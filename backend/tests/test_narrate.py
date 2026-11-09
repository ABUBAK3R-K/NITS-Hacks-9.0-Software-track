"""Narrator tests. No real network: httpx.MockTransport stands in for Groq and Gemini."""
import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.narrate import grounding, llm
from app.narrate.grounding import ground
from app.narrate.llm import narrate
from app.narrate.router import make_router
from app.schemas import Incident
from tests.conftest import load_fixture

EM = "\u2014"


@pytest.fixture
def hero() -> Incident:
    return Incident.model_validate(load_fixture("incident_db_pool.json"))


@pytest.fixture
def amb() -> Incident:
    return Incident.model_validate(load_fixture("incident_ambiguous.json"))


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")


@pytest.fixture
def no_keys(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def groq_body(payload: dict) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": json.dumps(payload)}}]}


def gemini_body(payload: dict) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}


GOOD = {
    "summary": "postgres ran out of connections \u2014 everything else is a downstream effect.",
    "sentences": [
        {"text": "postgres connection pool reached 100% at 14:02:05 IST.", "cites": ["evt_000784", "evt_000481"]},
        {"text": "auth-svc logins then failed \u2014 no DB connection.", "cites": ["evt_000787"]},
        {"text": "A made up claim about a cache.", "cites": ["evt_999999"]},
    ],
}


def run(incident, handler):
    return asyncio.run(narrate(incident, transport=httpx.MockTransport(handler)))


def test_groq_unknown_cite_dropped(hero, keys):
    def handler(req: httpx.Request):
        assert "groq.com" in req.url.host
        assert req.headers["authorization"] == "Bearer test-groq"
        body = json.loads(req.content)
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json=groq_body(GOOD))

    rep = run(hero, handler)
    assert rep.provider == "groq"
    assert rep.dropped == 1
    assert len(rep.sentences) == 2
    assert EM not in rep.summary and all(EM not in s.text for s in rep.sentences)


@pytest.mark.parametrize("failure", ["timeout", "500", "badjson"])
def test_groq_failure_falls_through_to_gemini(hero, keys, failure):
    seen = []

    def handler(req: httpx.Request):
        seen.append(req.url.host)
        if "groq.com" in req.url.host:
            if failure == "timeout":
                raise httpx.ReadTimeout("slow", request=req)
            if failure == "500":
                return httpx.Response(500, json={"error": "boom"})
            return httpx.Response(200, json={"choices": [{"message": {"content": "not json {"}}]})
        assert "generativelanguage.googleapis.com" in req.url.host
        assert req.headers["x-goog-api-key"] == "test-gemini"
        assert json.loads(req.content)["generationConfig"]["responseMimeType"] == "application/json"
        return httpx.Response(200, json=gemini_body(GOOD))

    rep = run(hero, handler)
    assert rep.provider == "gemini"
    assert rep.dropped == 1
    assert len(seen) == 2


def test_both_fail_gives_template(hero, keys):
    def handler(req):
        return httpx.Response(503, text="down")

    rep = run(hero, handler)
    assert rep.provider == "template"
    assert rep.sentences


def test_hang_respects_total_budget(hero, keys, monkeypatch):
    monkeypatch.setattr(llm, "TOTAL_BUDGET_S", 0.6)

    async def slow(*a, **k):
        await asyncio.sleep(5)

    monkeypatch.setattr(llm, "PROVIDERS", (("groq", "GROQ_API_KEY", slow), ("gemini", "GEMINI_API_KEY", slow)))
    import time

    llm._ssl_context()  # warm, as make_router does at startup
    t0 = time.monotonic()
    rep = asyncio.run(narrate(hero))
    assert time.monotonic() - t0 < 1.5
    assert rep.provider == "template"


def test_no_keys_gives_template_without_network(hero, no_keys):
    def handler(req):
        raise AssertionError("no request expected")

    rep = run(hero, handler)
    assert rep.provider == "template" and rep.dropped == 0


def test_all_dropped_falls_back(hero, keys):
    bad = {"summary": "x", "sentences": [{"text": "nope", "cites": ["evt_nope"]}, {"text": "empty", "cites": []}]}

    def handler(req):
        if "groq.com" in req.url.host:
            return httpx.Response(200, json=groq_body(bad))
        return httpx.Response(500)

    rep = run(hero, handler)
    assert rep.provider == "template"
    assert rep.dropped == 2


@pytest.mark.parametrize("name", ["incident_db_pool.json", "incident_ambiguous.json"])
def test_template_grounded(name, no_keys):
    inc = Incident.model_validate(load_fixture(name))
    rep = asyncio.run(narrate(inc))
    known = {e.id for e in inc.evidence_events}
    assert len(rep.sentences) >= 5
    for s in rep.sentences:
        assert s.cites and set(s.cites) <= known
        assert EM not in s.text
    assert EM not in rep.summary
    # the template must survive its own grounding check
    kept, dropped = ground([s.model_dump() for s in rep.sentences], inc)
    assert dropped == 0


def test_hero_template_numbers(hero, no_keys):
    rep = asyncio.run(narrate(hero))
    text = rep.summary + " ".join(s.text for s in rep.sentences)
    for needle in ("postgres", "0.80", "14 alerts", "57 users", "38 failed payments", "INR 45,462", "14:02:05 IST"):
        assert needle in text


def test_abstained_wording(amb, no_keys):
    rep = asyncio.run(narrate(amb))
    assert "declines to" in rep.summary
    assert "declines to pick" in rep.sentences[0].text
    text = " ".join(s.text for s in rep.sentences)
    assert "0.433" in text and "0.500" in text  # engine scores in the fixture
    assert "Data that would settle it" in text


def test_abstained_llm_without_decline_gets_it_added(amb, keys):
    payload = {
        "summary": "Two candidates.",
        "sentences": [{"text": "ledger-svc errors began at 14:20:31 IST.", "cites": ["evt_001620"]}],
    }

    def handler(req):
        return httpx.Response(200, json=groq_body(payload))

    rep = run(amb, handler)
    assert rep.provider == "groq"
    assert "declines to pick" in rep.sentences[0].text


def test_grounding_blames_other_service(hero):
    raw = [
        {"text": "The root cause is ledger-svc.", "cites": ["evt_000790"]},
        {"text": "ledger-svc failed because postgres is the root cause.", "cites": ["evt_000790"]},
        {"text": "payments-svc failed because ledger-svc returned 503.", "cites": ["evt_000793"]},
    ]
    kept, dropped = ground(raw, hero)
    assert dropped == 1
    assert [s.text for s in kept][0].startswith("ledger-svc failed")


def test_grounding_does_not_blame_check_when_abstained(amb):
    kept, dropped = ground([{"text": "The root cause may be ledger-svc or postgres.", "cites": ["evt_001620"]}], amb)
    assert dropped == 0 and len(kept) == 1


def test_sanitize():
    assert grounding.sanitize("a \u2014 b") == "a, b"
    assert "\u2014" not in grounding.sanitize("x\u2014y")


def test_router_200_404_and_cache(hero, no_keys):
    calls = []

    def getter(i):
        calls.append(i)
        return hero if i == hero.id else None

    app = FastAPI()
    r = make_router(getter)
    app.include_router(r)
    c = TestClient(app)
    res = c.post(f"/api/incidents/{hero.id}/report")
    assert res.status_code == 200
    body = res.json()
    assert body["incident_id"] == hero.id and body["provider"] == "template"
    again = c.post(f"/api/incidents/{hero.id}/report").json()
    assert again["generated_at"] == body["generated_at"]  # served from cache
    assert c.post("/api/incidents/nope/report").status_code == 404


def test_sanitize_normalises_unicode_hyphens():
    assert grounding.sanitize("ledger\u2011svc and auth\u2010svc") == "ledger-svc and auth-svc"
