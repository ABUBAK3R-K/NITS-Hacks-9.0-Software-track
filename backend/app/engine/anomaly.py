"""Step 1: per-service anomaly detection.

Signals per service, all turned into time-stamped `Point`s:
  * metrics: EWMA mean/variance baseline per (service, metric name) and a
    z-score per sample. Anomalous samples never update the baseline, so a
    sustained fault cannot poison it. A sample counts when z >= z_threshold
    and it sits in a run of 2+ anomalous samples (or z >= z_strong: one critical-level
    sample is enough on its own). On high-frequency signals (sampled every second or
    faster) the run must also last hf_confirm_s seconds.
  * alerts (kind=alert) count directly.
  * logs: 1 critical log counts directly; error logs count as a burst
    (2+ within 10 s) or when a strong signal is within 5 s.
  * failed requests (attrs.status >= 500): a burst of 3+ within +-10 s that is
    at least 20% of the service's requests, or within 5 s of a strong signal.
  * silence: a service that stopped reporting metrics (gap > 3 x its scrape
    interval) is down; evidence is its last scrape.
Points are split into episodes by quiet gaps of episode_gap_s. Onset = first
point of an episode.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from math import sqrt

from app.schemas import Event

from .common import SEV_RANK, Episode, Point, event_status, fmt_metric, iso


@dataclass
class AnomalyResult:
    episodes: dict[str, list[Episode]]
    scrape_interval: dict[str, float]


def _eps(name: str, cfg: dict) -> float:
    tab = cfg["std_abs_eps"]
    for suffix in ("_ms", "_pct"):
        if name.endswith(suffix):
            return tab[suffix]
    for key in ("rate", "ratio"):
        if key in name:
            return tab[key]
    return tab["default"]


def _score_signal(values: list[float], name: str, cfg: dict) -> tuple[list[float], list[float]]:
    """z-score of each sample against the baseline built from earlier normal samples."""
    alpha, warm = cfg["ewma_alpha"], cfg["warmup_samples"]
    gate, rel = cfg["z_update_gate"], cfg["std_rel_floor"]
    eps = _eps(name, cfg)
    down = any(k in name for k in cfg["down_is_bad"])
    mean = var = 0.0
    n = 0
    zs: list[float] = []
    means: list[float] = []
    for x in values:
        z = 0.0
        if n >= warm:
            std = max(sqrt(var), rel * abs(mean), eps)
            z = ((mean - x) if down else (x - mean)) / std
        zs.append(z)
        means.append(mean if n else x)
        if n < warm or z < gate:
            a = max(alpha, 1.0 / (n + 1))
            d = x - mean
            mean += a * d
            var = (1.0 - a) * (var + a * d * d)
            n += 1
    return zs, means


def _z_sev(z: float) -> int:
    return 3 if z >= 20 else 2 if z >= 8 else 1


def _substantial(ep: Episode, cfg: dict) -> bool:
    """An episode needs one strong signal (metric, alert, silence, critical log)
    or at least min_weak_points weak ones, so two stray error logs never page."""
    if len(ep.points) >= cfg["min_weak_points"]:
        return True
    return any(p.kind in ("metric", "alert", "silence") or p.sev >= 3 for p in ep.points)


def detect(evs: list[Event], ts: list[float], cfg: dict) -> AnomalyResult:
    t_end = ts[-1] if ts else 0.0
    sig_idx: dict[tuple[str, str], list[int]] = {}
    metric_ts: dict[str, list[float]] = {}
    metric_last_id: dict[str, list[str]] = {}
    err_logs: dict[str, list[int]] = {}
    req_ts: dict[str, list[float]] = {}
    fails: dict[str, list[int]] = {}
    strong: dict[str, list[Point]] = {}
    sev_mag = cfg["severity_magnitude"]

    def add_strong(p: Point, svc: str) -> None:
        strong.setdefault(svc, []).append(p)

    for i, e in enumerate(evs):
        k = e.kind
        svc = e.service
        if k == "metric":
            if e.value is None:
                continue
            sig_idx.setdefault((svc, e.name), []).append(i)
            mt = metric_ts.setdefault(svc, [])
            if not mt or mt[-1] != ts[i]:
                mt.append(ts[i])
                metric_last_id.setdefault(svc, []).append(e.id)
        elif k == "alert":
            msg = (e.message or "").lower()
            state = str((e.attrs or {}).get("state", "")).lower()
            if msg.startswith("[resolved]") or state == "resolved":
                continue
            val = f" ({fmt_metric(e.name, e.value)})" if e.value is not None else ""
            add_strong(Point(ts[i], e.id, "alert", e.name, sev_mag.get(e.severity, 8.0),
                             max(SEV_RANK.get(e.severity, 1), 1), e.value, None, None,
                             f"alert {e.name} firing{val}"), svc)
        elif k == "log":
            if e.severity == "critical":
                add_strong(Point(ts[i], e.id, "log", "log", sev_mag["critical"], 3, None, None, None,
                                 f"critical log: {(e.message or '')[:140]}"), svc)
            elif e.severity == "error":
                err_logs.setdefault(svc, []).append(i)
        elif k == "request":
            req_ts.setdefault(svc, []).append(ts[i])
            st = event_status(e)
            if st is not None and st >= 500:
                fails.setdefault(svc, []).append(i)

    # ---- metrics
    thr, strong_z, cap = cfg["z_threshold"], cfg["z_strong"], cfg["z_cap"]
    hf_interval, hf_confirm_s = cfg["hf_interval_s"], cfg["hf_confirm_s"]
    for (svc, name), idxs in sig_idx.items():
        vals = [evs[i].value for i in idxs]
        zs, means = _score_signal(vals, name, cfg)
        flag = [z >= thr for z in zs]
        m = len(idxs)
        # A run of consecutive flagged samples j..k is confirmed when it has 2+ samples. On a
        # high-frequency signal (median sample gap <= hf_interval_s) it must also last
        # hf_confirm_s, so a blip on a 1 s series (often one slower scrape forward-filled
        # for a few seconds) does not page. Slower signals keep the plain 2-sample rule.
        sig_ts = [ts[i] for i in idxs]
        gaps = sorted(b - a for a, b in zip(sig_ts, sig_ts[1:]))
        need_s = hf_confirm_s if gaps and gaps[len(gaps) // 2] <= hf_interval else 0.0
        confirmed = [False] * m
        j = 0
        while j < m:
            if not flag[j]:
                j += 1
                continue
            k = j
            while k + 1 < m and flag[k + 1]:
                k += 1
            if k > j and sig_ts[k] - sig_ts[j] >= need_s:
                for q in range(j, k + 1):
                    confirmed[q] = True
            j = k + 1
        for j in range(m):
            if not flag[j]:
                continue
            z = zs[j]
            if z >= strong_z or confirmed[j]:
                e = evs[idxs[j]]
                sev = max(SEV_RANK.get(e.severity, 0), _z_sev(z))
                add_strong(Point(ts[idxs[j]], e.id, "metric", name, min(z, cap), sev, vals[j], means[j],
                                 round(z, 2), f"{name} {fmt_metric(name, vals[j])} "
                                 f"(baseline {fmt_metric(name, means[j])})"), svc)

    # ---- silence: a service that stops reporting metrics
    scrape: dict[str, float] = {}
    for svc, mt in metric_ts.items():
        if len(mt) < 2:
            continue
        diffs = sorted(b - a for a, b in zip(mt, mt[1:]))
        med = diffs[len(diffs) // 2]
        scrape[svc] = med
        if len(mt) < cfg["silence_min_scrapes"] or med <= 0:
            continue
        limit = max(cfg["silence_factor"] * med, cfg["silence_min_s"])
        ids = metric_last_id[svc]
        gaps = [(mt[j], mt[j + 1], ids[j]) for j in range(len(mt) - 1) if mt[j + 1] - mt[j] > limit]
        if t_end - mt[-1] > limit:
            gaps.append((mt[-1], t_end, ids[-1]))
        step = cfg["episode_gap_s"] / 2.0
        for a, b, eid in gaps:
            t = a + med
            while True:
                add_strong(Point(t, eid, "silence", "silence", cfg["silence_magnitude"], 3, None, None, None,
                                 f"stopped reporting metrics: no scrape for {b - a:.0f} s (last at {iso(a)})"), svc)
                if t >= b:
                    break
                t = min(t + step, b)

    # ---- weak signals: error logs and failed requests
    points: dict[str, list[Point]] = {svc: list(p) for svc, p in strong.items()}
    strong_ts = {svc: sorted(p.ts for p in pts) for svc, pts in strong.items()}
    att = cfg["attach_window_s"]

    def near_strong(svc: str, t: float) -> bool:
        st = strong_ts.get(svc)
        if not st:
            return False
        j = bisect_left(st, t - att)
        return j < len(st) and st[j] <= t + att

    lw, lc = cfg["log_burst_window_s"], cfg["log_burst_count"]
    for svc, idxs in err_logs.items():
        lts = [ts[i] for i in idxs]
        for j, i in enumerate(idxs):
            t = lts[j]
            cnt = bisect_right(lts, t + lw) - bisect_left(lts, t - lw)
            if cnt >= lc or near_strong(svc, t):
                e = evs[i]
                points.setdefault(svc, []).append(Point(t, e.id, "log", "log", sev_mag["error"], 2, None, None,
                                                        None, f"error log: {(e.message or '')[:140]}"))

    rw, rc, rr = cfg["req_burst_window_s"], cfg["req_burst_count"], cfg["req_burst_min_ratio"]
    for svc, idxs in fails.items():
        fts = [ts[i] for i in idxs]
        rts = req_ts.get(svc, [])
        for j, i in enumerate(idxs):
            t = fts[j]
            cnt = bisect_right(fts, t + rw) - bisect_left(fts, t - rw)
            tot = max(1, bisect_right(rts, t + rw) - bisect_left(rts, t - rw))
            ratio = cnt / tot
            if (cnt >= rc and ratio >= rr) or near_strong(svc, t):
                e = evs[i]
                st = event_status(e)
                route = (e.attrs or {}).get("route") or ""
                points.setdefault(svc, []).append(Point(
                    t, e.id, "request", e.name, min(cap, 10.0 + 30.0 * ratio), 2, None, None, None,
                    f"failed requests: HTTP {st} {route}".rstrip() + f" ({cnt} failed in {2 * rw:.0f} s)"))

    # ---- episodes
    gap = cfg["episode_gap_s"]
    episodes: dict[str, list[Episode]] = {}
    for svc in sorted(points):
        pts = sorted(points[svc], key=lambda p: (p.ts, p.event_id, p.kind))
        eps_list: list[Episode] = []
        cur: Episode | None = None
        for p in pts:
            if cur is None or p.ts - cur.last > gap:
                cur = Episode(svc)
                eps_list.append(cur)
            cur.points.append(p)
        keep = [ep for ep in eps_list if _substantial(ep, cfg)]
        if keep:
            episodes[svc] = keep
    return AnomalyResult(episodes, scrape)
