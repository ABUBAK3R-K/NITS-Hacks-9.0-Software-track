"""J.A.R.V.I.S. data contracts (pydantic v2).

These models are LAW. Backend responses must validate against them and
frontend types (contracts/types.ts) must mirror them field for field.
Changing a shape means updating this file, types.ts, fixtures/ and both
sides in the same commit.

Timestamps are ISO 8601 strings in UTC with millisecond precision and a
trailing "Z", e.g. "2026-11-09T14:02:05.120Z".
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------- enums
Source = Literal["sim", "api", "upload"]
EventKind = Literal["metric", "log", "alert", "change", "request"]
Severity = Literal["info", "warn", "error", "critical"]
Tier = Literal["edge", "app", "data", "external"]
IncidentStatus = Literal["OPEN", "ABSTAINED", "RESOLVED"]
Health = Literal["healthy", "degraded", "critical"]
Provider = Literal["groq", "gemini", "template"]
FaultType = Literal[
    "db_pool_exhaustion", "bad_deploy", "redis_down", "red_herring", "psp_slow"
]

# Service ids of the simulated payments platform. Uploaded/API events may use
# other service ids, so Event.service stays a plain str.
SERVICE_IDS = (
    "api-gateway",
    "auth-svc",
    "redis-session",
    "postgres",
    "payments-svc",
    "ledger-svc",
    "psp-adapter",
)

# Known root-cause signatures (rca.py picks one, recommend.py keys on it).
# Free str in the model so the engine can emit new ones without a contract change.
SIGNATURES = (
    "connection_pool_exhausted",  # postgres pool saturated, connection refused
    "error_spike_after_deploy",   # 5xx spike shortly after a change event
    "dependency_down",            # service unreachable / connection refused
    "latency_spike",              # p95 latency anomaly without errors dominating
    "error_spike",                # error rate anomaly without a change event
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------------------------------------------------------------- events
class Event(_Model):
    """One observation. Metric events carry `value`; request events carry
    attrs.status (HTTP code), attrs.user_id and, on payments-svc,
    attrs.amount_inr. Change events carry attrs.version."""

    id: str = Field(pattern=r"^evt_[A-Za-z0-9_]+$")
    ts: str
    source: Source
    service: str
    kind: EventKind
    severity: Severity
    name: str  # latency_p95_ms | error_rate | db_pool_in_use_pct | deploy | log | <AlertName> | http_request ...
    value: Optional[float] = None
    message: Optional[str] = None
    attrs: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


# ---------------------------------------------------------------- topology
class Service(_Model):
    id: str
    label: str
    tier: Tier


class Edge(_Model):
    """`from` calls `to` (caller -> callee). `to` is a dependency of `from`."""

    from_: str = Field(alias="from")
    to: str


class Topology(_Model):
    services: list[Service]
    edges: list[Edge]


class ServiceHealth(_Model):
    service: str
    health: Health
    is_root_cause: bool = False


class TopologyState(_Model):
    """SSE `topology_state` payload: current health of every node."""

    ts: str
    services: list[ServiceHealth]
    # Edges on the propagation path of the top OPEN incident, root first, for
    # animated edges in the graph. Each item is [caller, callee].
    hot_edges: list[tuple[str, str]] = Field(default_factory=list)


# ---------------------------------------------------------------- incidents
class ScoreComponents(_Model):
    """Weighted contributions (already multiplied by their weight), so
    upstream <= 0.40, precedence <= 0.30, change <= 0.20, severity <= 0.10.
    Candidate.score = (upstream + precedence + change + severity) * penalty."""

    upstream: float
    precedence: float
    change: float
    severity: float


class ComponentEvidence(_Model):
    """Event ids that produced each score component."""

    upstream: list[str] = Field(default_factory=list)
    precedence: list[str] = Field(default_factory=list)
    change: list[str] = Field(default_factory=list)
    severity: list[str] = Field(default_factory=list)


class Candidate(_Model):
    service: str
    score: float  # final, after penalty, 0..1, rounded to 3 dp
    components: ScoreComponents
    penalty: float = 1.0  # 0.5 when the service has an anomalous dependency of its own
    victim_of: Optional[str] = None  # the anomalous dependency that triggered the penalty
    onset: Optional[str] = None  # first anomalous ts of this service
    signature: Optional[str] = None
    evidence: list[str]  # union of component_evidence, never empty
    component_evidence: ComponentEvidence = Field(default_factory=ComponentEvidence)


class RootCause(_Model):
    service: str
    signature: str
    confidence: float  # = top candidate score
    evidence: list[str] = Field(min_length=1)


class Impact(_Model):
    services: list[str]  # anomalous set + all upstream callers to the edge
    users_affected: int  # unique attrs.user_id on failed request events
    failed_payments: int  # failed request events on payments-svc
    failed_value_inr: float  # sum of attrs.amount_inr over those


class TimelineEntry(_Model):
    ts: str
    service: str
    what: str  # human readable, e.g. "error_rate 0.42 (baseline 0.01)"
    event_ids: list[str]
    caused_by: Optional[str] = None  # nearest anomalous dependency with an earlier onset


class Recommendation(_Model):
    action: str
    why: str
    evidence: list[str]
    runbook: str


class IncidentSummary(_Model):
    """Item of GET /api/incidents. Cheap to send over SSE."""

    id: str
    title: str  # e.g. "postgres: connection_pool_exhausted"
    status: IncidentStatus
    opened_at: str
    updated_at: str
    services: list[str]
    alert_count: int  # number of kind=="alert" events grouped into the incident
    severity: Severity
    root_service: Optional[str] = None  # root_cause.service, or None when ABSTAINED
    confidence: Optional[float] = None  # root_cause.confidence, or top1 score when ABSTAINED


class Incident(_Model):
    """GET /api/incidents/{id} and SSE `incident_update` payload."""

    id: str
    title: str
    status: IncidentStatus
    opened_at: str
    updated_at: str
    services: list[str]  # anomalous services in the group
    alert_count: int
    severity: Severity
    root_cause: Optional[RootCause] = None  # None when ABSTAINED
    candidates: list[Candidate]  # sorted by score desc
    abstain_reason: Optional[str] = None  # set when ABSTAINED: which rule fired, with numbers
    disambiguation: list[str] = Field(default_factory=list)  # "what data would disambiguate", set when ABSTAINED
    impact: Impact
    timeline: list[TimelineEntry]  # sorted by ts asc
    recommendations: list[Recommendation]
    # Every event id referenced anywhere above, resolved, so the UI evidence
    # drawer and the narrator need no extra request. This is the incident's
    # evidence set for citation validation.
    evidence_events: list[Event]


# ---------------------------------------------------------------- report
class ReportSentence(_Model):
    text: str
    cites: list[str] = Field(min_length=1)


class Report(_Model):
    """POST /api/incidents/{id}/report"""

    incident_id: str
    summary: str
    sentences: list[ReportSentence]  # only sentences that survived grounding
    dropped: int  # sentences removed because cites were missing/unknown
    provider: Provider
    generated_at: str


# ---------------------------------------------------------------- api io
class FaultRequest(_Model):
    type: FaultType


class SimStatus(_Model):
    """Response of POST /api/sim/start|fault|reset."""

    running: bool
    active_faults: list[FaultType]
    events_in_store: int


class IngestResult(_Model):
    """Response of POST /api/events and POST /api/upload."""

    accepted: int
    rejected: int
    errors: list[str] = Field(default_factory=list)  # first few parse errors, "line 12: ..."


class HealthResponse(_Model):
    """GET /health"""

    status: Literal["ok"]
    version: str


# SSE (GET /api/stream), each message is `event: <name>\ndata: <json>\n\n`:
#   event: event            data: Event            (throttled sample of raw events)
#   event: incident_update  data: Incident         (full detail, on every recompute that changed it)
#   event: topology_state   data: TopologyState    (every recompute, and every 2 s heartbeat)
SSE_EVENT_NAMES = ("event", "incident_update", "topology_state")

ENGINE_WEIGHTS = {"upstream": 0.40, "precedence": 0.30, "change": 0.20, "severity": 0.10}
VICTIM_PENALTY = 0.5
ABSTAIN_MIN_MARGIN = 0.15
ABSTAIN_MIN_SCORE = 0.45
