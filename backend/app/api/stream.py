"""GET /api/stream: Server-Sent Events.

Wire format (identical to contracts/fixtures/sse_sample.txt):
    event: <event|incident_update|topology_state>\ndata: <compact JSON>\n\n
On connect the client first gets the current topology_state and one
incident_update per known incident, then live messages.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.store import incident_json, store

router = APIRouter(prefix="/api", tags=["stream"])


@router.get("/stream")
async def stream(request: Request, limit: int = 0):
    """`limit` > 0 closes the stream after that many messages (tests, curl)."""

    async def gen():
        q = store.subscribe()
        sent = 0
        try:
            first = [("topology_state", store.topology_state().model_dump_json(by_alias=True))]
            first += [("incident_update", incident_json(i)) for i in list(store.incidents.values())]
            for name, data in first:
                yield {"event": name, "data": data}
                sent += 1
                if limit and sent >= limit:
                    return
            while True:
                if await request.is_disconnected():
                    return
                try:
                    name, data = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                yield {"event": name, "data": data}
                sent += 1
                if limit and sent >= limit:
                    return
        finally:
            store.unsubscribe(q)

    return EventSourceResponse(gen(), sep="\n", ping=15)
