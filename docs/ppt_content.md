# J.A.R.V.I.S. PPT content (draft)

Order follows TASKS.md "Submission". Short bullets for the slide, one speaker-note line under each. Numbers are from the hero and ambiguous fixtures.

## 1. Problem
- One database hiccup pages three teams in under 3 seconds
- 14 alerts fire across 5 services for a single postgres pool exhaustion
- Teams debug symptoms (failed logins, failed payments, slow gateway) in parallel
- Every minute costs real money: 38 failed payments worth INR 45,462 in our hero scenario

Speaker note: "When postgres runs out of connections, auth, payments and the gateway all page at once, and nobody is looking at postgres."

## 2. Why existing alerting fails
- Alerts are per service, so one cause looks like many incidents
- First to page is not root cause: in our hero case the gateway paged first and the postgres alert fired sixth
- Threshold alerts carry no dependency context, so victims look like culprits
- AI summaries that guess a cause are worse than none: confident and wrong sends a team the wrong way

Speaker note: "Alert order is not causal order, and an LLM that guesses is just a faster way to be wrong."

## 3. Solution
- Group related alerts into one incident using the service dependency graph
- Rank root-cause candidates with a deterministic, explainable score
- Every claim cites event ids; click any chip to see the raw event
- The LLM only narrates; sentences without valid citations are removed and counted
- Abstain when two causes are too close, and say what data would decide it

Speaker note: "Many alerts, one cause, and every claim shows its evidence."

## 4. Architecture
- Ingest: Chaos Lab simulator, file upload (jsonl, csv, log lines), POST /api/events
- In-memory store, deterministic engine (anomaly, grouping, RCA, impact, timeline, recommendations)
- Incidents stream to a Next.js dashboard over SSE
- Narrator: Groq, then Gemini Flash, then template, all inside one 8 s budget, then grounding
- (Use the mermaid diagram from README.md, exported as an image)

Speaker note: "The engine decides, the LLM phrases, and grounding checks the LLM against the engine's evidence."

## 5. How the engine decides (scoring + abstain)
- score = (upstream 0.40 + precedence 0.30 + change 0.20 + severity 0.10) x penalty
- Victim penalty 0.5 when a service has a failing dependency of its own
- Hero: postgres 0.40 + 0.30 + 0 + 0.10 = 0.80; ledger-svc, payments-svc, auth-svc halved to 0.29, 0.24, 0.23
- Abstain if top1 - top2 < 0.15 or top1 < 0.45
- Ambiguous case: postgres 0.500 vs ledger-svc 0.433 (margin 0.067 < 0.15), so the system declines and asks for 1 s resolution data, per-version error rates and the postgres lock holders

Speaker note: "Same events in, same ranking out, and when the evidence cannot separate two causes we say so instead of guessing."

## 6. Demo screenshots (placeholders)
- [Capture] Alert flood: Chaos Lab after `db_pool_exhaustion`, alerts in red
- [Capture] Collapse: one incident card reading "14 alerts, 1 cause", graph path lit to postgres with pulsing ring
- [Capture] Root cause card: confidence 0.80 bar, stacked component bars, evidence chips open in the event drawer
- [Capture] Report view: cited sentences as chips, "1 unsupported sentence removed" counter, provider badge
- [Capture] Red herring / ambiguous: ABSTAINED card with both candidates and the "what would disambiguate" list
- [Capture] Impact strip: 57 users, 38 failed payments, INR 45,462

Speaker note: "Watch the alerts arrive out of order, and the engine still points at postgres."

## 7. Tech stack
- Backend: Python 3.11, FastAPI, pydantic v2, networkx, numpy, SSE (sse-starlette), pytest
- Frontend: Next.js (App Router), TypeScript, Tailwind, React Flow, Recharts, EventSource
- LLM: Groq gpt-oss-120b (JSON mode), Gemini 2.5 Flash fallback, raw httpx (no SDKs)
- Contracts: one pydantic schema file mirrored in TypeScript, seeded fixtures shared by all lanes
- Deploy: Render (backend, render.yaml) and Vercel (frontend)

Speaker note: "Boring, explainable parts, chosen so every module can be explained in one sentence."

## 8. Impact
- Time to root cause: one incident card instead of three teams triaging three symptoms
- Fewer wrong rollbacks: victims are labelled ("do not roll back payments-svc or api-gateway")
- Trust: every sentence in the report links to raw evidence; unsupported sentences are visibly removed
- Honest uncertainty: abstaining with a data checklist beats a confident wrong answer
- Works with zero LLM: the template narrator keeps the demo and production path alive when an API fails

Speaker note: "The win is not just speed, it is that on-call can trust and verify every claim in seconds."

## 9. Roadmap
- Benchmark accuracy on RCAEval (RE1/RE2 Online Boutique subset): report AC@1 and AC@3 against published baselines
- OpenTelemetry and Prometheus-style ingest adapter, so real traces and metrics flow in
- Slack and PagerDuty hooks: post the grounded report into the incident channel, page only the root-cause owner
- Incident similarity: "seen this before" from past incidents

Speaker note: "Next we prove the scoring on a public benchmark and plug into the tools on-call already uses."

## 10. Team
- [Name]: role (engine / backend)
- [Name]: role (dashboard / frontend)
- [Name]: role (simulator / ingest)
- [Name]: role (narrator / docs / pitch)

Speaker note: "We split the build into four parallel lanes around one shared contract."

## Appendix: why it works / what we learned (from DECISIONS.md and FAILURES.md)
- Contracts first: one pydantic file plus seeded fixtures let four lanes build in parallel and integrate without shape drift
- Fixtures are generated by the real formula from hand-set onsets, not hand-typed JSON, so every number in the UI is self-consistent
- Score components are stored already weighted, so the UI draws stacked bars directly and the score is a visible sum
- `evidence_events` ships inside every incident, so the evidence drawer and the citation validator need no extra request
- The ambiguous case is honest by design: 2 s sampling hides whether postgres lock waits came first, so the right answer is "we cannot tell yet"
- Grounding drops a whole sentence on one unknown cite, because we cannot tell which half the bad id was supporting
- An LLM that drops all its sentences counts as a failure, and the template takes over with the dropped count still shown
- Lesson: building an HTTPS client on Windows cost about 1.5 s per call and quietly ate the 8 s LLM budget; we now build the SSL context once
- Lesson: an operator-precedence slip (`a or [] if b else []`) in the fixture generator was caught on read-through before the first run
- Lesson: `core.autocrlf` would have turned the SSE sample into CRLF and broken the wire format, so fixtures are pinned to LF
