"""GET /api/topology, GET /api/incidents, GET /api/incidents/{id}, GET /api/incidents/{id}/shift_check."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from app.engine.common import parse_ts
from app.engine.config import CONFIG
from app.engine.given_time import rank_at
from app.schemas import (FIX_IDS, ApplyFixRequest, ApplyFixResult, Incident, IncidentSummary, ShiftCandidate,
                         ShiftCheck, Topology)
from app.store import store

router = APIRouter(prefix="/api", tags=["incidents"])


@router.get("/topology", response_model=Topology, response_model_by_alias=True)
async def get_topology() -> Topology:
    return store.topology


@router.get("/incidents", response_model=list[IncidentSummary])
async def list_incidents() -> list[IncidentSummary]:
    return store.summaries()


@router.get("/incidents/{incident_id}", response_model=Incident)
async def get_incident(incident_id: str) -> Incident:
    inc = store.get_incident(incident_id)  # live or sandbox run
    if inc is None:
        raise HTTPException(status_code=404, detail=f"unknown incident {incident_id}")
    return inc


def shift_check(inc: Incident, events: list, cfg: dict = CONFIG) -> ShiftCheck:
    """Rank services by metric shift at the incident's first onset, independently of the engine."""
    failure_ts = min((t.ts for t in inc.timeline), default=inc.opened_at)
    res = rank_at(events, parse_ts(failure_ts), cfg)
    engine_root = inc.root_cause.service if inc.root_cause else None
    agrees = None if res.root is None or engine_root is None else res.root == engine_root
    return ShiftCheck(
        incident_id=inc.id, failure_ts=failure_ts, confident=res.confident, root_service=res.root, agrees=agrees,
        ranking=[ShiftCandidate(service=c.service, score=c.score, metric=c.metric, before_median=c.before_median,
                                after_median=c.after_median, evidence=c.evidence) for c in res.ranking[:5]])


@router.get("/incidents/{incident_id}/shift_check", response_model=ShiftCheck)
async def get_shift_check(incident_id: str) -> ShiftCheck:
    hit = store.events_and_incident(incident_id)
    if hit is None:
        raise HTTPException(status_code=404, detail=f"unknown incident {incident_id}")
    events, inc = hit
    return await asyncio.to_thread(shift_check, inc, events)


@router.post("/incidents/{incident_id}/apply_fix", response_model=ApplyFixResult)
async def apply_fix(incident_id: str, req: ApplyFixRequest) -> ApplyFixResult:
    """U5: the simulator applies the remediation (live or sandbox). The lifecycle moves
    only when the engine later sees the recovery, never because of this call."""
    if req.fix_id not in FIX_IDS:
        raise HTTPException(status_code=422, detail=f"unknown fix_id {req.fix_id}; known: {list(FIX_IDS)}")
    out = await asyncio.to_thread(store.apply_fix, incident_id, req.fix_id)
    if out is None:
        raise HTTPException(status_code=404, detail=f"unknown incident {incident_id}")
    return out
