"""Step 2: group anomaly episodes into incidents.

Two episodes on different services join the same incident when the services
are adjacent in the topology (edges treated as undirected) and their onsets
are within group_window_s. Linking only through anomalous services means the grouping graph is
the subgraph INDUCED on anomalous services: a healthy service never bridges
two incidents. Services missing from the topology have no edges, so they form
their own incidents.
"""
from __future__ import annotations

from .common import Episode, TopoIndex


def _merge(eps: list[Episode]) -> Episode:
    if len(eps) == 1:
        return eps[0]
    out = Episode(eps[0].service)
    out.points = sorted((p for e in eps for p in e.points), key=lambda p: (p.ts, p.event_id, p.kind))
    return out


def group(episodes: dict[str, list[Episode]], topo: TopoIndex, cfg: dict) -> list[dict[str, Episode]]:
    nodes: list[Episode] = [e for svc in sorted(episodes) for e in episodes[svc]]
    nodes.sort(key=lambda e: (e.onset, e.service))
    parent = list(range(len(nodes)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    win = cfg["group_window_s"]
    n = len(nodes)
    for i in range(n):
        a = nodes[i]
        for j in range(i + 1, n):
            b = nodes[j]  # b.onset >= a.onset
            if b.onset - a.onset > win:
                break  # nodes are sorted by onset, so every later node is further away
            if a.service != b.service and topo.adjacent(a.service, b.service):
                ra, rb = find(i), find(j)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)

    comps: dict[int, list[Episode]] = {}
    for i in range(n):
        comps.setdefault(find(i), []).append(nodes[i])
    groups: list[dict[str, Episode]] = []
    for root in sorted(comps):
        by_svc: dict[str, list[Episode]] = {}
        for e in comps[root]:
            by_svc.setdefault(e.service, []).append(e)
        groups.append({svc: _merge(v) for svc, v in sorted(by_svc.items())})
    groups.sort(key=lambda g: (min(e.onset for e in g.values()), sorted(g)))
    return groups
