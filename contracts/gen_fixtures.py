"""Generates contracts/fixtures/*.json from two hand-designed scenarios.

Run:  python contracts/gen_fixtures.py   (from repo root)

Fixtures are the shared target for every lane:
  * Lane B can run analyze(events_*.json, topology) and compare with incident_*.json
  * Lane C renders incident_*.json / incidents_list.json / report_db_pool.json before the API exists
  * Lane D grounds report sentences against incident.evidence_events

Scores below are computed with the CLAUDE.md formula (weights, victim penalty,
abstain rule) from hand-set onsets and peak z-scores, so the numbers are
self-consistent. This is a fixture builder, not the engine.
"""
from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from contracts.schemas import (  # noqa: E402
    ABSTAIN_MIN_MARGIN, ABSTAIN_MIN_SCORE, ENGINE_WEIGHTS, VICTIM_PENALTY,
    Event, Incident, IncidentSummary, Report, Topology, TopologyState,
)

OUT = ROOT / "contracts" / "fixtures"
rng = random.Random(42)

# ---------------------------------------------------------------- topology
TOPOLOGY = {
    "services": [
        {"id": "api-gateway", "label": "API Gateway", "tier": "edge"},
        {"id": "auth-svc", "label": "Auth Service", "tier": "app"},
        {"id": "payments-svc", "label": "Payments Service", "tier": "app"},
        {"id": "ledger-svc", "label": "Ledger Service", "tier": "app"},
        {"id": "redis-session", "label": "Redis (sessions)", "tier": "data"},
        {"id": "postgres", "label": "PostgreSQL", "tier": "data"},
        {"id": "psp-adapter", "label": "PSP Adapter (bank)", "tier": "external"},
    ],
    "edges": [
        {"from": "api-gateway", "to": "auth-svc"},
        {"from": "api-gateway", "to": "payments-svc"},
        {"from": "auth-svc", "to": "redis-session"},
        {"from": "auth-svc", "to": "postgres"},
        {"from": "payments-svc", "to": "ledger-svc"},
        {"from": "payments-svc", "to": "psp-adapter"},
        {"from": "ledger-svc", "to": "postgres"},
    ],
}
DEPS: dict[str, list[str]] = {}
for e in TOPOLOGY["edges"]:
    DEPS.setdefault(e["from"], []).append(e["to"])
TIER = {s["id"]: s["tier"] for s in TOPOLOGY["services"]}

BASE_LAT = {"api-gateway": 120, "auth-svc": 45, "payments-svc": 180, "ledger-svc": 60,
            "redis-session": 2, "postgres": 8, "psp-adapter": 350}
BASE_ERR = {"api-gateway": 0.004, "auth-svc": 0.003, "payments-svc": 0.006, "ledger-svc": 0.002,
            "redis-session": 0.0, "postgres": 0.0, "psp-adapter": 0.01}


def callers_closure(svc: str) -> set[str]:
    """Services that transitively depend on svc."""
    out, stack = set(), [svc]
    while stack:
        cur = stack.pop()
        for caller, deps in DEPS.items():
            if cur in deps and caller not in out:
                out.add(caller)
                stack.append(caller)
    return out


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


class Stream:
    def __init__(self, start_id: int):
        self.n = start_id
        self.events: list[dict] = []

    def add(self, t, service, kind, severity, name, value=None, message=None, **attrs) -> str:
        eid = f"evt_{self.n:06d}"
        self.n += 1
        ev = {"id": eid, "ts": iso(t), "source": "sim", "service": service, "kind": kind,
              "severity": severity, "name": name}
        if value is not None:
            ev["value"] = round(float(value), 4)
        if message is not None:
            ev["message"] = message
        ev["attrs"] = attrs
        self.events.append(ev)
        return eid

    def sorted(self):
        return sorted(self.events, key=lambda e: (e["ts"], e["id"]))


def baseline(s: Stream, t0: datetime, seconds: int, onset: dict[str, float] | None = None,
             elevated: dict[str, dict[str, float]] | None = None):
    """Steady metrics every 2 s per service, staggered phase. After a service's
    onset (seconds from t0), its `elevated` metrics keep reporting the degraded
    value (+-8% noise) so the anomaly is sustained, as a real scrape would be."""
    onset, elevated = onset or {}, elevated or {}
    for i, svc in enumerate(BASE_LAT):
        phase = 0.17 * i
        k = 0
        while 2 * k + phase < seconds:
            off = 2 * k + phase
            k += 1
            t = t0 + timedelta(seconds=off)
            bad = svc in onset and off > onset[svc] + 0.5
            vals = {"latency_p95_ms": BASE_LAT[svc] * rng.uniform(0.9, 1.12),
                    "error_rate": max(0.0, BASE_ERR[svc] * rng.uniform(0.5, 1.5))}
            if svc == "postgres":
                vals["db_pool_in_use_pct"] = rng.uniform(34, 48)
                vals["pg_lock_wait_ms"] = rng.uniform(1.5, 4.5)
            for name, v in vals.items():
                sev = "info"
                if bad and name in elevated.get(svc, {}):
                    v, sev = elevated[svc][name] * rng.uniform(0.92, 1.08), "error"
                    if name == "db_pool_in_use_pct":
                        v = 100.0
                attrs = {"pool_max": 100} if name == "db_pool_in_use_pct" else {}
                s.add(t, svc, "metric", sev, name, v, **attrs)


def requests(s: Stream, t0: datetime, seconds: int, fail_from: dict[str, float] | None = None, rate=2.0):
    """Synthetic user traffic: logins on auth-svc, payments on payments-svc.
    From fail_from[svc] seconds onward, requests on svc fail with 503
    (70% of logins, 75% of payments)."""
    fail_from = fail_from or {}
    t, users = 0.0, [f"u_{n}" for n in range(1000, 1600)]
    while t < seconds:
        t += rng.expovariate(rate)
        ts = t0 + timedelta(seconds=t)
        user = rng.choice(users)
        if rng.random() < 0.4:
            bad = t >= fail_from.get("auth-svc", 1e9) and rng.random() < 0.7
            s.add(ts, "auth-svc", "request", "error" if bad else "info", "http_request",
                  message=f"POST /v1/login {503 if bad else 200}", user_id=user,
                  status=503 if bad else 200, route="/v1/login")
        else:
            amt = rng.choice([99, 149, 199, 299, 499, 799, 999, 1299, 2499, 4999])
            bad = t >= fail_from.get("payments-svc", 1e9) and rng.random() < 0.75
            s.add(ts, "payments-svc", "request", "error" if bad else "info", "http_request",
                  message=f"POST /v1/payments {503 if bad else 201}", user_id=user,
                  status=503 if bad else 201, route="/v1/payments", amount_inr=amt,
                  order_id=f"ord_{rng.randint(100000, 999999)}")


# ---------------------------------------------------------------- scoring
def score(anom: dict, changes: dict) -> list[dict]:
    """anom[svc] = {"onset": dt, "onset_ev": id, "z": float, "peak_ev": id}
    changes[svc] = change event id within 10 min before onset."""
    names = list(anom)
    zmax = max(a["z"] for a in anom.values())
    out = []
    for svc in names:
        others = [o for o in names if o != svc]
        dependents = [o for o in others if o in callers_closure(svc)]
        up = len(dependents) / len(others) if others else 0.0
        later = [d for d in dependents if anom[d]["onset"] > anom[svc]["onset"]]
        prec = len(later) / len(dependents) if dependents else 0.0
        chg = 1.0 if svc in changes else 0.0
        sev = anom[svc]["z"] / zmax
        comps = {"upstream": round(ENGINE_WEIGHTS["upstream"] * up, 3),
                 "precedence": round(ENGINE_WEIGHTS["precedence"] * prec, 3),
                 "change": round(ENGINE_WEIGHTS["change"] * chg, 3),
                 "severity": round(ENGINE_WEIGHTS["severity"] * sev, 3)}
        anomalous_deps = [d for d in DEPS.get(svc, []) if d in anom]
        penalty = VICTIM_PENALTY if anomalous_deps else 1.0
        ce = {"upstream": [anom[d]["onset_ev"] for d in dependents],
              "precedence": [anom[svc]["onset_ev"]] + [anom[d]["onset_ev"] for d in later],
              "change": [changes[svc]] if svc in changes else [],
              "severity": [anom[svc]["peak_ev"]]}
        evidence = list(dict.fromkeys(x for v in ce.values() for x in v))
        out.append({"service": svc, "score": round(sum(comps.values()) * penalty, 3),
                    "components": comps, "penalty": penalty,
                    "victim_of": anomalous_deps[0] if anomalous_deps else None,
                    "onset": iso(anom[svc]["onset"]), "signature": anom[svc]["sig"],
                    "evidence": evidence, "component_evidence": ce})
    out.sort(key=lambda c: -c["score"])
    return out


def timeline(anom: dict, what: dict) -> list[dict]:
    rows = []
    for svc, a in sorted(anom.items(), key=lambda kv: kv[1]["onset"]):
        earlier = [d for d in DEPS.get(svc, []) if d in anom and anom[d]["onset"] < a["onset"]]
        cause = max(earlier, key=lambda d: anom[d]["onset"]) if earlier else None
        rows.append({"ts": iso(a["onset"]), "service": svc, "what": what[svc],
                     "event_ids": [a["onset_ev"]] + ([a["peak_ev"]] if a["peak_ev"] != a["onset_ev"] else []),
                     "caused_by": cause})
    return rows


def impact(events: list[dict], anom: dict, t_from: datetime) -> dict:
    lo = iso(t_from)
    failed = [e for e in events if e["kind"] == "request" and e["attrs"].get("status", 200) >= 500 and e["ts"] >= lo]
    pay = [e for e in failed if e["service"] == "payments-svc"]
    svcs = set(anom)
    for s in list(svcs):
        svcs |= callers_closure(s)
    order = sorted(svcs, key=lambda s: anom[s]["onset"] if s in anom else datetime.max.replace(tzinfo=timezone.utc))
    return {"services": order,
            "users_affected": len({e["attrs"]["user_id"] for e in failed}),
            "failed_payments": len(pay),
            "failed_value_inr": float(sum(e["attrs"]["amount_inr"] for e in pay))}


def collect_ids(inc: dict) -> list[str]:
    ids = []
    if inc["root_cause"]:
        ids += inc["root_cause"]["evidence"]
    for c in inc["candidates"]:
        ids += c["evidence"]
    for t in inc["timeline"]:
        ids += t["event_ids"]
    for r in inc["recommendations"]:
        ids += r["evidence"]
    return list(dict.fromkeys(ids))


def build_incident(inc_id, title, events, anom, changes, what, recs_fn, t_from, disamb=None):
    cands = score(anom, changes)
    top1, top2 = cands[0], cands[1] if len(cands) > 1 else None
    margin = top1["score"] - (top2["score"] if top2 else 0.0)
    abstain = margin < ABSTAIN_MIN_MARGIN or top1["score"] < ABSTAIN_MIN_SCORE
    reasons = []
    if top2 and margin < ABSTAIN_MIN_MARGIN:
        reasons.append(f"top two candidates differ by {margin:.3f} (< {ABSTAIN_MIN_MARGIN})")
    if top1["score"] < ABSTAIN_MIN_SCORE:
        reasons.append(f"top score {top1['score']:.3f} is below {ABSTAIN_MIN_SCORE}")
    alerts = [e for e in events if e["kind"] == "alert"]
    by_id = {e["id"]: e for e in events}
    inc = {
        "id": inc_id, "title": title,
        "status": "ABSTAINED" if abstain else "OPEN",
        "opened_at": iso(min(a["onset"] for a in anom.values())),
        "updated_at": max(e["ts"] for e in events),
        "services": [c for c in sorted(anom, key=lambda s: anom[s]["onset"])],
        "alert_count": len(alerts),
        "severity": "critical",
        "root_cause": None if abstain else {"service": top1["service"], "signature": top1["signature"],
                                             "confidence": top1["score"], "evidence": top1["evidence"]},
        "candidates": cands,
        "abstain_reason": "; ".join(reasons) if abstain else None,
        "disambiguation": (disamb or []) if abstain else [],
        "impact": impact(events, anom, t_from),
        "timeline": timeline(anom, what),
        "recommendations": recs_fn(),
        "evidence_events": [],
    }
    inc["evidence_events"] = [by_id[i] for i in collect_ids(inc)]
    return Incident.model_validate(inc).model_dump(mode="json", by_alias=True)


# ================================================================ scenario 1: db pool exhaustion (HERO)
def scenario_db_pool():
    s = Stream(1)
    T = datetime(2026, 11, 9, 8, 32, 5, tzinfo=timezone.utc)  # 14:02:05 IST
    W = T - timedelta(seconds=40)
    lag = {"postgres": 0.12, "auth-svc": 1.21, "ledger-svc": 1.53, "payments-svc": 2.38, "api-gateway": 3.30}
    baseline(s, W, 80, onset={k: 40 + v for k, v in lag.items()}, elevated={
        "postgres": {"db_pool_in_use_pct": 100.0, "latency_p95_ms": 2400.0},
        "auth-svc": {"error_rate": 0.31, "latency_p95_ms": 2110.0},
        "ledger-svc": {"error_rate": 0.42, "latency_p95_ms": 2050.0},
        "payments-svc": {"error_rate": 0.38, "latency_p95_ms": 2620.0},
        "api-gateway": {"error_rate": 0.22, "latency_p95_ms": 3120.0}})
    requests(s, W, 80, fail_from={"auth-svc": 40 + 1.25, "payments-svc": 40 + 2.45})
    at = lambda sec: T + timedelta(seconds=sec)  # noqa: E731
    A = {}
    # postgres: pool saturated, connections refused
    p_on = s.add(at(0.12), "postgres", "metric", "critical", "db_pool_in_use_pct", 100.0, pool_max=100)
    p_log = s.add(at(0.48), "postgres", "log", "critical", "log",
                  message="FATAL: remaining connection slots are reserved for non-replication superuser connections")
    p_lat = s.add(at(0.90), "postgres", "metric", "critical", "latency_p95_ms", 2400.0)
    A["postgres"] = {"onset": at(0.12), "onset_ev": p_on, "peak_ev": p_log, "z": 41.0, "sig": "connection_pool_exhausted"}
    # auth-svc
    a_on = s.add(at(1.21), "auth-svc", "log", "error", "log",
                 message="login failed: could not acquire DB connection within 2000ms (pool=auth-pg)", user_id="u_1187")
    a_err = s.add(at(1.40), "auth-svc", "metric", "error", "error_rate", 0.31)
    s.add(at(1.40), "auth-svc", "metric", "warn", "latency_p95_ms", 2110.0)
    A["auth-svc"] = {"onset": at(1.21), "onset_ev": a_on, "peak_ev": a_err, "z": 27.5, "sig": "error_spike"}
    # ledger-svc
    l_on = s.add(at(1.53), "ledger-svc", "log", "error", "log",
                 message="ledger write failed: timeout acquiring connection from pool (postgres:5432)")
    l_err = s.add(at(1.70), "ledger-svc", "metric", "critical", "error_rate", 0.42)
    s.add(at(1.70), "ledger-svc", "metric", "warn", "latency_p95_ms", 2050.0)
    A["ledger-svc"] = {"onset": at(1.53), "onset_ev": l_on, "peak_ev": l_err, "z": 30.2, "sig": "error_spike"}
    # payments-svc
    pm_on = s.add(at(2.38), "payments-svc", "log", "error", "log",
                  message="POST /v1/payments -> ledger-svc 503: upstream ledger unavailable", order_id="ord_558201")
    pm_err = s.add(at(2.41), "payments-svc", "metric", "critical", "error_rate", 0.38)
    s.add(at(2.41), "payments-svc", "metric", "warn", "latency_p95_ms", 2620.0)
    A["payments-svc"] = {"onset": at(2.38), "onset_ev": pm_on, "peak_ev": pm_err, "z": 33.0, "sig": "error_spike"}
    # api-gateway
    g_on = s.add(at(3.30), "api-gateway", "metric", "critical", "latency_p95_ms", 3120.0)
    s.add(at(3.30), "api-gateway", "metric", "error", "error_rate", 0.22)
    A["api-gateway"] = {"onset": at(3.30), "onset_ev": g_on, "peak_ev": g_on, "z": 20.5, "sig": "latency_spike"}
    # 14 alerts, deliberately NOT in causal order (gateway pages first, as in real life)
    for sec, svc, name, sev, val in [
        (3.6, "api-gateway", "GatewayLatencyP95High", "critical", 3120.0),
        (4.1, "auth-svc", "LoginFailureRateHigh", "critical", 0.31),
        (4.4, "payments-svc", "PaymentFailureRateHigh", "critical", 0.38),
        (4.9, "api-gateway", "Gateway5xxRateHigh", "error", 0.22),
        (5.2, "ledger-svc", "LedgerWriteErrors", "critical", 0.42),
        (5.8, "auth-svc", "AuthLatencyP95High", "warn", 2110.0),
        (6.3, "postgres", "PgConnectionsSaturated", "critical", 100.0),
        (6.9, "payments-svc", "PaymentsLatencyP95High", "warn", 2620.0),
        (7.4, "ledger-svc", "LedgerLatencyP95High", "warn", 2050.0),
        (8.0, "postgres", "PgQueryLatencyHigh", "error", 2400.0),
        (9.5, "api-gateway", "GatewaySLOBurnRate", "critical", 14.2),
        (11.0, "payments-svc", "PaymentSuccessRateLow", "critical", 0.62),
        (12.6, "auth-svc", "LoginFailureRateHigh", "critical", 0.35),
        (14.1, "postgres", "PgConnectionsSaturated", "critical", 100.0),
    ]:
        s.add(at(sec), svc, "alert", sev, name, val, message=f"[FIRING] {name} on {svc}")
    events = s.sorted()
    what = {"postgres": "connection pool at 100% (baseline ~41%), clients refused",
            "auth-svc": "login error_rate 0.31 (baseline 0.003): cannot get DB connection",
            "ledger-svc": "write error_rate 0.42 (baseline 0.002): pool timeout to postgres",
            "payments-svc": "payment error_rate 0.38 (baseline 0.006): ledger-svc returns 503",
            "api-gateway": "p95 latency 3120 ms (baseline ~120 ms)"}

    def recs():
        return [
            {"action": "Free postgres connections: terminate idle-in-transaction sessions and raise max_connections headroom",
             "why": "postgres pool hit 100% and is refusing new clients before any dependent service failed",
             "evidence": [p_on, p_log],
             "runbook": "psql -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle in transaction' AND state_change < now() - interval '30 seconds';\" then check pool usage drops below 80%. Raise max_connections or PgBouncer pool_size only if usage stays high."},
            {"action": "Cap pool size per client in auth-svc and ledger-svc and fail fast with a 2 s acquire timeout",
             "why": "auth-svc and ledger-svc both block on pool acquisition, which amplifies the outage into the gateway",
             "evidence": [a_on, l_on],
             "runbook": "Set DB_POOL_MAX=20 and DB_ACQUIRE_TIMEOUT_MS=2000 in auth-svc and ledger-svc config, rolling restart one replica at a time."},
            {"action": "Do not roll back payments-svc or api-gateway: they are victims",
             "why": "their errors start after postgres saturation and their log lines name the ledger or postgres dependency",
             "evidence": [pm_on, g_on],
             "runbook": "Hold any rollback of payments-svc and api-gateway. Re-check their error_rate after postgres pool usage recovers."},
        ]

    inc = build_incident("inc_1", "postgres: connection_pool_exhausted", events, A, {}, what, recs, T)
    return events, inc, {"p_on": p_on, "p_log": p_log, "p_lat": p_lat, "a_on": a_on, "l_on": l_on,
                         "pm_on": pm_on, "pm_err": pm_err, "g_on": g_on, "a_err": a_err, "l_err": l_err}, s.n


# ================================================================ scenario 2: ambiguous (ABSTAINED)
def scenario_ambiguous(start_id: int):
    s = Stream(start_id)
    D = datetime(2026, 11, 9, 8, 50, 0, tzinfo=timezone.utc)  # 14:20:00 IST deploy
    W = D - timedelta(seconds=20)
    lag = {"ledger-svc": 31.0, "payments-svc": 31.9, "api-gateway": 32.8, "postgres": 34.2}
    baseline(s, W, 80, onset={k: 20 + v for k, v in lag.items()}, elevated={
        "ledger-svc": {"error_rate": 0.47},
        "payments-svc": {"error_rate": 0.29},
        "api-gateway": {"latency_p95_ms": 1480.0},
        "postgres": {"pg_lock_wait_ms": 870.0}})
    requests(s, W, 80, fail_from={"payments-svc": 20 + 31.95})
    at = lambda sec: D + timedelta(seconds=sec)  # noqa: E731
    A = {}
    dep = s.add(at(0.0), "ledger-svc", "change", "info", "deploy", message="ledger-svc v4.1.9 -> v4.2.0 (migration 0042_add_settlement_idx)",
                version="v4.2.0", previous_version="v4.1.9", actor="ci-bot")
    l_on = s.add(at(31.0), "ledger-svc", "log", "error", "log",
                 message="ledger write failed: canceling statement due to lock timeout (relation ledger_entries)")
    l_err = s.add(at(31.2), "ledger-svc", "metric", "critical", "error_rate", 0.47)
    A["ledger-svc"] = {"onset": at(31.0), "onset_ev": l_on, "peak_ev": l_err, "z": 36.0, "sig": "error_spike_after_deploy"}
    pm_on = s.add(at(31.9), "payments-svc", "metric", "error", "error_rate", 0.29)
    A["payments-svc"] = {"onset": at(31.9), "onset_ev": pm_on, "peak_ev": pm_on, "z": 28.8, "sig": "error_spike"}
    g_on = s.add(at(32.8), "api-gateway", "metric", "error", "latency_p95_ms", 1480.0)
    A["api-gateway"] = {"onset": at(32.8), "onset_ev": g_on, "peak_ev": g_on, "z": 18.0, "sig": "latency_spike"}
    p_on = s.add(at(34.2), "postgres", "metric", "warn", "pg_lock_wait_ms", 870.0)
    A["postgres"] = {"onset": at(34.2), "onset_ev": p_on, "peak_ev": p_on, "z": 10.8, "sig": "latency_spike"}
    for sec, svc, name, sev, val in [
        (33.5, "payments-svc", "PaymentFailureRateHigh", "critical", 0.29),
        (34.0, "api-gateway", "GatewayLatencyP95High", "error", 1480.0),
        (35.1, "ledger-svc", "LedgerWriteErrors", "critical", 0.47),
        (36.4, "postgres", "PgLockWaitHigh", "warn", 870.0),
        (38.0, "payments-svc", "PaymentSuccessRateLow", "critical", 0.71),
        (40.2, "api-gateway", "GatewaySLOBurnRate", "error", 6.1),
    ]:
        s.add(at(sec), svc, "alert", sev, name, val, message=f"[FIRING] {name} on {svc}")
    events = s.sorted()
    what = {"ledger-svc": "write error_rate 0.47: lock timeout on ledger_entries, 31 s after v4.2.0 deploy",
            "payments-svc": "payment error_rate 0.29 (baseline 0.006)",
            "api-gateway": "p95 latency 1480 ms (baseline ~120 ms)",
            "postgres": "lock wait 870 ms (baseline < 5 ms), sampled every 2 s"}

    def recs():
        return [
            {"action": "Roll back ledger-svc canary to v4.1.9 and watch postgres lock waits",
             "why": "cheap and reversible; if lock waits clear, the v4.2.0 migration is the cause",
             "evidence": [dep, l_on],
             "runbook": "kubectl rollout undo deploy/ledger-svc --to-revision=<v4.1.9>; watch pg_lock_wait_ms for 2 minutes."},
            {"action": "Identify the lock holder in postgres before blaming either side",
             "why": "postgres lock waits rose, but at 2 s sampling we cannot tell whether they precede ledger-svc errors",
             "evidence": [p_on],
             "runbook": "psql -c \"SELECT pid, application_name, wait_event_type, query FROM pg_stat_activity WHERE wait_event_type = 'Lock';\""},
        ]

    disamb = [
        "postgres pg_stat_activity: which client holds the locks on ledger_entries (ledger-svc v4.2.0 migration or something else)",
        "ledger-svc error_rate split by version (v4.1.9 vs v4.2.0 replicas)",
        "postgres pg_lock_wait_ms at 1 s resolution, to see whether it rises before ledger-svc errors (2 s sampling hides the order)",
    ]
    inc = build_incident("inc_2", "ledger-svc deploy or postgres locks (undecided)", events, A, {"ledger-svc": dep},
                         what, recs, D + timedelta(seconds=31), disamb)
    return events, inc


# ================================================================ write
def summary(inc: dict) -> dict:
    rc = inc["root_cause"]
    return IncidentSummary.model_validate({
        "id": inc["id"], "title": inc["title"], "status": inc["status"], "opened_at": inc["opened_at"],
        "updated_at": inc["updated_at"], "services": inc["services"], "alert_count": inc["alert_count"],
        "severity": inc["severity"], "root_service": rc["service"] if rc else None,
        "confidence": rc["confidence"] if rc else inc["candidates"][0]["score"],
    }).model_dump(mode="json")


def health_state(inc: dict, ts: str) -> dict:
    sev = {e["service"]: e["severity"] for e in inc["evidence_events"]}
    out = []
    for svc in [s["id"] for s in TOPOLOGY["services"]]:
        if svc in inc["services"]:
            h = "critical" if sev.get(svc) == "critical" or svc == (inc["root_cause"] or {}).get("service") else "degraded"
        else:
            h = "healthy"
        out.append({"service": svc, "health": h, "is_root_cause": svc == (inc["root_cause"] or {}).get("service")})
    root = (inc["root_cause"] or {}).get("service")
    hot = []
    if root:
        frontier = {root}
        while frontier:
            nxt = set()
            for caller, deps in DEPS.items():
                for d in deps:
                    if d in frontier and caller in inc["services"] and [caller, d] not in hot:
                        hot.append([caller, d])
                        nxt.add(caller)
            frontier = nxt
    return TopologyState.model_validate({"ts": ts, "services": out, "hot_edges": hot}).model_dump(mode="json")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    Topology.model_validate(TOPOLOGY)
    ev1, inc1, ids, next_id = scenario_db_pool()
    ev2, inc2 = scenario_ambiguous(next_id)
    for e in ev1 + ev2:
        Event.model_validate(e)

    report = {
        "incident_id": "inc_1",
        "summary": "postgres ran out of connections at 14:02:05 IST and every other failing service was waiting on it. "
                   "Login, ledger and payment failures are downstream effects, not separate problems.",
        "sentences": [
            {"text": "postgres connection pool reached 100% at 14:02:05 IST and began refusing new clients.",
             "cites": [ids["p_on"], ids["p_log"]]},
            {"text": "About 1.1 s later auth-svc logins started failing because auth-svc could not acquire a database connection.",
             "cites": [ids["a_on"], ids["a_err"]]},
            {"text": "ledger-svc writes then timed out waiting on the same pool, pushing its error rate to 0.42.",
             "cites": [ids["l_on"], ids["l_err"]]},
            {"text": "payments-svc failed because ledger-svc returned 503, so failed payments are a symptom of the database problem.",
             "cites": [ids["pm_on"], ids["pm_err"]]},
            {"text": "api-gateway p95 latency rose to 3120 ms as requests queued behind both failing paths.",
             "cites": [ids["g_on"]]},
            {"text": f"{inc1['impact']['users_affected']} users saw a failed login or payment and "
                     f"{inc1['impact']['failed_payments']} payments worth INR {int(inc1['impact']['failed_value_inr'])} failed.",
             "cites": [ids["pm_on"], ids["a_on"]]},
        ],
        "dropped": 1,  # e.g. the LLM claimed "a nightly batch job caused the spike" with no citation
        "provider": "groq",
        "generated_at": inc1["updated_at"],
    }
    Report.model_validate(report)

    def dump(name, obj):
        (OUT / name).write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")

    dump("topology.json", TOPOLOGY)
    dump("events_db_pool.json", ev1)
    dump("events_ambiguous.json", ev2)
    dump("incident_db_pool.json", inc1)
    dump("incident_ambiguous.json", inc2)
    dump("incidents_list.json", [summary(inc1), summary(inc2)])
    dump("report_db_pool.json", report)

    # SSE sample: exactly the wire format of GET /api/stream
    first_alert = next(e for e in ev1 if e["kind"] == "alert")
    pg_event = next(e for e in ev1 if e["id"] == ids["p_on"])
    lines = []
    for name, data in [("event", pg_event), ("event", first_alert),
                       ("topology_state", health_state(inc1, inc1["updated_at"])),
                       ("incident_update", inc1)]:
        lines.append(f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n")
    (OUT / "sse_sample.txt").write_text("".join(lines), encoding="utf-8")

    for inc in (inc1, inc2):
        print(inc["id"], inc["status"], [(c["service"], c["score"]) for c in inc["candidates"]],
              inc["abstain_reason"], inc["alert_count"], inc["impact"])
    print("events:", len(ev1), len(ev2))


if __name__ == "__main__":
    main()
