"""Naive AI mode (U4). No real network: httpx.MockTransport stands in for Groq and Gemini."""
import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.narrate import naive as nv
from app.narrate.naive import (
    MAX_LINES, NAIVE_PROMPT, ask_naive, build_prompt, compact_events, make_naive_router, parse_root_service,
    read_measured,
)
from app.schemas import Event, Incident
from tests.conftest import load_fixture

HYPH = chr(0x2011)


def test_prompt_is_frozen():
    assert NAIVE_PROMPT == (
        "Here are the events from a production system during an incident. What is the single root cause "
        "service? Answer with the service id and a short explanation."
    )


def test_compact_events_bounded_deterministic_neutral(db_pool_events):
    out = compact_events(db_pool_events)
    lines = out.split("\n")
    assert len(lines) <= MAX_LINES + 2  # header + cap + omitted note
    assert compact_events(list(reversed(db_pool_events))) == out
    assert "http_request" not in out and " | request | " not in out
    for e in db_pool_events:
        if e.kind in ("alert", "change"):
            assert e.ts + " | " + e.service + " | " + e.kind in out
    body = lines[1:-1] if lines[-1].startswith("(") else lines[1:]
    assert [ln.split(" | ")[0] for ln in body] == sorted(ln.split(" | ")[0] for ln in body)
    low = out.lower()
    for word in ("topology", "candidate", "score", "depends on", "jarvis"):
        assert word not in low
    assert len(build_prompt(db_pool_events)) < 40_000


def test_compact_events_small_input_keeps_all():
    evs = [Event(id=f"evt_{i}", ts=f"2026-11-09T08:32:0{i}.000Z", source="sim", service="postgres",
                 kind="log", severity="error", name="log", message="m") for i in range(3)]
    out = compact_events(evs)
    assert len(out.split("\n")) == 4 and "omitted" not in out


@pytest.mark.parametrize("text,want", [
    (load_fixture("naive_answer_db_pool.json")["raw_text"], "postgres"),
    (f"Root cause service: ledger{HYPH}svc. It failed first.", "ledger-svc"),
    (f"**Service id:** `auth{chr(0x2010)}svc`", "auth-svc"),
    ("api-gateway errors are symptoms. service id: postgres", "postgres"),
    ("The gateway api-gateway and payments-svc failed; postgres too.", "api-gateway"),
    ("It is PostgreSQL saturating", "postgres"),
    ("root cause service: the database, see payments-svc", "payments-svc"),
    ("no idea", None),
    ("", None),
])
def test_parser(text, want):
    assert parse_root_service(text) == want


def _run(handler, events, monkeypatch, groq=True, gemini=True, timeout_s=5.0):
    monkeypatch.setenv("GROQ_API_KEY", "k1") if groq else monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "k2") if gemini else monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    return asyncio.run(ask_naive(events, timeout_s=timeout_s, transport=httpx.MockTransport(handler)))


def test_groq_success_sends_frozen_prompt_temp0(db_pool_events, monkeypatch):
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Service id: postgres. Pool full."}}]})

    ans = _run(handler, db_pool_events, monkeypatch)
    assert ans.provider == "groq" and ans.model == "openai/gpt-oss-120b"
    assert ans.root_cause_service == "postgres" and ans.measured is None
    b = seen["body"]
    assert b["temperature"] == 0 and b["reasoning_effort"] == "low"
    assert b["messages"][0]["content"] == build_prompt(db_pool_events)


def test_fallback_to_gemini(db_pool_events, monkeypatch):
    def handler(req):
        if "groq" in str(req.url):
            return httpx.Response(413, json={"error": "too large"})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ledger-svc"}]}}]})

    ans = _run(handler, db_pool_events, monkeypatch)
    assert ans.provider == "gemini" and ans.model == "gemini-2.5-flash"
    assert ans.root_cause_service == "ledger-svc"


def test_both_fail_never_raises(db_pool_events, monkeypatch):
    def handler(req):
        return httpx.Response(500)

    ans = _run(handler, db_pool_events, monkeypatch)
    assert ans.root_cause_service is None and ans.provider == "template"
    assert "groq: HTTP 500" in ans.raw_text and "gemini: HTTP 500" in ans.raw_text


def test_no_keys(db_pool_events, monkeypatch):
    ans = _run(lambda r: httpx.Response(200), db_pool_events, monkeypatch, groq=False, gemini=False)
    assert ans.root_cause_service is None and "no LLM key" in ans.raw_text


def test_read_measured(tmp_path):
    p = tmp_path / "results.json"
    assert read_measured(p) is None
    p.write_text(json.dumps({"methods": [{"name": "jarvis", "top1": 0.9, "n": 100},
                                         {"name": "llm_only", "top1": 0.55, "n": 40}]}))
    m = read_measured(p)
    assert m.top1 == 0.55 and m.n == 40


def test_router_200_404_cache_and_measured(db_pool_events, tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k1")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    inc = Incident.model_validate(load_fixture("incident_db_pool.json"))
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "root cause service: postgres"}}]})

    results = tmp_path / "results.json"
    app = FastAPI()
    app.include_router(make_naive_router(lambda i: inc if i == inc.id else None, lambda i: db_pool_events,
                                         results_path=results, transport=httpx.MockTransport(handler)))
    c = TestClient(app)
    assert c.get("/api/incidents/nope/naive").status_code == 404
    r = c.get(f"/api/incidents/{inc.id}/naive")
    assert r.status_code == 200
    assert r.json()["root_cause_service"] == "postgres" and r.json()["measured"] is None
    results.write_text(json.dumps({"methods": [{"name": "llm_only", "top1": 0.5, "n": 40}]}))
    r2 = c.get(f"/api/incidents/{inc.id}/naive").json()
    assert r2["measured"] == {"top1": 0.5, "n": 40}
    assert calls["n"] == 1  # cached per (id, updated_at)


def test_default_results_path_is_repo_eval():
    assert nv.RESULTS_PATH.parts[-2:] == ("eval", "results.json")
    assert (nv.REPO_ROOT / "contracts").is_dir()
