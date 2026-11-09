"""POST /api/events and POST /api/upload.

POST /api/events takes either a JSON list of Event, or an Alertmanager or
Grafana webhook body sent as-is (detected by shape, see ingest/webhooks.py).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, File, UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import TypeAdapter, ValidationError

from app.ingest.parsers import MAX_ERRORS, parse_upload
from app.ingest.webhooks import parse_webhook
from app.schemas import Event, IngestResult
from app.store import store

router = APIRouter(prefix="/api", tags=["ingest"])
_EVENTS = TypeAdapter(list[Event])


@router.post("/events", response_model=IngestResult)
async def ingest_events(body: Any = Body(...)) -> IngestResult:
    if isinstance(body, list):
        try:
            events = _EVENTS.validate_python(body)
        except ValidationError as exc:
            raise RequestValidationError(exc.errors(include_url=False))
        accepted, errors = store.add_events(events)
        return IngestResult(accepted=accepted, rejected=len(errors), errors=errors[:MAX_ERRORS])
    parsed = parse_webhook(body)
    if parsed is None:
        raise RequestValidationError([{
            "type": "value_error", "loc": ("body",), "input": None,
            "msg": "expected a list of Event, an Alertmanager webhook or a Grafana webhook",
        }])
    events, bad = parsed
    accepted, dup_errors = store.add_events(events)
    errors = (bad + dup_errors)[:MAX_ERRORS]
    return IngestResult(accepted=accepted, rejected=len(bad) + len(dup_errors), errors=errors)


@router.post("/upload", response_model=IngestResult)
async def upload(file: UploadFile = File(...)) -> IngestResult:
    raw = await file.read()
    text = raw.decode("utf-8-sig", errors="replace")
    events, rejected, errors = parse_upload(file.filename or "", text, store.new_upload_id)
    accepted, dup_errors = store.add_events(events)
    errors = (errors + dup_errors)[:MAX_ERRORS]
    return IngestResult(accepted=accepted, rejected=rejected + len(dup_errors), errors=errors)
