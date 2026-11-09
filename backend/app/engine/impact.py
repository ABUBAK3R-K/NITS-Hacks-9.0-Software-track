"""Step 5: blast radius.

services         = anomalous set (by onset) + every transitive caller up to the edge
users_affected   = unique attrs.user_id on failed requests (status >= 500)
failed_payments  = failed payment requests (payments-svc, or carrying amount_inr),
                   de-duplicated by attrs.order_id when present
failed_value_inr = sum of attrs.amount_inr over those
Only failed requests on impacted services inside the incident window count.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right

from app.schemas import Event, Impact

from .common import Episode, TopoIndex

PAYMENTS_SERVICE = "payments-svc"


def impacted_services(group: dict[str, Episode], topo: TopoIndex) -> list[str]:
    anomalous = sorted(group, key=lambda s: (group[s].onset, s))
    extra: set[str] = set()
    for s in anomalous:
        extra |= topo.callers_closure(s)
    extra -= set(anomalous)
    return anomalous + sorted(extra)


def compute(group: dict[str, Episode], topo: TopoIndex, failed: list[tuple[float, Event]],
            failed_ts: list[float], t_from: float, t_to: float) -> Impact:
    services = impacted_services(group, topo)
    svc_set = set(services)
    lo, hi = bisect_left(failed_ts, t_from), bisect_right(failed_ts, t_to)
    users: set[str] = set()
    pay_keys: set[str] = set()
    value = 0.0
    for t, e in failed[lo:hi]:
        if e.service not in svc_set:
            continue
        attrs = e.attrs or {}
        uid = attrs.get("user_id")
        if uid is not None:
            users.add(str(uid))
        amt = attrs.get("amount_inr")
        if e.service == PAYMENTS_SERVICE or amt is not None:
            key = str(attrs.get("order_id") or e.id)
            if key in pay_keys:
                continue
            pay_keys.add(key)
            if isinstance(amt, (int, float)) and not isinstance(amt, bool):
                value += float(amt)
    return Impact(services=services, users_affected=len(users), failed_payments=len(pay_keys),
                  failed_value_inr=round(value, 2))
