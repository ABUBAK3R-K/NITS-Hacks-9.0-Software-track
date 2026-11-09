"""GET /api/topology, GET /api/incidents, GET /api/incidents/{id}."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from app.schemas import FIX_IDS, ApplyFixRequest, ApplyFixResult, Incident, IncidentSummary, Topology
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
