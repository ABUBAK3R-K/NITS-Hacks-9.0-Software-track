# J.A.R.V.I.S.

Justified Analysis, Reasoning & Visualization of Incident Systems. NITS Hacks 9.0, Software Track, PS1.

Many alerts, one cause, and every claim shows its evidence. A deterministic engine decides the root cause. The LLM only narrates what the evidence already proves, and the system abstains instead of guessing when two causes are equally likely.

Full README (architecture, how the engine decides, abstain rule) is written in Lane D.

## Run locally
```
python -m venv .venv && .venv/Scripts/pip install -r backend/requirements.txt
cd backend && ../.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm run dev
```
Tests: `cd backend && ../.venv/Scripts/python -m pytest -q`
Regenerate fixtures: `.venv/Scripts/python contracts/gen_fixtures.py`
