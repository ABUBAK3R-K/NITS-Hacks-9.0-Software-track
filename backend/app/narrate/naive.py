"""Naive AI mode (U4): ask an LLM for the root cause with no engine at all.

This is the honest baseline. The model gets a frozen neutral prompt plus a
compact dump of the raw events: no topology, no ranking, no engine output, no
hints. The same compact_events() + NAIVE_PROMPT feed both the incident page
(GET /api/incidents/{id}/naive) and the eval baseline ("llm_only" in
eval/run_eval.py), so the UI and the eval see the same input.

ask_naive() never raises: Groq first, Gemini fallback, else an answer with
root_cause_service=None and raw_text saying what failed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
from fastapi import APIRouter, HTTPException

from app.schemas import SERVICE_IDS, Event, Incident, Measured, NaiveAnswer

from .llm import GEMINI_URL, GROQ_MODEL_DEFAULT, GROQ_URL, _ssl_context

log = logging.getLogger("jarvis.naive")

# FROZEN. Written verbatim from UPGRADES.md before any eval result existed.
# Never weaken, reword or add hints to it to make the engine look better:
# the honesty guardrail says the naive baseline gets a neutral prompt.
NAIVE_PROMPT = (
    "Here are the events from a production system during an incident. "
    "What is the single root cause service? "
    "Answer with the service id and a short explanation."
)

MAX_LINES = 150  # the full dump got HTTP 413 Payload Too Large from Groq
MAX_MESSAGE_CHARS = 160
GEMINI_MODEL_DEFAULT = "gemini-2.5-flash"
GROQ_SHARE = 0.6  # Groq may use 60% of the timeout, Gemini gets the rest

REPO_ROOT = Path(__file__).resolve().parents[3]
RESULTS_PATH = REPO_ROOT / "eval" / "results.json"


# ------------------------------------------------------------ compact dump
def _keep(e: Event) -> bool:
    if e.kind == "request":
        return False  # hundreds of per-request events caused the 413
    if e.kind == "metric":
        return e.severity != "info"
    return True  # alerts, changes, logs


def _fmt_value(v: Optional[float]) -> str:
    if v is None:
        return ""
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def _line(e: Event) -> str:
    msg = " ".join((e.message or "").split())
    if len(msg) > MAX_MESSAGE_CHARS:
        msg = msg[: MAX_MESSAGE_CHARS - 3] + "..."
    return " | ".join([e.ts, e.service, e.kind, e.severity, e.name, _fmt_value(e.value), msg])


def compact_events(events: list[Event]) -> str:
    """Deterministic, size-bounded, neutral dump: one line per event.

    Drops per-request events and info metrics. Keeps alerts, changes, logs and
    non-info metrics. If more than MAX_LINES remain, keeps every change and
    alert event plus the earliest other lines up to the cap. Output is in time
    order. Same input always gives the same string.
    """
    kept = sorted((e for e in events if _keep(e)), key=lambda e: (e.ts, e.id))
    omitted = 0
    if len(kept) > MAX_LINES:
        must = [e for e in kept if e.kind in ("change", "alert")][:MAX_LINES]
        must_ids = {e.id for e in must}
        rest = [e for e in kept if e.id not in must_ids][: MAX_LINES - len(must)]
        chosen = {e.id for e in must} | {e.id for e in rest}
        omitted = len(kept) - len(chosen)
        kept = [e for e in kept if e.id in chosen]
    lines = ["ts | service | kind | severity | name | value | message"]
    lines += [_line(e) for e in kept]
    if omitted:
        lines.append(f"({omitted} more events of the same kinds omitted for size)")
    return "\n".join(lines)


def build_prompt(events: list[Event]) -> str:
    return NAIVE_PROMPT + "\n\nEvents:\n" + compact_events(events)


# ------------------------------------------------------------ answer parser
_HYPHENS = {chr(c): "-" for c in (0x2010, 0x2011, 0x2012, 0x2013, 0x2212)}
_ALIASES = {"postgresql": "postgres"}
_EXPLICIT = re.compile(
    r"(?:root[\s-]*cause[\s-]*service(?:[\s-]*id)?|service[\s-]*id)\s*(?:is)?\s*[:=]?\s*([a-z][a-z0-9-]*)"
)
_TOKEN = re.compile(r"(?<![a-z0-9-])(" + "|".join(
    re.escape(s) for s in sorted(list(SERVICE_IDS) + list(_ALIASES), key=len, reverse=True)
) + r")(?![a-z0-9-])")


def normalize(text: str) -> str:
    for k, v in _HYPHENS.items():
        text = text.replace(k, v)
    return text


def parse_root_service(text: str) -> Optional[str]:
    """Explicit "service id: X" / "root cause service: X" first, else the first
    known service id mentioned anywhere. None if nothing matches."""
    t = normalize(text or "").lower()
    plain = re.sub(r"[*`_\"']", "", t)
    for m in _EXPLICIT.finditer(plain):
        cand = _ALIASES.get(m.group(1), m.group(1))
        if cand in SERVICE_IDS:
            return cand
    m = _TOKEN.search(t)
    if m:
        return _ALIASES.get(m.group(1), m.group(1))
    return None


# ------------------------------------------------------------ providers
async def _groq(client: httpx.AsyncClient, prompt: str, key: str, timeout: float) -> tuple[str, str]:
    model = os.getenv("GROQ_MODEL", GROQ_MODEL_DEFAULT)
    body: dict[str, Any] = {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }
    if model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = "low"  # same setting as the narrator
    r = await client.post(GROQ_URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
    r.raise_for_status()
    return model, r.json()["choices"][0]["message"]["content"] or ""


async def _gemini(client: httpx.AsyncClient, prompt: str, key: str, timeout: float) -> tuple[str, str]:
    model = os.getenv("GEMINI_MODEL", GEMINI_MODEL_DEFAULT)
    gen_cfg: dict[str, Any] = {"temperature": 0}
    if "flash" in model and "2.5" in model:
        gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}  # same setting as the narrator
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen_cfg}
    r = await client.post(GEMINI_URL.format(model=model), json=body, headers={"x-goog-api-key": key},
                          timeout=timeout)
    r.raise_for_status()
    parts = r.json()["candidates"][0]["content"]["parts"]
    return model, "".join(p.get("text", "") for p in parts)


async def ask_naive(
    events: list[Event], *, timeout_s: float = 20.0, transport: httpx.AsyncBaseTransport | None = None
) -> NaiveAnswer:
    """Frozen prompt + compact events, temperature 0. Never raises.
    `transport` is for tests (httpx.MockTransport). `measured` is left None."""
    t0 = time.monotonic()
    failures: list[str] = []
    try:
        prompt = build_prompt(events)
        groq_key = os.getenv("GROQ_API_KEY", "").strip()
        gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not groq_key and not gemini_key:
            failures.append("no LLM key configured")
        else:
            verify = True if transport is not None else _ssl_context()
            deadline = t0 + timeout_s
            async with httpx.AsyncClient(transport=transport, verify=verify) as client:
                for name, key, call in (("groq", groq_key, _groq), ("gemini", gemini_key, _gemini)):
                    if not key:
                        failures.append(f"{name}: no key")
                        continue
                    remaining = deadline - time.monotonic()
                    if remaining < 0.5:
                        failures.append(f"{name}: no time left")
                        continue
                    attempt = remaining * GROQ_SHARE if (name == "groq" and gemini_key) else remaining
                    try:
                        model, text = await asyncio.wait_for(call(client, prompt, key, attempt), timeout=attempt)
                    except Exception as exc:
                        detail = type(exc).__name__
                        if isinstance(exc, httpx.HTTPStatusError):
                            detail = f"HTTP {exc.response.status_code}"
                        log.warning("naive provider %s failed: %s", name, detail)
                        failures.append(f"{name}: {detail}")
                        continue
                    if not text.strip():
                        failures.append(f"{name}: empty answer")
                        continue
                    return NaiveAnswer(
                        provider=name,
                        model=model,
                        root_cause_service=parse_root_service(text),
                        raw_text=text,
                        latency_ms=int((time.monotonic() - t0) * 1000),
                        measured=None,
                    )
    except Exception as exc:  # last line of defence
        log.exception("ask_naive failed")
        failures.append(f"internal: {type(exc).__name__}")
    return NaiveAnswer(
        provider="template",
        model="none",
        root_cause_service=None,
        raw_text="Naive LLM unavailable (" + "; ".join(failures) + "). No answer was produced.",
        latency_ms=int((time.monotonic() - t0) * 1000),
        measured=None,
    )


# ------------------------------------------------------------ endpoint
def read_measured(path: Path = RESULTS_PATH) -> Optional[Measured]:
    """top1 and n of the "llm_only" method from eval/results.json, or None."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for m in data.get("methods", []):
            if m.get("name") == "llm_only":
                return Measured(top1=float(m["top1"]), n=int(m["n"]))
    except Exception:
        return None
    return None


FAILED_RETRY_S = 30.0


def make_naive_router(
    get_incident: Callable[[str], Optional[Incident]],
    events_for_incident: Callable[[str], list[Event]],
    *,
    results_path: Optional[Path] = None,
    timeout_s: float = 20.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> APIRouter:
    """GET /api/incidents/{incident_id}/naive. Cached per (incident id, updated_at);
    a failed answer is cached for 30 s only. `measured` is read fresh each call,
    so it appears as soon as eval/results.json exists."""
    router = APIRouter()
    cache: dict[tuple[str, str], tuple[NaiveAnswer, float]] = {}
    rpath = results_path or RESULTS_PATH

    @router.get("/api/incidents/{incident_id}/naive", response_model=NaiveAnswer)
    async def naive(incident_id: str) -> NaiveAnswer:
        incident = get_incident(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail=f"unknown incident {incident_id}")
        key = (incident.id, incident.updated_at)
        hit = cache.get(key)
        if hit and hit[1] > time.monotonic():
            ans = hit[0]
        else:
            ans = await ask_naive(list(events_for_incident(incident.id)), timeout_s=timeout_s,
                                  transport=transport)
            ttl = FAILED_RETRY_S if ans.provider == "template" else float("inf")
            for k in [k for k in cache if k[0] == incident.id]:
                del cache[k]
            cache[key] = (ans, time.monotonic() + ttl)
        return ans.model_copy(update={"measured": read_measured(rpath)})

    router.cache = cache  # type: ignore[attr-defined]  (exposed for tests)
    return router
