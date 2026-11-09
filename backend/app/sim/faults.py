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

from app.schemas import Decoy, FaultSpec, FaultType
from app.sim.topology import callers_map, deps_map, service_ids

GATEWAY = "api-gateway"
HOP_LAG = (0.3, 1.5)  # seconds per hop, callee -> caller
ALERT_REFIRE_S = 60.0


@dataclass
class FaultProfile:
    """How one fault looks in telemetry (internal; the contract FaultSpec picks one)."""

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

SPECS: dict[str, FaultProfile] = {
    "db_pool_exhaustion": FaultProfile(root_delay=(0.1, 0.3), **_DB_POOL_SPEC),
    "red_herring": FaultProfile(
        root_delay=(30.0, 30.0),
        change=("ledger-svc", "v4.1.8", "v4.1.7", "ledger-svc v4.1.7 -> v4.1.8 (copy change in settlement emails)"),
        **_DB_POOL_SPEC),
    "bad_deploy": FaultProfile(
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
    "redis_down": FaultProfile(
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
    "psp_slow": FaultProfile(
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
    type: str  # FaultType preset name for live Chaos Lab faults, "custom" for runs
    start: datetime
    spec: FaultProfile
    onsets: dict[str, datetime]
    injected_at: datetime | None = None  # when the fault itself started (decoy delay excluded)
    fspec: FaultSpec | None = None
    scheduled: list[tuple[datetime, dict]] = field(default_factory=list)  # one-shot drafts, sorted
    next_log: dict[str, datetime] = field(default_factory=dict)
    next_refire: dict[str, datetime] = field(default_factory=dict)
    # U5 remediation: set by apply_fix. Per service, metrics ease from degraded back to
    # baseline between heal_start and heal_end; the root first, then its callers.
    fix_id: str | None = None
    fixed_at: datetime | None = None
    heal_start: dict[str, datetime] = field(default_factory=dict)
    heal_end: dict[str, datetime] = field(default_factory=dict)

    def heal_frac(self, svc: str, t: datetime) -> float:
        """0 while fully degraded, rising to 1 when svc is back at baseline."""
        hs = self.heal_start.get(svc)
        if hs is None or t < hs:
            return 0.0
        he = self.heal_end[svc]
        if t >= he:
            return 1.0
        return (t - hs).total_seconds() / max((he - hs).total_seconds(), 1e-6)


def propagate(spec: FaultProfile, root_onset: datetime, rng: random.Random) -> dict[str, datetime]:
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


def alert_draft(spec: FaultProfile, svc: str, name: str, sev: str, metric: str) -> dict:
    return draft(svc, "alert", sev, name, spec.elevated[svc].get(metric),
                 message=f"[FIRING] {name} on {svc}")


# ---------------------------------------------------------------- U1 fault primitives
# The contract FaultSpec (target, kind, onset_jitter_s, decoy, seed) picks a profile.
# The 5 Chaos Lab buttons are presets of FaultSpec; their (target, kind) pairs reuse the
# hand-written profiles above so the hero demo keeps its exact wording and timing.
APP_SERVICES = ("auth-svc", "payments-svc", "ledger-svc")
ALL_KINDS = ("pool_exhaustion", "latency", "error_spike", "down", "bad_deploy")
# Baseline p95 per service, mirrored from generator.BASE_LAT (generator imports this module).
_BASE_LAT = {"api-gateway": 120, "auth-svc": 45, "payments-svc": 180, "ledger-svc": 60,
             "redis-session": 2, "postgres": 8, "psp-adapter": 350}


def valid_kinds(target: str) -> list[str]:
    """Kinds that make sense for target: pool_exhaustion only on postgres,
    bad_deploy only on app services, latency / error_spike / down anywhere."""
    out = ["latency", "error_spike", "down"]
    if target == "postgres":
        out.insert(0, "pool_exhaustion")
    if target in APP_SERVICES:
        out.append("bad_deploy")
    return out


PRESETS: dict[str, FaultSpec] = {
    "db_pool_exhaustion": FaultSpec(target="postgres", kind="pool_exhaustion"),
    "bad_deploy": FaultSpec(target="payments-svc", kind="bad_deploy"),
    "redis_down": FaultSpec(target="redis-session", kind="down"),
    "red_herring": FaultSpec(target="postgres", kind="pool_exhaustion",
                             decoy=Decoy(service="ledger-svc", offset_s=-30.0)),
    "psp_slow": FaultSpec(target="psp-adapter", kind="latency"),
}

_PRESET_PROFILES = {
    ("postgres", "pool_exhaustion"): SPECS["db_pool_exhaustion"],
    ("payments-svc", "bad_deploy"): SPECS["bad_deploy"],
    ("redis-session", "down"): SPECS["redis_down"],
    ("psp-adapter", "latency"): SPECS["psp_slow"],
}

# Decoy deploys: (previous, new version, what changed). Harmless by construction.
_DECOY_DEPLOYS = {
    "ledger-svc": ("v4.1.7", "v4.1.8", "copy change in settlement emails"),
    "api-gateway": ("v1.14.2", "v1.14.3", "update CORS allow-list"),
    "auth-svc": ("v3.2.0", "v3.2.1", "bump i18n strings for login page"),
    "payments-svc": ("v2.3.0", "v2.3.0-hotfix1", "add log field for merchant category"),
    "redis-session": ("7.2.4", "7.2.5", "minor patch, config unchanged"),
    "postgres": ("15.6", "15.6-cfg2", "raise log_min_duration_statement to 500ms"),
    "psp-adapter": ("v0.9.4", "v0.9.5", "rotate bank sandbox certificate"),
}
_BAD_DEPLOYS = {
    "auth-svc": ("v3.2.0", "v3.3.0", "migrate token signer"),
    "payments-svc": ("v2.3.0", "v2.3.1", "refactor PaymentIntentMapper"),
    "ledger-svc": ("v4.1.7", "v4.2.0", "new settlement writer"),
}


def _camel(svc: str) -> str:
    return "".join(p.capitalize() for p in svc.split("-"))


def _affected(target: str) -> list[str]:
    """target, then its transitive callers (breadth first, topology order)."""
    cm = callers_map()
    out, frontier = [target], [target]
    while frontier:
        nxt = []
        for s in frontier:
            for c in cm.get(s, []):
                if c not in out:
                    out.append(c)
                    nxt.append(c)
        frontier = nxt
    return out


def _callee_in(svc: str, affected: list[str]) -> str:
    for d in deps_map().get(svc, []):
        if d in affected:
            return d
    return affected[0]


def profile_for(target: str, kind: str) -> FaultProfile:
    """Telemetry profile for a (target, kind) primitive. Preset pairs reuse the
    hand-written profiles; anything else is generated from the topology."""
    if kind not in valid_kinds(target):
        raise ValueError(f"fault kind {kind} is not valid for {target}")
    if (target, kind) in _PRESET_PROFILES:
        return _PRESET_PROFILES[(target, kind)]
    aff = _affected(target)
    cam = _camel(target)
    base = _BASE_LAT.get(target, 100)
    elevated, metric_sev, fail, onset_logs, recurring, alerts = {}, {}, {}, {}, {}, {}
    change = None
    root_delay = (0.1, 0.3)
    if kind == "latency":
        root_lat = max(800.0, base * 12)
        elevated[target] = {"latency_p95_ms": root_lat, "error_rate": 0.08}
        root_metric = "latency_p95_ms"
        root_log = ("error", f"{target}: request exceeded 3000ms, worker pool saturated (p95 {root_lat:.0f}ms)")
        alerts[target] = [(f"{cam}LatencyP95High", "critical", "latency_p95_ms")]
        root_fail = (0.3, 504)
    elif kind == "error_spike":
        elevated[target] = {"error_rate": 0.45, "latency_p95_ms": base * 2.5}
        root_metric = "error_rate"
        root_log = ("error", f"{target}: unhandled exception in request handler, 500 Internal Server Error")
        alerts[target] = [(f"{cam}5xxRateHigh", "critical", "error_rate")]
        root_fail = (0.5, 500)
    elif kind == "down":
        elevated[target] = {"error_rate": 1.0, "latency_p95_ms": 1000.0}
        root_metric = "error_rate"
        root_log = ("critical", f"connect ECONNREFUSED {target}:443 ({target} unreachable, health check failing)")
        alerts[target] = [(f"{cam}Down", "critical", "error_rate")]
        root_fail = (0.9, 503)
    else:  # bad_deploy on an app service
        prev, ver, what = _BAD_DEPLOYS[target]
        change = (target, ver, prev, f"{target} {prev} -> {ver} ({what})")
        root_delay = (5.0, 10.0)
        elevated[target] = {"error_rate": 0.46, "latency_p95_ms": base * 3}
        root_metric = "error_rate"
        root_log = ("error", f"{target}: NullPointerException in request handler after deploy [{ver}]")
        alerts[target] = [(f"{cam}5xxRateHigh", "critical", "error_rate")]
        root_fail = (0.6, 500)
    metric_sev[target] = "critical"
    onset_logs[target] = root_log
    recurring[target] = [root_log]
    if target in ("auth-svc", "payments-svc"):
        fail[target] = root_fail
    for svc in aff[1:]:
        callee = _callee_in(svc, aff)
        b = _BASE_LAT.get(svc, 100)
        c = _camel(svc)
        if kind == "latency":
            elevated[svc] = {"latency_p95_ms": b + elevated[target]["latency_p95_ms"] * 0.9, "error_rate": 0.07}
            msg = ("warn", f"call to {callee} exceeded deadline 3000ms")
            alerts[svc] = [(f"{c}LatencyP95High", "error", "latency_p95_ms")]
            fail_svc = (0.25, 504)
        else:
            err = 0.4 if kind == "down" else 0.25
            elevated[svc] = {"error_rate": err, "latency_p95_ms": b * 2.5}
            word = "connection refused" if kind == "down" else "returned 500"
            msg = ("error", f"upstream {callee} {word}, request failed")
            alerts[svc] = [(f"{c}5xxRateHigh", "error", "error_rate")]
            fail_svc = (0.5 if kind == "down" else 0.35, 503)
        metric_sev[svc] = "error"
        onset_logs[svc] = msg
        recurring[svc] = [msg]
        if svc in ("auth-svc", "payments-svc"):
            fail[svc] = fail_svc
    return FaultProfile(root=target, root_delay=root_delay, elevated=elevated, metric_sev=metric_sev,
                        fail=fail, onset_logs=onset_logs, recurring_logs=recurring, alerts=alerts,
                        root_metric=root_metric, change=change)


def affected_services(target: str, kind: str) -> set[str]:
    return set(profile_for(target, kind).elevated)


def validate_spec(spec: FaultSpec) -> None:
    """Raise ValueError if the spec cannot be simulated."""
    if spec.target not in service_ids():
        raise ValueError(f"unknown target {spec.target}")
    if spec.kind not in valid_kinds(spec.target):
        raise ValueError(f"fault kind {spec.kind} is not valid for {spec.target}; valid: {valid_kinds(spec.target)}")
    if spec.decoy is not None:
        if spec.decoy.service not in service_ids():
            raise ValueError(f"unknown decoy service {spec.decoy.service}")
        if spec.decoy.service == spec.target:
            raise ValueError("decoy must be on a different service than the target")
        if not -120.0 <= spec.decoy.offset_s <= 60.0:
            raise ValueError("decoy offset_s must be within -120..60")
    if not 0.0 <= spec.onset_jitter_s <= 60.0:
        raise ValueError("onset_jitter_s must be within 0..60")


def build_fault(ftype: FaultType, at: datetime, rng: random.Random) -> ActiveFault:
    """A Chaos Lab preset, injected live at `at`."""
    return build_spec_fault(PRESETS[ftype], at, rng, label=ftype)


def build_spec_fault(fs: FaultSpec, at: datetime, rng: random.Random, label: str = "custom",
                     past_ok: bool = False, not_before: datetime | None = None) -> ActiveFault:
    """Schedule a FaultSpec injected at `at`. The decoy deploy lands at root onset +
    decoy.offset_s. Live (past_ok False) a negative offset delays the root instead, so
    the decoy is not written into the past; offline the decoy may precede `at`."""
    spec = profile_for(fs.target, fs.kind)
    root = spec.root
    delay = rng.uniform(*spec.root_delay)
    real_delay = delay
    decoy = fs.decoy
    if decoy is not None and decoy.offset_s < 0 and not past_ok:
        real_delay = max(delay, -decoy.offset_s)
    root_onset = at + timedelta(seconds=real_delay)
    onsets = propagate(spec, root_onset, rng)
    f = ActiveFault(type=label, start=at, spec=spec, onsets=onsets,
                    injected_at=root_onset - timedelta(seconds=delay), fspec=fs)
    if decoy is not None:
        prev, ver, what = _DECOY_DEPLOYS.get(decoy.service, ("v1.0.0", "v1.0.1", "config tweak"))
        t = root_onset + timedelta(seconds=decoy.offset_s)
        floor = not_before if past_ok else at
        if floor is not None and t < floor:
            t = floor
        f.scheduled.append((t, draft(decoy.service, "change", "info", "deploy",
                                     message=f"{decoy.service} {prev} -> {ver} ({what})",
                                     version=ver, previous_version=prev, actor="ci-bot")))
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
        stop = min(until, f.heal_start.get(svc, until))  # a remediated service stops erroring
        while pool and f.next_log[svc] < stop:
            sev, msg = pool[rng.randrange(len(pool))]
            out.append((f.next_log[svc], draft(svc, "log", sev, "log", message=msg)))
            f.next_log[svc] += timedelta(seconds=rng.uniform(2.0, 5.0))
    for svc in list(f.next_refire):
        name, sev, metric = f.spec.alerts[svc][0]
        stop = min(until, f.heal_start.get(svc, until))
        while f.next_refire[svc] < stop:
            out.append((f.next_refire[svc], alert_draft(f.spec, svc, name, sev, metric)))
            f.next_refire[svc] += timedelta(seconds=ALERT_REFIRE_S)
    return out


# Which fix remediates which fault (U5). A fix that does not match the fault changes nothing.
def fix_matches(fs: FaultSpec, fix_id: str) -> bool:
    if fix_id == "raise_pool_postgres":
        return fs.kind == "pool_exhaustion"
    if fix_id == "rollback_deploy":
        return fs.kind == "bad_deploy"
    if fix_id == "circuit_breaker_psp":
        return fs.target == "psp-adapter"
    if fix_id == "restart_service":
        return fs.kind in ("down", "latency", "error_spike") and fs.target != "psp-adapter"
    return False


ROOT_HEAL_S = 10.0  # root metrics ease back to baseline over this long
CALLER_HEAL_S = 2.0


def apply_fix(f: ActiveFault, fix_id: str, at: datetime, rng: random.Random) -> None:
    """Remediate f at `at`: emit a `fix_applied` change on the root, ease the root back to
    baseline over ROOT_HEAL_S, then each caller after a hop lag. Pending one-shot fault
    drafts (late alerts, onset logs) of a service that is already healing are dropped."""
    root = f.spec.root
    f.fix_id, f.fixed_at = fix_id, at
    f.heal_start[root] = at
    f.heal_end[root] = at + timedelta(seconds=ROOT_HEAL_S)
    cm = callers_map()
    frontier = [root]
    while frontier:
        nxt = []
        for callee in frontier:
            for caller in cm.get(callee, []):
                if caller not in f.onsets or caller in f.heal_start:
                    continue
                hs = f.heal_end[callee] + timedelta(seconds=rng.uniform(*HOP_LAG))
                f.heal_start[caller] = hs
                f.heal_end[caller] = hs + timedelta(seconds=CALLER_HEAL_S)
                nxt.append(caller)
        frontier = nxt
    f.scheduled = [(t, d) for t, d in f.scheduled
                   if d["kind"] == "change" or t < f.heal_start.get(d["service"], t + timedelta(days=1))]
    f.scheduled.append((at, draft(root, "change", "info", "fix_applied",
                                  message=f"remediation {fix_id} applied to {root}", fix_id=fix_id,
                                  actor="jarvis-operator")))
    f.scheduled.sort(key=lambda x: x[0])


def metric_override(faults: list[ActiveFault], svc: str, metric: str, t: datetime):
    """(degraded value, severity, heal fraction) if an active fault degrades this metric
    at t, else None. heal fraction > 0 means the value is easing back to baseline."""
    best = None
    for f in faults:
        on = f.onsets.get(svc)
        if on is None or t < on or metric not in f.spec.elevated.get(svc, {}):
            continue
        frac = f.heal_frac(svc, t)
        if frac >= 1.0:
            continue
        v = f.spec.elevated[svc][metric]
        if best is None or v > best[0]:
            best = (v, f.spec.metric_sev.get(svc, "error"), frac)
    return best


def failure(faults: list[ActiveFault], svc: str, t: datetime) -> tuple[float, int]:
    """(failure probability, HTTP status) for a request on svc at t."""
    best = (0.0, 200)
    for f in faults:
        on = f.onsets.get(svc)
        if on is None or t < on or svc not in f.spec.fail:
            continue
        p, code = f.spec.fail[svc]
        p *= 1.0 - f.heal_frac(svc, t)
        if p > best[0]:
            best = (p, code)
    return best
