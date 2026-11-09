"""FastAPI app: CORS, health, routers.

Lanes add their routers here at integration time:
  Lane A: sim + ingest + stream routers
  Lane B: engine is called from store.py, incidents router
  Lane D: report router
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.schemas import HealthResponse

VERSION = "0.1.0"

app = FastAPI(title="J.A.R.V.I.S.", version=VERSION)

_origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=VERSION)
