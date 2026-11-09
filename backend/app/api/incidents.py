"""GET /api/topology, GET /api/incidents, GET /api/incidents/{id}."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas import Incident, IncidentSummary, Topology
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
    inc = store.incidents.get(incident_id)
    if inc is None:
        raise HTTPException(status_code=404, detail=f"unknown incident {incident_id}")
    return inc
