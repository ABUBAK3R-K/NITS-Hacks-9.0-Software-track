# References

Every number in the README, the PPT and the demo video is either from an external source (R) or produced by our own code (E). External figures were checked against the source page on 9 Nov 2026. Our own figures come with the file that holds them and the command that regenerates them; none are typed by hand.

## External sources

### Problem statistics

| Id | Claim | Source |
|---|---|---|
| R1 | Problem statement: NITS Hacks 9.0 Software Track, PS1 | [Unstop: Software Track, NITS Hacks 9.0](https://unstop.com/hackathons/software-track-nits-hacks-90-national-institute-of-technology-nit-silchar-1794955) |
| R2 | On 12 Apr 2025 the UPI transaction success rate fell to **about 50% for nearly two hours**. NPCI attributed it to some PSP banks overusing the "Check Transaction" API. | [Outlook Money: UPI outage attributed to surge in API calls from banks, says NPCI](https://www.outlookmoney.com/banking/upi-outage-attributed-to-surge-in-api-calls-from-banks-says-npci) |
| R3 | Same incident: NPCI named certain banks as the cause and planned fixes | [India TV: UPI outage caused due to certain banks, says NPCI](https://www.indiatvnews.com/technology/news/upi-outage-caused-due-to-certain-banks-says-npci-as-it-plans-fixes-2025-04-16-985701) |
| R4 | AWS us-east-1 disruption, 19 Oct 2025 11:48 PM PDT to 20 Oct 2025 2:20 PM PDT. Root cause: a race condition in DynamoDB's DNS management left an empty DNS record, and the failure cascaded to dependent services. | [AWS: Summary of the Amazon DynamoDB service disruption in us-east-1](https://aws.amazon.com/message/101925) |

### Benchmark and industry practice

| Id | Claim | Source |
|---|---|---|
| R5 | RCAEval: **735 failure cases from 3 microservice systems** (our final-round accuracy target) | [Pham et al., RCAEval, WWW 2025 (arXiv 2412.17015)](https://arxiv.org/abs/2412.17015) |
| R6 | Event correlation tools group alerts by topology, timing and change data | [BigPanda: What is event correlation?](https://www.bigpanda.io/glossary/event-correlation/) |

## Our own numbers

### Engine design constants

| Id | Number | Where it lives |
|---|---|---|
| E1 | Score weights: upstream **0.40**, precedence **0.30**, change **0.20**, severity **0.10**. Victim penalty **x0.5**. Abstain if the top two differ by **< 0.15** or the top score is **< 0.45**. | `contracts/schemas.py` (`ENGINE_WEIGHTS`, `VICTIM_PENALTY`, `ABSTAIN_MIN_MARGIN`, `ABSTAIN_MIN_SCORE`) |
| E2 | Narrator budget: **8 s** total, with Groq capped at **4.5 s** so Gemini still gets an attempt | `backend/app/narrate/llm.py` (`TOTAL_BUDGET_S`, `PER_PROVIDER_CAP_S`) |

### Hero and ambiguous scenarios (engine output)

| Id | Number | Where it lives / how to regenerate |
|---|---|---|
| E3 | Hero scenario (postgres pool exhaustion): **14 alerts, 5 services, 1 incident (14.0x noise reduction)**. postgres **0.80**; ledger-svc **0.30**, auth-svc **0.25**, payments-svc **0.25**, api-gateway **0.05**. Impact: **57 users, 38 failed payments, INR 45,462**. | `contracts/fixtures/incident_db_pool.json`, produced by the real engine: `python contracts/gen_fixtures.py` |
| E4 | Ambiguous scenario: **ABSTAINED**, postgres **0.500** vs ledger-svc **0.433**, margin **0.067** (< 0.15) | `contracts/fixtures/incident_ambiguous.json`, same command |

### Evaluation

The evaluation runs on our own simulator with randomized blind faults. RCAEval real-data numbers (R5) are a final-round item.

| Id | Number | Where it lives / how to regenerate |
|---|---|---|
| E5 | 100 blind runs, seeds 0-99, 20 per fault kind, 39 with a decoy deploy. Top-1: J.A.R.V.I.S. **100%**, latest deploy **25%**, most alerts **47%**, LLM only **78% (n=40)**. False blame: **0%**, **75%**, **53%**, **0%**. | `eval/results.json`, `eval/RESULTS.md`: `python eval/run_eval.py --seeds 0-99 --llm-n 40` |
| E6 | LLM only: **9 of 40** runs got no Groq answer after retries (rate limit); it was correct on **all 31** runs where it answered. | `eval/RESULTS.md` (split text and per-kind table: 5+7+7+6+6 = 31) |
| E7 | Sensitivity: only upstream at -50% lowers J.A.R.V.I.S. top-1, to **90%**; every other weight at ±50% stays at **100%**. | `eval/results.json` (`sensitivity`) |

### Tests and live checks

| Id | Number | Where it lives / how to regenerate |
|---|---|---|
| E8 | **278** backend tests passing at commit `7dd2d6f`, including the engine against the real simulator for all 5 faults | `cd backend && ../.venv/Scripts/python -m pytest -q` |
| E9 | Live sandbox checks: MTTD **3 s** and **9 s**, MTTR **121 s** and **118 s** (postgres pool exhaustion and payments-svc bad deploy). Every run measures its own values. | `DECISIONS.md` (Tier 2 live check entry); reproduce with `POST /api/runs/designed`, then `POST /api/incidents/{id}/apply_fix` |
| E10 | Groq `openai/gpt-oss-120b`: warm calls **2.1 to 2.4 s** on the hero incident. The key's limit is **8,000 tokens/min** (read from the `x-ratelimit-limit-tokens` response header). | `DECISIONS.md` (Groq model entries), `FAILURES.md` (rate limit entry) |

### Project progress

| Id | Number | Where it lives |
|---|---|---|
| E11 | **13 of 14 planned items built (93%).** Built: TASKS.md lanes A-D (4), UPGRADES.md U1-U7 (7), and 2 of the 3 TASKS.md final-round backlog items (OpenTelemetry ingest, incident similarity). In progress: RCAEval. The demo video (recorded 9 Nov) shows the earlier 11 of 14. | `TASKS.md`, `UPGRADES.md`, `backend/app/ingest/otlp.py`, `backend/app/similar.py` |
