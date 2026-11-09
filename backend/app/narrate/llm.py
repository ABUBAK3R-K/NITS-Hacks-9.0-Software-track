"""Narrator: Groq -> Gemini Flash -> deterministic template.

The engine has already decided the root cause. The LLM only rephrases the
evidence, and grounding.py throws away anything it cannot back with an event
id from the incident. One total budget of 8 s covers every LLM attempt, so
the report endpoint answers in under ~8 s even when both providers hang.
narrate() never raises.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.schemas import Incident, Report

from .grounding import blames_other_service, ground, sanitize
from .template import ist, template_report_parts

log = logging.getLogger("jarvis.narrate")

TOTAL_BUDGET_S = 8.0
MIN_ATTEMPT_S = 0.5  # do not start a provider call with less than this left
# Groq is capped so a slow Groq call still leaves Gemini (~3.7 s measured) room inside the 8 s total.
PER_PROVIDER_CAP_S = {"groq": 4.5}

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# llama-3.3-70b-versatile is no longer served on our Groq account (404 model_not_found, 9 Nov).
# gpt-oss-120b tested at ~2.5 s on the hero incident with every sentence grounded.
GROQ_MODEL_DEFAULT = "openai/gpt-oss-120b"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM_PROMPT = """You write incident reports for an on-call engineer at a payments company.
A deterministic engine has ALREADY decided the root cause. You only explain what its evidence shows.

Rules:
1. Only state facts that the cited evidence event ids support. Do not guess or add new facts.
2. Every sentence must cite at least one id, copied exactly from evidence_events[].id. Never invent ids.
3. If status is OPEN, the root cause is root_cause.service. Never name any other service as the cause. Other failing services are downstream effects.
4. If status is ABSTAINED, say clearly that the system declines to pick a root cause, name the top two candidates with their scores, give abstain_reason, and list what data would disambiguate. Do not pick one yourself.
5. Plain language, short sentences, times in IST exactly as given (e.g. 14:02:05 IST). Use numbers from the input only.
6. Never use em dashes. Use commas or periods.
7. Write 4 to 8 sentences: root cause (or abstain decision), the propagation in time order, the impact, and the first recommended action.

Return ONLY a JSON object of this shape:
{"summary": "two sentences, no citations", "sentences": [{"text": "one sentence", "cites": ["evt_..."]}]}"""


_DECLINE = re.compile(r"declin|abstain|not (?:pick|name|choose)|cannot (?:tell|decide)|too close", re.IGNORECASE)


class NarrationError(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def compact_view(incident: Incident) -> dict[str, Any]:
    """Small JSON view of the incident for the prompt. Times are pre-converted to IST."""
    return {
        "id": incident.id,
        "status": incident.status,
        "title": incident.title,
        "opened_at": ist(incident.opened_at),
        "alert_count": incident.alert_count,
        "root_cause": (
            {
                "service": incident.root_cause.service,
                "signature": incident.root_cause.signature,
                "confidence": incident.root_cause.confidence,
                "evidence": incident.root_cause.evidence,
            }
            if incident.root_cause
            else None
        ),
        "abstain_reason": incident.abstain_reason,
        "disambiguation": incident.disambiguation,
        "top_candidates": [
            {
                "service": c.service,
                "score": c.score,
                "components": c.components.model_dump(),
                "penalty": c.penalty,
                "victim_of": c.victim_of,
                "onset": ist(c.onset) if c.onset else None,
                "evidence": c.evidence,
            }
            for c in incident.candidates[:3]
        ],
        "timeline": [
            {"time": ist(t.ts), "service": t.service, "what": t.what, "event_ids": t.event_ids, "caused_by": t.caused_by}
            for t in incident.timeline
        ],
        "impact": incident.impact.model_dump(),
        "recommendations": [{"action": r.action, "why": r.why, "evidence": r.evidence} for r in incident.recommendations],
        "evidence_events": [
            {
                "id": e.id,
                "time": ist(e.ts),
                "service": e.service,
                "kind": e.kind,
                "name": e.name,
                "value": e.value,
                "message": e.message,
            }
            for e in incident.evidence_events
        ],
    }


def _user_prompt(incident: Incident) -> str:
    return "Incident:\n" + json.dumps(compact_view(incident), separators=(",", ":"))


def _parse(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):] if "{" in text else text
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("sentences"), list):
        raise NarrationError("schema mismatch: no sentences list")
    if not isinstance(data.get("summary", ""), str):
        raise NarrationError("schema mismatch: summary not a string")
    return data


async def _call_groq(client: httpx.AsyncClient, incident: Incident, key: str, timeout: float) -> dict[str, Any]:
    model = os.getenv("GROQ_MODEL", GROQ_MODEL_DEFAULT)
    body: dict[str, Any] = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(incident)},
        ],
    }
    if model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = "low"  # keeps latency well inside the 8 s budget
    r = await client.post(GROQ_URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
    r.raise_for_status()
    return _parse(r.json()["choices"][0]["message"]["content"])


async def _call_gemini(client: httpx.AsyncClient, incident: Incident, key: str, timeout: float) -> dict[str, Any]:
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    gen_cfg: dict[str, Any] = {"responseMimeType": "application/json", "temperature": 0.2}
    if "flash" in model and "2.5" in model:
        gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}  # thinking would eat the 8 s budget
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": _user_prompt(incident)}]}],
        "generationConfig": gen_cfg,
    }
    r = await client.post(
        GEMINI_URL.format(model=model), json=body, headers={"x-goog-api-key": key}, timeout=timeout
    )
    r.raise_for_status()
    parts = r.json()["candidates"][0]["content"]["parts"]
    return _parse("".join(p.get("text", "") for p in parts))


PROVIDERS = (("groq", "GROQ_API_KEY", _call_groq), ("gemini", "GEMINI_API_KEY", _call_gemini))


def _template(incident: Incident, dropped: int = 0) -> Report:
    summary, sentences = template_report_parts(incident)
    return Report(
        incident_id=incident.id,
        summary=summary,
        sentences=sentences,
        dropped=dropped,
        provider="template",
        generated_at=_now_iso(),
    )


async def narrate(incident: Incident, *, transport: httpx.AsyncBaseTransport | None = None) -> Report:
    """Build a grounded Report. `transport` is for tests (httpx.MockTransport)."""
    try:
        return await _narrate(incident, transport)
    except Exception:  # last line of defence: the demo must never break
        log.exception("narrate failed, using template")
        try:
            return _template(incident)
        except Exception:
            log.exception("template failed")
            return Report(
                incident_id=incident.id,
                summary=sanitize(incident.title),
                sentences=[],
                dropped=0,
                provider="template",
                generated_at=_now_iso(),
            )


_SSL_CTX = None


def _ssl_context():
    """Building an SSL context costs ~1.5 s on Windows, so build it once."""
    global _SSL_CTX
    if _SSL_CTX is None:
        _SSL_CTX = httpx.create_ssl_context()
    return _SSL_CTX


async def _narrate(incident: Incident, transport: httpx.AsyncBaseTransport | None) -> Report:
    last_dropped = 0
    if not any(os.getenv(env_key, "").strip() for _, env_key, _ in PROVIDERS):
        return _template(incident)
    verify = True if transport is not None else _ssl_context()
    deadline = time.monotonic() + TOTAL_BUDGET_S
    async with httpx.AsyncClient(transport=transport, verify=verify) as client:
        for name, env_key, call in PROVIDERS:
            key = os.getenv(env_key, "").strip()
            if not key:
                continue
            remaining = deadline - time.monotonic()
            if remaining < MIN_ATTEMPT_S:
                log.warning("narrate budget spent before %s", name)
                break
            attempt = min(remaining, PER_PROVIDER_CAP_S.get(name, remaining))
            try:
                data = await asyncio.wait_for(call(client, incident, key, attempt), timeout=attempt)
            except Exception as exc:  # timeout, HTTP error, bad JSON, schema mismatch
                log.warning("narrate provider %s failed: %s", name, type(exc).__name__)
                continue
            sentences, dropped = ground(data.get("sentences"), incident)
            if not sentences:
                log.warning("narrate provider %s: all %d sentences dropped", name, dropped)
                last_dropped = dropped
                continue
            t_summary, t_sentences = template_report_parts(incident)
            summary = sanitize(data.get("summary") or "")
            if not summary or blames_other_service(summary, incident):
                summary = t_summary
            if incident.status == "ABSTAINED" and t_sentences and not any(
                _DECLINE.search(s.text) for s in sentences
            ):
                # the abstain decision must be stated; borrow the template's grounded sentence
                sentences.insert(0, t_sentences[0])
            return Report(
                incident_id=incident.id,
                summary=summary,
                sentences=sentences,
                dropped=dropped,
                provider=name,
                generated_at=_now_iso(),
            )
    return _template(incident, dropped=last_dropped)
