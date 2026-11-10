"""Given-time ranking: which service's metrics shifted most at a reported failure time.

Used when the failure time is already known (an on-call engineer says "it broke at
14:02", or a benchmark supplies it). For every (service, metric) we compare the
median after the failure time with the median before it, scaled by the spread
before it (interquartile range, with floors):

    shift = |median(after) - median(before)| / max(IQR(before), 5% of |median(before)|, 1e-6)

A service's score is its largest shift over its metrics. This is the robust
median/IQR scoring that BARO uses (Pham, Ha, Zhang, ASE 2024, arXiv 2408.13729);
it does not use the topology, so it complements the live engine (run.analyze),
which has to find the failure time on its own.

Pure and deterministic: same events and time in, same ranking out. Every ranked
service carries evidence ids: the metric sample after the failure time that is
furthest from the "before" median, and the last sample before the failure time.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.schemas import Event

from .common import parse_ts


@dataclass
class ShiftCandidate:
    service: str
    score: float
    metric: str
    before_median: float
    after_median: float
    evidence: list[str] = field(default_factory=list)


@dataclass
class GivenTimeResult:
    ranking: list[ShiftCandidate]
    confident: bool  # top score is at least min_ratio x the second one

    @property
    def root(self) -> str | None:
        return self.ranking[0].service if self.confident and self.ranking else None


def rank_at(evs: list[Event], failure_ts: float, cfg: dict) -> GivenTimeResult:
    pre_s, post_s = cfg["given_time_pre_s"], cfg["given_time_post_s"]
    min_pre, min_post = cfg["given_time_min_pre"], cfg["given_time_min_post"]
    series: dict[tuple[str, str], tuple[list, list]] = {}  # (before, after) lists of (ts, id, value)
    for e in evs:
        if e.kind != "metric" or e.value is None:
            continue
        t = parse_ts(e.ts)
        if failure_ts - pre_s <= t < failure_ts:
            side = 0
        elif failure_ts <= t < failure_ts + post_s:
            side = 1
        else:
            continue
        series.setdefault((e.service, e.name), ([], []))[side].append((t, e.id, float(e.value)))
    best: dict[str, ShiftCandidate] = {}
    for (svc, name) in sorted(series):
        pre, post = (sorted(x) for x in series[(svc, name)])  # time order, so evidence ignores input order
        if len(pre) < min_pre or len(post) < min_post:
            continue
        a, b = np.asarray([x[2] for x in pre]), np.asarray([x[2] for x in post])
        med = float(np.median(a))
        iqr = float(np.percentile(a, 75) - np.percentile(a, 25))
        after = float(np.median(b))
        s = abs(after - med) / max(iqr, 0.05 * abs(med), 1e-6)
        if svc not in best or s > best[svc].score:
            far = int(np.argmax(np.abs(b - med)))
            best[svc] = ShiftCandidate(svc, round(s, 3), name, round(med, 6), round(after, 6),
                                       [post[far][1], pre[-1][1]])
    ranking = sorted(best.values(), key=lambda c: (-c.score, c.service))
    top = ranking[0].score if ranking else 0.0
    second = ranking[1].score if len(ranking) > 1 else 0.0
    confident = top > 0 and top >= cfg["given_time_min_ratio"] * second
    return GivenTimeResult(ranking, confident)
