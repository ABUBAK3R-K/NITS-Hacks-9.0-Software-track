"""POST /api/sim/start | fault | reset -> SimStatus."""
from __future__ import annotations

from fastapi import APIRouter

from app.schemas import FaultRequest, SimStatus
from app.store import store

router = APIRouter(prefix="/api/sim", tags=["sim"])


@router.post("/start", response_model=SimStatus)
async def sim_start() -> SimStatus:
    store.start()
    return store.status()


@router.post("/fault", response_model=SimStatus)
async def sim_fault(req: FaultRequest) -> SimStatus:
    store.inject(req.type)  # starts the sim if it is stopped
    return store.status()


@router.post("/reset", response_model=SimStatus)
async def sim_reset() -> SimStatus:
    store.reset()
    return store.status()
