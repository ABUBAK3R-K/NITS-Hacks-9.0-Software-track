"""Generates contracts/fixtures/*.json from two hand-designed scenarios.

Run:  python contracts/gen_fixtures.py   (from repo root)

Fixtures are the shared target for every lane:
  * Lane B can run analyze(events_*.json, topology) and compare with incident_*.json
  * Lane C renders incident_*.json / incidents_list.json / report_db_pool.json before the API exists
  * Lane D grounds report sentences against incident.evidence_events

The event streams are hand-designed here; the incidents are produced by running
the real engine (backend/app/engine) on them, so fixtures always match what the
live demo shows. Re-run after any engine change.
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
    Event, IncidentSummary, Report, Topology,
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


# ---------------------------------------------------------------- incidents come from the real engine
sys.path.insert(0, str(ROOT / "backend"))
from app.engine.run import analyze  # noqa: E402


def engine_incident(events: list[dict]) -> dict:
    """Run the production engine on the scenario stream and return its single incident."""
    incs = analyze([Event.model_validate(e) for e in events], Topology.model_validate(TOPOLOGY))
    assert len(incs) == 1, f"expected one incident, engine returned {len(incs)}"
    return incs[0].model_dump(mode="json", by_alias=True)


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
    # postgres: pool saturated, connections refused
    p_on = s.add(at(0.12), "postgres", "metric", "critical", "db_pool_in_use_pct", 100.0, pool_max=100)
    p_log = s.add(at(0.48), "postgres", "log", "critical", "log",
                  message="FATAL: remaining connection slots are reserved for non-replication superuser connections")
    p_lat = s.add(at(0.90), "postgres", "metric", "critical", "latency_p95_ms", 2400.0)
    # auth-svc
    a_on = s.add(at(1.21), "auth-svc", "log", "error", "log",
                 message="login failed: could not acquire DB connection within 2000ms (pool=auth-pg)", user_id="u_1187")
    a_err = s.add(at(1.40), "auth-svc", "metric", "error", "error_rate", 0.31)
    s.add(at(1.40), "auth-svc", "metric", "warn", "latency_p95_ms", 2110.0)
    # ledger-svc
    l_on = s.add(at(1.53), "ledger-svc", "log", "error", "log",
                 message="ledger write failed: timeout acquiring connection from pool (postgres:5432)")
    l_err = s.add(at(1.70), "ledger-svc", "metric", "critical", "error_rate", 0.42)
    s.add(at(1.70), "ledger-svc", "metric", "warn", "latency_p95_ms", 2050.0)
    # payments-svc
    pm_on = s.add(at(2.38), "payments-svc", "log", "error", "log",
                  message="POST /v1/payments -> ledger-svc 503: upstream ledger unavailable", order_id="ord_558201")
    pm_err = s.add(at(2.41), "payments-svc", "metric", "critical", "error_rate", 0.38)
    s.add(at(2.41), "payments-svc", "metric", "warn", "latency_p95_ms", 2620.0)
    # api-gateway
    g_on = s.add(at(3.30), "api-gateway", "metric", "critical", "latency_p95_ms", 3120.0)
    s.add(at(3.30), "api-gateway", "metric", "error", "error_rate", 0.22)
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
    inc = engine_incident(events)
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
    s.add(at(0.0), "ledger-svc", "change", "info", "deploy", message="ledger-svc v4.1.9 -> v4.2.0 (migration 0042_add_settlement_idx)",
                version="v4.2.0", previous_version="v4.1.9", actor="ci-bot")
    s.add(at(31.0), "ledger-svc", "log", "error", "log",
                 message="ledger write failed: canceling statement due to lock timeout (relation ledger_entries)")
    s.add(at(31.2), "ledger-svc", "metric", "critical", "error_rate", 0.47)
    s.add(at(31.9), "payments-svc", "metric", "error", "error_rate", 0.29)
    s.add(at(32.8), "api-gateway", "metric", "error", "latency_p95_ms", 1480.0)
    s.add(at(34.2), "postgres", "metric", "warn", "pg_lock_wait_ms", 870.0)
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
    inc = engine_incident(events)
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
    """topology_state exactly as the live store builds it (app.store.build_topology_state)."""
    from app.store import build_topology_state
    from contracts.schemas import Incident
    st = build_topology_state([Incident.model_validate(inc)], Topology.model_validate(TOPOLOGY), ts)
    return st.model_dump(mode="json")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    Topology.model_validate(TOPOLOGY)
    ev1, inc1, ids, next_id = scenario_db_pool()
    ev2, inc2 = scenario_ambiguous(next_id)
    for e in ev1 + ev2:
        Event.model_validate(e)

    # Report sentences are built from the engine's incident so every cite is in its
    # evidence set and every number is one the engine measured.
    tl = {t["service"]: t for t in inc1["timeline"]}
    imp = inc1["impact"]
    sentences = [{"text": f"postgres was the first service to fail: {tl['postgres']['what']}.",
                  "cites": inc1["root_cause"]["evidence"][-2:]}]
    for svc in [t["service"] for t in inc1["timeline"]][1:]:
        dep = tl[svc]["caused_by"]
        tail = f", after {dep} degraded" if dep else ""
        sentences.append({"text": f"{svc} followed{tail}: {tl[svc]['what']}.", "cites": tl[svc]["event_ids"]})
    sentences.append({"text": f"{imp['users_affected']} users saw a failed login or payment and "
                              f"{imp['failed_payments']} payments worth INR {int(imp['failed_value_inr'])} failed.",
                      "cites": tl["payments-svc"]["event_ids"][:1] + tl["auth-svc"]["event_ids"][:1]})
    report = {
        "incident_id": inc1["id"],
        "summary": "postgres ran out of connections and every other failing service was waiting on it. "
                   "Login, ledger and payment failures are downstream effects, not separate problems.",
        "sentences": sentences,
        "dropped": 1,  # e.g. the LLM claimed "a nightly batch job caused the spike" with no citation
        "provider": "groq",
        "generated_at": inc1["updated_at"],
    }
    Report.model_validate(report)

    def dump(name, obj):
        (OUT / name).write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8", newline="\n")

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
    (OUT / "sse_sample.txt").write_text("".join(lines), encoding="utf-8", newline="\n")

    for inc in (inc1, inc2):
        print(inc["id"], inc["status"], [(c["service"], c["score"]) for c in inc["candidates"]],
              inc["abstain_reason"], inc["alert_count"], inc["impact"])
    print("events:", len(ev1), len(ev2))


if __name__ == "__main__":
    main()
