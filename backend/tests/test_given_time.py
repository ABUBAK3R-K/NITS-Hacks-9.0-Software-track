"""Given-time ranking (app.engine.given_time): robust median/IQR shift at a known failure time."""
from datetime import datetime, timezone

from app.engine.config import CONFIG
from app.engine.given_time import rank_at
from app.schemas import Event

T0 = 1_700_000_000.0


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _events(shift_svc: str = "cart", step: float = 5.0) -> list[Event]:
    out = []
    for i in range(900):  # 15 min at 1 s, failure at +600 s
        t = T0 + i
        for j, svc in enumerate(("cart", "checkout", "frontend")):
            base = 1.0 + 0.05 * ((i * (j + 3)) % 7)  # deterministic wiggle
            v = base + (step if svc == shift_svc and i >= 600 else 0.0)
            out.append(Event(id=f"evt_{svc}_{i:04d}", ts=_iso(t), source="upload", service=svc,
                             kind="metric", severity="info", name=f"{svc}_cpu", value=v))
    return out


def test_shifted_service_ranks_first_with_evidence():
    r = rank_at(_events("cart"), T0 + 600, CONFIG)
    assert r.ranking[0].service == "cart"
    assert r.confident and r.root == "cart"
    ev = r.ranking[0].evidence
    assert len(ev) == 2 and ev[1] == "evt_cart_0599"  # last sample before the failure time
    assert ev[0].startswith("evt_cart_") and int(ev[0].rsplit("_", 1)[1]) >= 600


def test_deterministic_and_order_independent():
    evs = _events("checkout")
    a = rank_at(evs, T0 + 600, CONFIG)
    b = rank_at(list(reversed(evs)), T0 + 600, CONFIG)
    assert [(c.service, c.score, c.evidence) for c in a.ranking] == [(c.service, c.score, c.evidence) for c in b.ranking]
    assert a.root == "checkout"


def test_no_shift_is_not_confident():
    r = rank_at(_events(step=0.0), T0 + 600, CONFIG)
    assert r.root is None


def test_too_few_samples_scores_nothing():
    evs = [e for e in _events() if e.ts < _iso(T0 + 605)]  # only 5 s after the failure time
    r = rank_at(evs, T0 + 600, {**CONFIG, "given_time_min_post": 10})
    assert r.ranking == [] and r.root is None


def test_shift_check_endpoint_matches_fixture_shape_and_agrees_on_hero():
    import json
    from pathlib import Path

    from fastapi.testclient import TestClient

    from app.main import app
    from app.schemas import ShiftCheck

    fx = ShiftCheck.model_validate(json.loads((Path(__file__).resolve().parents[2] / "contracts" / "fixtures"
                                               / "shift_check_db_pool.json").read_text(encoding="utf-8")))
    assert fx.root_service == "postgres" and fx.agrees is True
    c = TestClient(app)
    run = c.post("/api/runs/designed", json={"target": "postgres", "kind": "pool_exhaustion", "seed": 1}).json()
    inc = run["incidents"][0]
    sc = ShiftCheck.model_validate(c.get(f"/api/incidents/{inc['id']}/shift_check").json())
    assert sc.ranking[0].service == "postgres" and sc.agrees is True
    assert all(len(r.evidence) == 2 for r in sc.ranking)
    assert c.get("/api/incidents/inc_nope/shift_check").status_code == 404
