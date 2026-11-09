"""GET /api/eval/results -> EvalResults.

Served from <repo root>/eval/results.json (written only by eval/run_eval.py). Until that
exists, the honest empty fixture (n=0, "eval not run yet") is returned. Never invented.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter

from app.schemas import FIXTURES, EvalResults

router = APIRouter(prefix="/api/eval", tags=["eval"])

REPO_ROOT = FIXTURES.parent.parent
RESULTS = REPO_ROOT / "eval" / "results.json"
FALLBACK = FIXTURES / "eval_results.json"


def load_results(path: Path | None = None) -> EvalResults:
    path = path or RESULTS
    src = path if path.exists() else FALLBACK
    return EvalResults.model_validate(json.loads(src.read_text(encoding="utf-8")))


@router.get("/results", response_model=EvalResults)
async def eval_results() -> EvalResults:
    return load_results()
