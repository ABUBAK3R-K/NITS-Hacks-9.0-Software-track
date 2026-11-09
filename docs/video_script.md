# Demo video script (target 1:50, the form says under 2:00)

Recorded locally. Everything on screen is the real engine on simulated data, which the organisers explicitly allow.

## Before you hit record
1. Start the backend (with keys) and the frontend in live mode:
   ```
   cd backend && set -a && . ../.env && set +a && ../.venv/Scripts/python -m uvicorn app.main:app --port 8000
   cd frontend && NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev
   ```
2. Use Chrome at 1920x1080, full screen (F11), zoom 100%. Close other tabs and turn notifications off.
3. Open http://localhost:3000/dashboard and click **Reset**, then **Start**. Wait 20 s so the baseline traffic settles. The indicator should read LIVE.
4. Warm the LLM once: open any incident report about 2 minutes before the take. Groq allows 8k tokens a minute, so a report and a naive answer in the same minute can push the report onto Gemini. That still works, it just shows "narrated by gemini".
5. Have the case study open in a second tab at http://localhost:3000/#evaluation.

## Shot list and narration

| Time | Screen | Do | Say |
|---|---|---|---|
| 0:00-0:10 | Dashboard, all green | nothing | "A payments platform: users can't log in, APIs are slow, payments fail. Three alerts, three teams paged. But it's one hidden cause." |
| 0:10-0:20 | Header line "Our AI is not allowed to decide anything..." | hover it | "J.A.R.V.I.S. decides the root cause with deterministic math on the dependency graph. The LLM only narrates what the math proved, and it abstains when two causes are too close." |
| 0:20-0:38 | Chaos Lab | click **DB pool exhaustion** | "Let's break the database. Alerts flood in, and notice the gateway pages first. Alert order is not causal order." (the flood collapses into "N alerts, 1 cause") |
| 0:38-0:52 | Root cause card + graph | click one **evidence chip**, show the raw event, close it | "One incident, root cause postgres, connection pool exhausted. Every claim links to the raw event that proves it." |
| 0:52-1:00 | "Why not the others?" | point at the ledger-svc line | "And it says why it did not blame the others: they broke after postgres, and their own dependency was already broken." |
| 1:00-1:12 | Click **Report**, then turn on **Naive AI mode** | wait for both columns | "Next to it, a plain LLM given the same events. It agrees here, and it is 78% on our blind eval. But it can't show its proof. Our report keeps only sentences that cite evidence; the guardrail removes the rest." (point at the shield chip) |
| 1:12-1:27 | Back to dashboard, click **Judge's Chaos** | wait, then click **Reveal** | "A judge can inject a hidden fault. The engine answers blind, then we reveal what was injected: match." (Retake if the draw is a one-service fault.) |
| 1:27-1:38 | Recommended actions | click **Apply fix** | "Apply the recommended fix. The graph heals, the lifecycle reaches RESOLVED, and time to detect and time to recover are measured, not typed." |
| 1:38-1:50 | Case study tab, Evaluation section | scroll the table slowly | "On 100 blind randomized outages in our simulator: J.A.R.V.I.S. 100%, blaming the latest deploy 25%, the noisiest service 47%. It plugs into real systems through the events API and Alertmanager or Grafana webhooks. Many alerts. One cause. Proof attached." |

## Honesty notes for the voice-over
- Say "on our simulator". The eval does not use real production data; RCAEval real-data numbers are a final-round item.
- The LLM's 78% is n=40. The other 22% were API rate limits, not wrong answers. Don't call the LLM inaccurate. The point is that our answer is auditable.
- Numbers in a live run differ from the slides (each run is its own simulation). Say "in this run", not the slide figures.
