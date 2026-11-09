# J.A.R.V.I.S. (NITS Hacks 9.0, Software Track, PS1)

Justified Analysis, Reasoning & Visualization of Incident Systems.

## One-line pitch
Many alerts, one cause, and every claim shows its evidence. A deterministic engine decides the root cause; the LLM only narrates what the evidence already proves, and the system abstains instead of guessing when two causes are equally likely.

## Hard deadlines (IST, 9 Nov 2026)
- 19:00 feature freeze (bug fixes only after this)
- 19:30 STOP ALL CODE. Demo video (< 2 min) + PPT (official template) from here.
- 21:00 Midterm submission on Unstop: PPT, demo video, repo link, live link.
- Final round 10 Nov 17:00 to 20:00: updated PPT/video/prototype allowed.

## Rules for every session
- Contracts in `contracts/` are law. Never change a response shape without updating `contracts/` + fixtures + both sides in the same commit.
- The engine is deterministic and pure: same events in, same ranking out. No LLM calls inside `backend/app/engine/`.
- Every root-cause claim carries `evidence: [event_id, ...]`. No evidence, no claim.
- LLM output is validated: any sentence whose cited ids are not in the incident's evidence set is dropped. If the LLM fails or times out (>8 s), use the deterministic template narrative. The demo must never break on an API call.
- Abstain rule: if `top1.score - top2.score < 0.15` or `top1.score < 0.45`, status is `ABSTAINED` and the UI shows both candidates plus "what data would disambiguate".
- Log every non-obvious choice in `DECISIONS.md` and every bug/dead end in `FAILURES.md`, same session, one short entry each. These feed the PPT.
- No em dashes in any user-facing text or docs.
- Prefer boring, working code over clever code. Wasih must be able to explain every module.

## Stack
- `backend/`: Python 3.11, FastAPI, pydantic v2, networkx, numpy. SSE for live stream (`sse-starlette`). pytest.
- `frontend/`: Next.js (App Router) + TypeScript + Tailwind + React Flow (`@xyflow/react`) + Recharts. EventSource for SSE.
- LLM: Groq (`openai/gpt-oss-120b`, JSON mode, override with `GROQ_MODEL`; llama-3.3-70b-versatile is no longer served) primary, Gemini Flash fallback, template fallback. Keys via env: `GROQ_API_KEY`, `GEMINI_API_KEY`.
- Deploy: backend on Render (free web service), frontend on Vercel. `NEXT_PUBLIC_API_URL` points at Render.

## Repo layout
```
contracts/            # schemas.py (pydantic), types.ts, fixtures/*.json  <- written FIRST
backend/app/
  main.py             # FastAPI app, CORS, routers
  store.py            # in-memory event store + incident cache (no DB needed)
  ingest/             # api ingest, file upload parsers (jsonl, csv, plain log lines)
  sim/                # topology + traffic generator + fault injectors
  engine/             # anomaly.py, grouping.py, rca.py, impact.py, timeline.py, recommend.py
  narrate/            # llm.py (groq/gemini/template), grounding.py (citation validator)
backend/tests/        # scenario tests: db_pool, bad_deploy, redis_down, red_herring, ambiguous
frontend/app/         # single dashboard page + /incident/[id] report view
DECISIONS.md FAILURES.md README.md
```

## Data contract (summary; full models in contracts/schemas.py)
Event:
```json
{"id":"evt_...","ts":"2026-11-09T14:02:11.120Z","source":"sim|api|upload",
 "service":"payments-svc","kind":"metric|log|alert|change|request",
 "severity":"info|warn|error|critical","name":"latency_p95_ms|error_rate|deploy|...",
 "value":812.0,"message":"optional text","attrs":{"user_id":"u_42","version":"v2.3.1","amount_inr":499}}
```
Topology: `{"services":[{"id","label","tier":"edge|app|data|external"}],"edges":[{"from":"caller","to":"callee"}]}`

Incident:
```json
{"id":"inc_1","status":"OPEN|ABSTAINED|RESOLVED","opened_at","services":["..."],
 "alert_count":14,"severity":"critical",
 "root_cause":{"service","signature","confidence","evidence":["evt_..."]} | null,
 "candidates":[{"service","score","components":{"upstream":0.4,"precedence":0.3,"change":0.2,"severity":0.1},"evidence":[...]}],
 "impact":{"services":[...],"users_affected":312,"failed_payments":87,"failed_value_inr":43413},
 "timeline":[{"ts","service","what","event_ids":[...],"caused_by":"service|null"}],
 "recommendations":[{"action","why","evidence":[...],"runbook":"..."}]}
```

## API
- `POST /api/events` batch ingest (list of Event)
- `POST /api/upload` multipart file: .jsonl, .csv, or plain log lines (`2026-11-09T.. LEVEL service message`)
- `GET  /api/topology`
- `GET  /api/incidents` list; `GET /api/incidents/{id}` full detail
- `POST /api/incidents/{id}/report` LLM narrative `{summary, sentences:[{text, cites:[evt_id]}], dropped:n, provider:"groq|gemini|template"}`
- `POST /api/sim/start`, `POST /api/sim/fault {"type":"db_pool_exhaustion|bad_deploy|redis_down|red_herring|psp_slow"}`, `POST /api/sim/reset`
- `GET  /api/stream` SSE: `event: event|incident_update|topology_state`

## Simulated system (payments platform, matches the PS scenario)
```
client -> api-gateway -> auth-svc -> redis-session
                                   -> postgres
          api-gateway -> payments-svc -> ledger-svc -> postgres
                                      -> psp-adapter (external bank)
```
Faults and expected symptoms:
- `db_pool_exhaustion` on postgres: login failures (auth), slow APIs (gateway p95), failed transactions (payments/ledger). Root cause: postgres. THIS IS THE HERO DEMO.
- `bad_deploy` on payments-svc (change event v2.3.1, then 5xx spike): root cause payments-svc, change component high.
- `redis_down`: login failures only; payments healthy. Proves we do not over-group.
- `red_herring`: harmless deploy on ledger-svc 30 s before a postgres pool exhaustion. Engine must still pick postgres (precedence + upstream beat change) or abstain, never blame ledger outright.
- `psp_slow`: external bank latency; payments degraded, root cause psp-adapter (external, recommend failover/circuit breaker).

## Engine algorithm (deterministic)
1. Anomaly per (service, signal): EWMA baseline + z-score over a rolling window; alerts and error-log bursts count as anomalies directly. Onset = first anomalous timestamp.
2. Grouping: anomalous services within a 5 min window that are connected in the dependency graph (treat edges as undirected for grouping) form one incident. Disconnected anomalies become separate incidents.
3. Candidate scoring per anomalous service in the incident (weights in one config dict):
   - upstream (0.40): share of other anomalous services that transitively depend on it (callers reach it via edges).
   - precedence (0.30): its onset is earlier than its anomalous dependents' onsets.
   - change (0.20): a `change` event on it within 10 min before onset.
   - severity (0.10): normalized anomaly magnitude.
   Penalty: if it has an anomalous dependency of its own, multiply by 0.5 (it is probably a victim).
4. Abstain rule (see above). Record every component with the event ids that produced it.
5. Impact: anomalous set + all upstream callers to the edge; users = unique `user_id` on failed requests; failed payments count and INR value from payments request events.
6. Timeline: onsets sorted, each annotated with `caused_by` = the nearest anomalous dependency with an earlier onset.
7. Recommendations: rule table keyed by (root service tier, signature) -> action + runbook text, each carrying evidence ids. LLM may rephrase, not invent.
