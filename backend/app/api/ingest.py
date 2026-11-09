"""POST /api/events and POST /api/upload."""
from __future__ import annotations

from fastapi import APIRouter, File, UploadFile

from app.ingest.parsers import MAX_ERRORS, parse_upload
from app.schemas import Event, IngestResult
from app.store import store

router = APIRouter(prefix="/api", tags=["ingest"])


@router.post("/events", response_model=IngestResult)
async def ingest_events(events: list[Event]) -> IngestResult:
    accepted, errors = store.add_events(events)
    return IngestResult(accepted=accepted, rejected=len(errors), errors=errors[:MAX_ERRORS])


@router.post("/upload", response_model=IngestResult)
async def upload(file: UploadFile = File(...)) -> IngestResult:
    raw = await file.read()
    text = raw.decode("utf-8-sig", errors="replace")
    events, rejected, errors = parse_upload(file.filename or "", text, store.new_upload_id)
    accepted, dup_errors = store.add_events(events)
    errors = (errors + dup_errors)[:MAX_ERRORS]
    return IngestResult(accepted=accepted, rejected=rejected + len(dup_errors), errors=errors)
