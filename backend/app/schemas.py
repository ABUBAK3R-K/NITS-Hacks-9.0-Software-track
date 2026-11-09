"""Backend entry point to the shared contracts.

contracts/ lives at the repo root (shared with the frontend), so we put the
repo root on sys.path once here. Everything in the backend imports models
from `app.schemas`, never from `contracts` directly.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from contracts.schemas import *  # noqa: E402,F401,F403
from contracts.schemas import (  # noqa: E402,F401  (explicit names for IDEs)
    ABSTAIN_MIN_MARGIN, ABSTAIN_MIN_SCORE, ENGINE_WEIGHTS, SERVICE_IDS, SIGNATURES, VICTIM_PENALTY,
    Candidate, ComponentEvidence, Edge, Event, FaultRequest, HealthResponse, Impact, Incident,
    IncidentSummary, IngestResult, Recommendation, Report, ReportSentence, RootCause,
    ScoreComponents, Service, ServiceHealth, SimStatus, TimelineEntry, Topology, TopologyState,
)

FIXTURES = _ROOT / "contracts" / "fixtures"
