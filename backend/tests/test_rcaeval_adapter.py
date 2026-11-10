"""RCAEval adapter on a tiny synthetic frame shaped like an RE1-OB metrics.parquet.
Needs pandas (eval-only dependency); skipped when it is not installed. Never touches real data."""
import sys
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval" / "rcaeval"))

import adapter as A  # noqa: E402

from app.engine.run import analyze  # noqa: E402
from app.schemas import Event, Topology  # noqa: E402

COLS = ["adservice_cpu", "cartservice_mem", "frontend_latency", "frontend-external_load",
        "checkoutservice_latency-90", "redis_cpu", "main_cpu", "PassthroughCluster_error"]


def _frame(n=6, t0=1685202688):
    df = pd.DataFrame({"time": [t0 + i for i in range(n)]})
    for k, c in enumerate(COLS):
        df[c] = [0.1 * (k + 1) + 0.001 * i for i in range(n)]
    df.loc[2, "adservice_cpu"] = None  # not observed at that second
    return df


def test_events_are_valid_metric_events():
    out = A.frame_to_events(_frame())
    assert out.events
    assert out.null_values == 1
    for e in out.events:
        Event.model_validate(e.model_dump())
        assert e.kind == "metric" and e.value is not None
        assert e.name in COLS and e.service == A.split_column(e.name)[0]
        assert e.ts.endswith("Z") and len(e.ts) == 24
    assert out.events[0].ts == "2023-05-27T15:51:28.000Z"
    assert len({e.id for e in out.events}) == len(out.events)
    # 6 kept columns x 6 rows, minus the null cell
    assert len(out.events) == 6 * 6 - 1


def test_non_services_are_dropped_and_names_kept():
    out = A.frame_to_events(_frame())
    assert set(out.dropped_columns) == {"main_cpu", "PassthroughCluster_error"}
    assert {e.name for e in out.events} == set(COLS) - {"main_cpu", "PassthroughCluster_error"}
    assert A.split_column("checkoutservice_latency-90") == ("checkoutservice", "latency-90")
    assert A.split_column("frontend-external_load") == ("frontend-external", "load")


def test_topology_has_no_unknown_services():
    topo = A.ob_topology()
    Topology.model_validate(topo.model_dump(by_alias=True))
    ids = {s.id for s in topo.services}
    assert all(e.from_ in ids and e.to in ids for e in topo.edges)
    assert ids == set(A.OB_SERVICES)
    assert {"frontend", "checkoutservice", "cartservice", "redis", "productcatalogservice", "currencyservice",
            "adservice", "recommendationservice", "shippingservice", "emailservice", "paymentservice",
            "frontend-external"} == ids
    out = A.frame_to_events(_frame())
    assert {e.service for e in out.events} <= ids


def test_no_label_or_inject_time_reaches_the_engine():
    out = A.frame_to_events(_frame())
    assert {e.kind for e in out.events} == {"metric"}
    assert all("inject" not in e.name and "root" not in e.name for e in out.events)
    assert isinstance(analyze(out.events, A.ob_topology()), list)  # the engine accepts adapted events
