"""POST /api/incidents/{incident_id}/report

Decoupled from the store: make_router() takes a getter, so tests pass a stub
and main.py passes the live store's lookup. Reports are cached per
(incident id, incident updated_at), so repeated clicks during the demo are
instant. A template report produced while an LLM key is configured (i.e. the
LLM failed) is cached for only 30 s, so a transient provider error heals.
"""
from __future__ import annotations

import os
import time
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException

from app.schemas import Incident, Report

from .llm import _ssl_context, narrate

Getter = Callable[[str], Optional[Incident]]
TEMPLATE_RETRY_S = 30.0


def make_router(get_incident: Getter) -> APIRouter:
    router = APIRouter()
    if os.getenv("GROQ_API_KEY") or os.getenv("GEMINI_API_KEY"):
        _ssl_context()  # pay the ~1.5 s SSL setup at startup, not on the first click
    cache: dict[tuple[str, str], tuple[Report, float]] = {}

    @router.post("/api/incidents/{incident_id}/report", response_model=Report)
    async def report(incident_id: str) -> Report:
        incident = get_incident(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail=f"unknown incident {incident_id}")
        key = (incident.id, incident.updated_at)
        hit = cache.get(key)
        if hit and hit[1] > time.monotonic():
            return hit[0]
        rep = await narrate(incident)
        llm_configured = bool(os.getenv("GROQ_API_KEY") or os.getenv("GEMINI_API_KEY"))
        ttl = TEMPLATE_RETRY_S if (rep.provider == "template" and llm_configured) else float("inf")
        # keep only the newest version per incident
        for k in [k for k in cache if k[0] == incident.id]:
            del cache[k]
        cache[key] = (rep, time.monotonic() + ttl)
        return rep

    router.cache = cache  # type: ignore[attr-defined]  (exposed for tests)
    return router
