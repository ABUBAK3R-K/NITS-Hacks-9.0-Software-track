"""The five chaos faults and how they propagate.

A fault has a root service. Its onset moves up the dependency graph to the
CALLERS (edge from->to means from calls to), one hop at a time, with a lag of
300 to 1500 ms per hop, but only into the services listed in the fault's
`elevated` table. After a service's onset its metrics stay degraded on every
scrape, it writes recurring error logs, its request path fails, and its
alerts fire after their own lag (the gateway usually pages first, the root
often last). Faults stay active until reset.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.schemas import FaultType
from app.sim.topology import callers_map

GATEWAY = "api-gateway"
HOP_LAG = (0.3, 1.5)  # seconds per hop, callee -> caller
ALERT_REFIRE_S = 60.0


@dataclass
class FaultSpec:
    root: str
    root_delay: tuple[float, float]  # seconds after injection until the root's onset
    elevated: dict[str, dict[str, float]]  # svc -> metric -> degraded value
    metric_sev: dict[str, str]  # severity of degraded scrapes per svc (default "error")
    fail: dict[str, tuple[float, int]]  # request path svc -> (failure prob, HTTP status)
    onset_logs: dict[str, tuple[str, str]]  # svc -> (severity, message) written at onset
    recurring_logs: dict[str, list[tuple[str, str]]]  # svc -> pool of (severity, message)
    alerts: dict[str, list[tuple[str, str, str]]]  # svc -> [(AlertName, severity, metric for value)]
    root_metric: str  # metric emitted immediately at the root's onset
    change: tuple[str, str, str, str] | None = None  # (svc, version, previous, message) at injection


_PG_FATAL = "FATAL: remaining connection slots are reserved for non-replication superuser connections"
_DB_POOL_SPEC = dict(
    root="postgres",
    elevated={
        "postgres": {"db_pool_in_use_pct": 100.0, "latency_p95_ms": 2400.0, "pg_lock_wait_ms": 310.0},
        "auth-svc": {"error_rate": 0.31, "latency_p95_ms": 2110.0},
        "ledger-svc": {"error_rate": 0.42, "latency_p95_ms": 2050.0},
        "payments-svc": {"error_rate": 0.38, "latency_p95_ms": 2620.0},
        "api-gateway": {"error_rate": 0.22, "latency_p95_ms": 3120.0},
    },
    metric_sev={"postgres": "critical"},
    fail={"auth-svc": (0.7, 503), "payments-svc": (0.75, 503)},
    onset_logs={
        "postgres": ("critical", _PG_FATAL),
        "auth-svc": ("error", "login failed: could not acquire DB connection within 2000ms (pool=auth-pg)"),
        "ledger-svc": ("error", "ledger write failed: timeout acquiring connection from pool (postgres:5432)"),
        "payments-svc": ("error", "POST /v1/payments -> ledger-svc 503: upstream ledger unavailable"),
        "api-gateway": ("error", "upstream timeout: 504 on POST /v1/payments after 3000ms (payments-svc)"),
    },
    recurring_logs={
        "postgres": [
            ("critical", _PG_FATAL),
            ("error", "FATAL: sorry, too many clients already"),
            ("warn", "LOG: 100 of 100 connections in use, 37 idle in transaction"),
        ],
        "auth-svc": [
            ("error", "login failed: could not acquire DB connection within 2000ms (pool=auth-pg)"),
            ("error", "HikariPool-1 - Connection is not available, request timed out after 2000ms"),
        ],
        "ledger-svc": [
            ("error", "ledger write failed: timeout acquiring connection from pool (postgres:5432)"),
            ("error", "sqlalchemy.exc.TimeoutError: QueuePool limit of size 20 overflow 10 reached"),
        ],
        "payments-svc": [
            ("error", "POST /v1/payments -> ledger-svc 503: upstream ledger unavailable"),
            ("error", "payment intent rolled back: ledger commit failed (ledger-svc 503)"),
        ],
        "api-gateway": [
            ("error", "upstream timeout: 504 on POST /v1/payments after 3000ms (payments-svc)"),
            ("warn", "upstream slow: POST /v1/login p95 2.1s (auth-svc)"),
        ],
    },
    alerts={
        "api-gateway": [("GatewayLatencyP95High", "critical", "latency_p95_ms"),
                        ("Gateway5xxRateHigh", "error", "error_rate")],
        "auth-svc": [("LoginFailureRateHigh", "critical", "error_rate"),
                     ("AuthLatencyP95High", "warn", "latency_p95_ms")],
        "payments-svc": [("PaymentFailureRateHigh", "critical", "error_rate"),
                         ("PaymentsLatencyP95High", "warn", "latency_p95_ms")],
        "ledger-svc": [("LedgerWriteErrors", "critical", "error_rate"),
                       ("LedgerLatencyP95High", "warn", "latency_p95_ms")],
        "postgres": [("PgConnectionsSaturated", "critical", "db_pool_in_use_pct"),
                     ("PgQueryLatencyHigh", "error", "latency_p95_ms")],
    },
    root_metric="db_pool_in_use_pct",
)

_NPE = ('java.lang.NullPointerException: Cannot invoke "LedgerEntry.getAmount()" at '
        'PaymentIntentMapper.toLedgerEntry(PaymentIntentMapper.java:88) [v2.3.1]')
_REDIS_REFUSED = "Error: connect ECONNREFUSED 10.0.3.12:6379 (redis-session primary unreachable)"
_AUTH_REDIS = "login failed: session store unavailable: redis-session ECONNREFUSED 10.0.3.12:6379"
_PSP_TIMEOUT = "bank call timed out after 4000ms: POST https://api.hdfc-pg.example/v2/charge (read timeout)"

SPECS: dict[str, FaultSpec] = {
    "db_pool_exhaustion": FaultSpec(root_delay=(0.1, 0.3), **_DB_POOL_SPEC),
    "red_herring": FaultSpec(
        root_delay=(30.0, 30.0),
        change=("ledger-svc", "v4.1.8", "v4.1.7", "ledger-svc v4.1.7 -> v4.1.8 (copy change in settlement emails)"),
        **_DB_POOL_SPEC),
    "bad_deploy": FaultSpec(
        root="payments-svc", root_delay=(5.0, 10.0),
        change=("payments-svc", "v2.3.1", "v2.3.0", "payments-svc v2.3.0 -> v2.3.1 (refactor PaymentIntentMapper)"),
        elevated={"payments-svc": {"error_rate": 0.46, "latency_p95_ms": 640.0},
                  "api-gateway": {"error_rate": 0.19, "latency_p95_ms": 410.0}},
        metric_sev={"payments-svc": "critical"},
        fail={"payments-svc": (0.6, 500)},
        onset_logs={"payments-svc": ("error", _NPE),
                    "api-gateway": ("error", "upstream 500 on POST /v1/payments (payments-svc)")},
        recurring_logs={
            "payments-svc": [("error", _NPE),
                             ("error", "POST /v1/payments 500 Internal Server Error (handler=createPayment, version=v2.3.1)")],
            "api-gateway": [("error", "upstream 500 on POST /v1/payments (payments-svc)")],
        },
        alerts={"api-gateway": [("Gateway5xxRateHigh", "error", "error_rate")],
                "payments-svc": [("Payments5xxRateHigh", "critical", "error_rate"),
                                 ("PaymentFailureRateHigh", "critical", "error_rate")]},
        root_metric="error_rate",
    ),
    "redis_down": FaultSpec(
        root="redis-session", root_delay=(0.1, 0.3),
        elevated={"redis-session": {"error_rate": 1.0, "latency_p95_ms": 1000.0},
                  "auth-svc": {"error_rate": 0.44, "latency_p95_ms": 1150.0},
                  "api-gateway": {"error_rate": 0.05, "latency_p95_ms": 260.0}},
        metric_sev={"redis-session": "critical", "api-gateway": "warn"},
        fail={"auth-svc": (0.6, 503)},
        onset_logs={"redis-session": ("critical", _REDIS_REFUSED),
                    "auth-svc": ("error", _AUTH_REDIS),
                    "api-gateway": ("warn", "upstream 503 on POST /v1/login (auth-svc)")},
        recurring_logs={
            "redis-session": [("critical", _REDIS_REFUSED),
                              ("error", "sentinel: +sdown master redis-session 10.0.3.12 6379")],
            "auth-svc": [("error", _AUTH_REDIS),
                         ("error", "RedisConnectionException: Unable to connect to redis-session:6379")],
            "api-gateway": [("warn", "upstream 503 on POST /v1/login (auth-svc)")],
        },
        alerts={"api-gateway": [("Gateway5xxRateHigh", "warn", "error_rate")],
                "auth-svc": [("LoginFailureRateHigh", "critical", "error_rate")],
                "redis-session": [("RedisDown", "critical", "error_rate")]},
        root_metric="error_rate",
    ),
    "psp_slow": FaultSpec(
        root="psp-adapter", root_delay=(0.1, 0.3),
        elevated={"psp-adapter": {"latency_p95_ms": 4000.0, "error_rate": 0.18},
                  "payments-svc": {"latency_p95_ms": 4350.0, "error_rate": 0.21},
                  "api-gateway": {"latency_p95_ms": 4480.0, "error_rate": 0.09}},
        metric_sev={"psp-adapter": "critical"},
        fail={"payments-svc": (0.3, 504)},
        onset_logs={"psp-adapter": ("error", _PSP_TIMEOUT),
                    "payments-svc": ("error", "psp-adapter call exceeded deadline 4000ms, payment left PENDING"),
                    "api-gateway": ("warn", "upstream slow: POST /v1/payments p95 4.4s (payments-svc)")},
        recurring_logs={
            "psp-adapter": [("error", _PSP_TIMEOUT),
                            ("warn", "bank latency p95 3980ms, retrying with backoff (attempt 2/3)")],
            "payments-svc": [("error", "psp-adapter call exceeded deadline 4000ms, payment left PENDING"),
                             ("error", "POST /v1/payments 504 Gateway Timeout (psp-adapter)")],
            "api-gateway": [("warn", "upstream slow: POST /v1/payments p95 4.4s (payments-svc)")],
        },
        alerts={"api-gateway": [("GatewayLatencyP95High", "critical", "latency_p95_ms")],
                "payments-svc": [("PaymentsLatencyP95High", "error", "latency_p95_ms"),
                                 ("PaymentFailureRateHigh", "critical", "error_rate")],
                "psp-adapter": [("PspLatencyHigh", "critical", "latency_p95_ms"),
                                ("PspTimeoutRateHigh", "error", "error_rate")]},
        root_metric="latency_p95_ms",
    ),
}

# Services each fault is allowed to touch (non-info events). Used by tests.
AFFECTED: dict[str, set[str]] = {k: set(v.elevated) for k, v in SPECS.items()}


@dataclass
class ActiveFault:
    type: FaultType
    start: datetime
    spec: FaultSpec
    onsets: dict[str, datetime]
    scheduled: list[tuple[datetime, dict]] = field(default_factory=list)  # one-shot drafts, sorted
    next_log: dict[str, datetime] = field(default_factory=dict)
    next_refire: dict[str, datetime] = field(default_factory=dict)


def propagate(spec: FaultSpec, root_onset: datetime, rng: random.Random) -> dict[str, datetime]:
    """Onset per affected service: root first, then each caller after a hop lag.
    A caller reached by several paths keeps the earliest arrival."""
    cm = callers_map()
    onsets = {spec.root: root_onset}
    frontier = [spec.root]
    while frontier:
        nxt = []
        for callee in frontier:
            for caller in cm.get(callee, []):
                if caller not in spec.elevated:
                    continue
                t = onsets[callee] + timedelta(seconds=rng.uniform(*HOP_LAG))
                if caller not in onsets or t < onsets[caller]:
                    onsets[caller] = t
                    nxt.append(caller)
        frontier = nxt
    return onsets


def _alert_lag(svc: str, root: str, rng: random.Random) -> float:
    if svc == GATEWAY:
        return rng.uniform(0.3, 0.9)  # the edge pages first
    if svc == root:
        return rng.uniform(4.0, 8.0)  # the real cause often pages late
    return rng.uniform(2.0, 5.0)


def draft(service, kind, severity, name, value=None, message=None, **attrs) -> dict:
    return {"service": service, "kind": kind, "severity": severity, "name": name,
            "value": value, "message": message, "attrs": attrs}


def alert_draft(spec: FaultSpec, svc: str, name: str, sev: str, metric: str) -> dict:
    return draft(svc, "alert", sev, name, spec.elevated[svc].get(metric),
                 message=f"[FIRING] {name} on {svc}")


def build_fault(ftype: FaultType, at: datetime, rng: random.Random) -> ActiveFault:
    spec = SPECS[ftype]
    root = spec.root
    root_onset = at + timedelta(seconds=rng.uniform(*spec.root_delay))
    onsets = propagate(spec, root_onset, rng)
    f = ActiveFault(type=ftype, start=at, spec=spec, onsets=onsets)
    if spec.change:
        svc, ver, prev, msg = spec.change
        f.scheduled.append((at, draft(svc, "change", "info", "deploy", message=msg,
                                      version=ver, previous_version=prev, actor="ci-bot")))
    attrs = {"pool_max": 100} if spec.root_metric == "db_pool_in_use_pct" else {}
    f.scheduled.append((root_onset, draft(root, "metric", "critical", spec.root_metric,
                                          spec.elevated[root][spec.root_metric], **attrs)))
    for svc, on in onsets.items():
        if svc in spec.onset_logs:
            sev, msg = spec.onset_logs[svc]
            t = on if svc != root else on + timedelta(seconds=rng.uniform(0.2, 0.5))
            f.scheduled.append((t, draft(svc, "log", sev, "log", message=msg)))
        f.next_log[svc] = on + timedelta(seconds=rng.uniform(2.0, 4.0))
        t = on + timedelta(seconds=_alert_lag(svc, root, rng))
        for i, (name, sev, metric) in enumerate(spec.alerts.get(svc, [])):
            if i:
                t += timedelta(seconds=rng.uniform(0.5, 2.5))
            f.scheduled.append((t, alert_draft(spec, svc, name, sev, metric)))
            if i == 0:
                f.next_refire[svc] = t + timedelta(seconds=ALERT_REFIRE_S)
    f.scheduled.sort(key=lambda x: x[0])
    return f


def fault_drafts(f: ActiveFault, until: datetime, rng: random.Random) -> list[tuple[datetime, dict]]:
    """All one-shot, recurring-log and alert re-fire drafts of f with ts < until."""
    out = []
    while f.scheduled and f.scheduled[0][0] < until:
        out.append(f.scheduled.pop(0))
    for svc in list(f.next_log):
        pool = f.spec.recurring_logs.get(svc, [])
        while pool and f.next_log[svc] < until:
            sev, msg = pool[rng.randrange(len(pool))]
            out.append((f.next_log[svc], draft(svc, "log", sev, "log", message=msg)))
            f.next_log[svc] += timedelta(seconds=rng.uniform(2.0, 5.0))
    for svc in list(f.next_refire):
        name, sev, metric = f.spec.alerts[svc][0]
        while f.next_refire[svc] < until:
            out.append((f.next_refire[svc], alert_draft(f.spec, svc, name, sev, metric)))
            f.next_refire[svc] += timedelta(seconds=ALERT_REFIRE_S)
    return out


def metric_override(faults: list[ActiveFault], svc: str, metric: str, t: datetime):
    """(degraded value, severity) if an active fault degrades this metric at t, else None."""
    best = None
    for f in faults:
        on = f.onsets.get(svc)
        if on is None or t < on or metric not in f.spec.elevated.get(svc, {}):
            continue
        v = f.spec.elevated[svc][metric]
        if best is None or v > best[0]:
            best = (v, f.spec.metric_sev.get(svc, "error"))
    return best


def failure(faults: list[ActiveFault], svc: str, t: datetime) -> tuple[float, int]:
    """(failure probability, HTTP status) for a request on svc at t."""
    best = (0.0, 200)
    for f in faults:
        on = f.onsets.get(svc)
        if on is None or t < on or svc not in f.spec.fail:
            continue
        if f.spec.fail[svc][0] > best[0]:
            best = f.spec.fail[svc]
    return best
