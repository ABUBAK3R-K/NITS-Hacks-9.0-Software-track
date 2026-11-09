"""Sandbox runs (U1): POST /api/runs/blind | /api/runs/designed, GET /api/runs/{id}[/reveal].

Each run is simulated fast-forwarded in its own namespace (store.runs), never the live
store, and the response arrives after the engine answered. The simulation and engine run
in a worker thread so SSE and the live Chaos Lab keep flowing meanwhile.
"""
from __future__ import annotations

import asyncio
import secrets

from fastapi import APIRouter, HTTPException

from app.schemas import FaultSpec, Run, RunReveal
from app.sim.faults import validate_spec
from app.sim.runner import random_blind_spec
from app.store import store

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.post("/blind", response_model=Run)
async def run_blind() -> Run:
    spec = random_blind_spec(secrets.randbelow(1_000_000_000))
    return await asyncio.to_thread(store.runs.create, spec, "blind")


@router.post("/designed", response_model=Run)
async def run_designed(spec: FaultSpec) -> Run:
    try:
        validate_spec(spec)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await asyncio.to_thread(store.runs.create, spec, "designed")


@router.get("/{run_id}", response_model=Run)
async def get_run(run_id: str) -> Run:
    rec = store.runs.get(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id}")
    return rec.run


@router.get("/{run_id}/reveal", response_model=RunReveal)
async def reveal_run(run_id: str) -> RunReveal:
    out = store.runs.reveal(run_id)
    if out is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id}")
    return out
