"""FastAPI app: CORS, health, routers, background sim loop.

Lane A: ingest, sim, incidents, stream routers + the live sim loop (lifespan).
Lane B: engine is called from store.py (app.engine.run.analyze).
Lane D: report router, included if present.
"""
import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import eval_results, incidents, ingest, runs, sim, stream
from app.narrate.router import make_router as make_report_router
from app.narrate.naive import make_naive_router
from app.schemas import HealthResponse
from app.store import run_loop, store

VERSION = "0.1.0"
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if os.getenv("SIM_AUTOSTART", "0") == "1":
        store.start()
    task = asyncio.create_task(run_loop(store))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="J.A.R.V.I.S.", version=VERSION, lifespan=lifespan)

_origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

class TouchLiveStore:
    """Plain ASGI middleware (safe for the SSE stream): every /api request, including
    opening the stream, postpones the live store's idle reset."""

    def __init__(self, app):
        self.inner = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and str(scope.get("path", "")).startswith("/api/"):
            store.touch()
        await self.inner(scope, receive, send)


app.add_middleware(TouchLiveStore)


app.include_router(ingest.router)
app.include_router(sim.router)
app.include_router(incidents.router)
app.include_router(stream.router)
app.include_router(runs.router)
app.include_router(eval_results.router)

# Report endpoint: the narrator gets incidents from the live store or any sandbox run.
# Wired explicitly (no try/except) so a broken import fails loudly at startup.
app.include_router(make_report_router(store.get_incident))
# Naive AI mode (U4): same events the engine saw, frozen neutral prompt.
app.include_router(make_naive_router(store.get_incident, store.events_for_incident))


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=VERSION)
