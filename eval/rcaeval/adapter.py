"""RCAEval RE1-OB adapter: one RCAEval case -> (events, topology, ground truth).

RE1-OB comes from RCAEval (Pham et al., WWW 2025, arXiv 2412.17015, MIT licence),
Hugging Face dataset `phamquiluan/RCAEval`. Each case directory holds
`metrics.parquet` (a `time` column in unix seconds, then one float column per
metric named `{service}_{metric}`, 1 s apart) and `inject_time.txt`. The
case index `cases.parquet` carries the label (`root_cause_service`).

What the engine sees: only metric Events built from the metric columns. The
label and the inject time are never turned into events; the runner may use the
inject time only to choose which incident to score (see run_rcaeval.py).

Downloaded files are untrusted input: they are only read with pandas
(parquet) and int() on inject_time.txt, nothing from them is executed.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

import pandas as pd  # noqa: E402

from app.schemas import Event, Topology  # noqa: E402

# ---------------------------------------------------------------- topology
# Online Boutique call graph, caller -> callee. Source: GoogleCloudPlatform/microservices-demo
# (README "Architecture" diagram, and the *_ADDR env vars in kubernetes-manifests/):
#   frontend.yaml:              PRODUCT_CATALOG, CURRENCY, CART, RECOMMENDATION, SHIPPING, CHECKOUT, AD
#   checkoutservice.yaml:       PRODUCT_CATALOG, SHIPPING, PAYMENT, EMAIL, CURRENCY, CART
#   recommendationservice.yaml: PRODUCT_CATALOG
#   cartservice.yaml:           REDIS_ADDR (redis-cart; its metric prefix in RCAEval is `redis`)
# frontend.yaml also defines Service `frontend-external` (type LoadBalancer, selector app: frontend):
# the user-facing entry in front of the same pods. RCAEval reports its load and error columns
# separately, so it is an edge node that calls frontend.
OB_EDGES: list[tuple[str, str]] = [
    ("frontend-external", "frontend"),
    ("frontend", "cartservice"),
    ("frontend", "productcatalogservice"),
    ("frontend", "currencyservice"),
    ("frontend", "recommendationservice"),
    ("frontend", "adservice"),
    ("frontend", "checkoutservice"),
    ("frontend", "shippingservice"),
    ("checkoutservice", "cartservice"),
    ("checkoutservice", "productcatalogservice"),
    ("checkoutservice", "currencyservice"),
    ("checkoutservice", "shippingservice"),
    ("checkoutservice", "emailservice"),
    ("checkoutservice", "paymentservice"),
    ("recommendationservice", "productcatalogservice"),
    ("cartservice", "redis"),
]
OB_TIERS = {"frontend-external": "edge", "redis": "data"}  # everything else is "app"

# Column prefixes in RE1-OB that are not application services in the call graph.
DROPPED_SERVICES = {
    "main": "the loadgenerator container (Locust traffic client, kubernetes-manifests/loadgenerator.yaml)",
    "frontend-check": "the loadgenerator init container (waits for the frontend), kubernetes-manifests/loadgenerator.yaml",
    "PassthroughCluster": "Istio's catch-all cluster for traffic to unknown destinations, not a service",
    "InboundPassthroughClusterIpv4": "Istio's catch-all cluster for inbound traffic, not a service",
}


def ob_topology() -> Topology:
    ids = sorted({s for e in OB_EDGES for s in e})
    return Topology.model_validate({
        "services": [{"id": s, "label": s, "tier": OB_TIERS.get(s, "app")} for s in ids],
        "edges": [{"from": a, "to": b} for a, b in OB_EDGES],
    })


OB_SERVICES = frozenset(s for e in OB_EDGES for s in e)


# ---------------------------------------------------------------- columns -> events
def split_column(col: str) -> tuple[str, str]:
    """`checkoutservice_latency-90` -> ("checkoutservice", "latency-90"). Service names in
    RE1-OB use hyphens, never underscores, so the first underscore is the split."""
    svc, _, metric = col.partition("_")
    return svc, metric


def iso_utc(t: float) -> str:
    ms = int(round(float(t) * 1000))
    d = datetime.fromtimestamp(ms // 1000, tz=timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"


@dataclass
class Adapted:
    events: list[Event]
    used_columns: list[str] = field(default_factory=list)
    dropped_columns: dict[str, str] = field(default_factory=dict)  # column -> reason
    null_values: int = 0  # cells that were null (metric not observed at that second): no event


def frame_to_events(df: pd.DataFrame, services: frozenset[str] = OB_SERVICES) -> Adapted:
    """One Event(kind="metric") per non-null cell of every metric column whose service is in the
    topology. name = the original column name, service = its prefix, ts = ISO UTC."""
    if "time" not in df.columns:
        raise ValueError("metrics frame has no `time` column")
    out = Adapted(events=[])
    cols: list[tuple[int, str, str]] = []
    for j, col in enumerate(c for c in df.columns if c != "time"):
        svc, metric = split_column(str(col))
        if svc in DROPPED_SERVICES:
            out.dropped_columns[col] = f"{svc}: {DROPPED_SERVICES[svc]}"
        elif svc not in services or not metric:
            out.dropped_columns[col] = f"{svc}: not in the Online Boutique call graph"
        else:
            cols.append((j, str(col), svc))
            out.used_columns.append(str(col))
    times = pd.to_numeric(df["time"], errors="coerce").to_numpy()
    values = {col: pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float) for _, col, _ in cols}
    for r, t in enumerate(times):
        if not isfinite(t):
            continue
        ts = iso_utc(t)
        for j, col, svc in cols:
            v = values[col][r]
            if not isfinite(v):
                out.null_values += 1
                continue
            out.events.append(Event(id=f"evt_r{r:05d}_c{j:02d}", ts=ts, source="upload", service=svc,
                                    kind="metric", severity="info", name=col, value=float(v)))
    return out


# ---------------------------------------------------------------- cases
@dataclass
class Case:
    case: str
    fault: str
    ground_truth: str
    inject_time: int
    adapted: Adapted
    n_rows: int

    @property
    def events(self) -> list[Event]:
        return self.adapted.events


def read_index(data_dir: Path, dataset: str = "RE1-OB") -> pd.DataFrame:
    idx = pd.read_parquet(Path(data_dir) / "cases.parquet")
    return idx[idx["dataset"] == dataset].sort_values("case").reset_index(drop=True)


def load_case(data_dir: Path, row) -> Case:
    """row: one row of read_index(). The label comes from cases.parquet (it also equals the
    `{service}` part of the directory name; the runner checks that)."""
    d = Path(data_dir) / str(row["case"])
    df = pd.read_parquet(d / "metrics.parquet")
    inject = int(str((d / "inject_time.txt").read_text(encoding="ascii")).strip())
    return Case(case=str(row["case"]), fault=str(row["fault"]), ground_truth=str(row["root_cause_service"]),
                inject_time=inject, adapted=frame_to_events(df), n_rows=len(df))
