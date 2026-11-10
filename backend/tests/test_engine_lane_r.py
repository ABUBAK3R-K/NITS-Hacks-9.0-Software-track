"""Lane R: sustained-anomaly rule on high-frequency metrics, incident selection, speed, determinism."""
import sys
from pathlib import Path

from app.engine import anomaly
from app.engine.common import iso
from app.engine.config import CONFIG
from app.engine.run import analyze
from app.engine.timeline import main_point
from app.schemas import Event
from tests.scenarios import scenario

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

T0 = 1_700_000_000.0


def _series(values, svc="svc-a", name="cpu_pct"):
    evs = [Event(id=f"evt_{i:05d}", ts=iso(T0 + i), source="upload", service=svc, kind="metric",
                 severity="info", name=name, value=float(v)) for i, v in enumerate(values)]
    return evs, [T0 + i for i in range(len(values))]


def _noisy_base(n=60):
    # distinct values around 50 so the baseline has a real spread
    return [50.0 + (i % 5) - 2 for i in range(n)]


def _points(values):
    evs, ts = _series(values)
    res = anomaly.detect(evs, ts, CONFIG)
    return [p for eps in res.episodes.values() for ep in eps for p in ep.points]


def test_short_blip_on_1s_series_does_not_page():
    # one elevated scrape (z about 6) forward-filled for 4 s by a 1 s resampler: not an anomaly
    assert _points(_noisy_base() + [80.0] * 4 + _noisy_base(30)) == []


def test_sustained_anomaly_on_1s_series_pages():
    pts = _points(_noisy_base() + [80.0, 80.0, 81.0, 81.0, 82.0, 82.0] + _noisy_base(30))
    assert len(pts) == 6 and pts[0].ts == T0 + 60  # onset is the first sample of the run


def test_single_extreme_scrape_still_counts():
    pts = _points(_noisy_base() + [400.0] + _noisy_base(30))
    assert len(pts) == 1 and pts[0].z >= CONFIG["z_strong"]


def test_slow_signals_keep_two_sample_rule():
    # 2 s scrapes (the simulator's rate): two anomalous samples in a row still page
    vals = _noisy_base(30) + [80.0, 81.0] + _noisy_base(15)
    evs = [Event(id=f"evt_{i:05d}", ts=iso(T0 + 2 * i), source="upload", service="svc-a", kind="metric",
                 severity="info", name="cpu_pct", value=float(v)) for i, v in enumerate(vals)]
    res = anomaly.detect(evs, [T0 + 2 * i for i in range(len(vals))], CONFIG)
    assert sum(len(ep.points) for eps in res.episodes.values() for ep in eps) == 2


def test_old_rule_is_one_config_away():
    # with hf_confirm_s = 0 the 4 s blip counts again (the rule before Lane R)
    evs, ts = _series(_noisy_base() + [80.0] * 4 + _noisy_base(30))
    res = anomaly.detect(evs, ts, {**CONFIG, "hf_confirm_s": 0.0})
    assert sum(len(ep.points) for eps in res.episodes.values() for ep in eps) == 4


def test_primary_incident_prefers_active(topology):
    from eval.run_eval import primary_incident
    incs = analyze(scenario("db_pool", seed=0), topology)
    big = max(incs, key=lambda i: len(i.services))
    resolved = big.model_copy(update={"status": "RESOLVED", "id": "inc_old",
                                      "services": big.services + ["extra-svc"]})
    small = big.model_copy(update={"status": "OPEN", "id": "inc_now", "services": big.services[:1]})
    assert primary_incident([resolved, small]).id == "inc_now"
    assert primary_incident([resolved]).id == "inc_old"


def test_main_point_long_episode_is_fast():
    from app.engine.common import Episode, Point
    ep = Episode("svc-a", [Point(T0 + i, f"evt_{i}", "metric", "latency_ms", 5.0, 1, 100.0 + i % 7)
                           for i in range(50_000)])
    p = main_point(ep, "latency_spike")
    assert p.value == 106.0 and p.ts == T0 + 6  # largest value, earliest on ties


def test_deterministic_with_evidence(topology):
    evs = scenario("red_herring", seed=3)
    a = [i.model_dump(mode="json") for i in analyze(evs, topology)]
    b = [i.model_dump(mode="json") for i in analyze(list(reversed(evs)), topology)]
    assert a == b
    for inc in a:
        if inc["root_cause"]:
            assert inc["root_cause"]["evidence"]
        for c in inc["candidates"]:
            assert c["evidence"]
