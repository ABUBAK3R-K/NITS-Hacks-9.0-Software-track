"use client";
// Second opinion (lane S): an independent ranking of which service's metrics shifted most at the
// incident's first onset (robust median/IQR, as in BARO). It ignores the dependency graph, so when it
// agrees with the engine the answer stands on two different methods.
import { useEffect, useState } from "react";
import type { Incident, ShiftCheck as ShiftCheckT } from "@/lib/contracts/types";
import { api } from "@/lib/api";
import { istTimeMs } from "@/lib/format";

const num = (x: number) => (Math.abs(x) >= 100 ? x.toFixed(0) : Math.abs(x) >= 1 ? x.toFixed(2) : x.toPrecision(2));

export function ShiftCheck({ incident }: { incident: Incident }) {
  const [sc, setSc] = useState<ShiftCheckT | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api
      .getShiftCheck(incident.id)
      .then((r) => alive && setSc(r))
      .catch((e: Error) => alive && setErr(e.message));
    return () => {
      alive = false;
    };
  }, [incident.id, incident.updated_at]);

  const verdict =
    sc == null
      ? null
      : sc.agrees === true
        ? { text: "Agrees with the engine", cls: "bg-safe-soft text-safe ring-safe/50" }
        : sc.agrees === false
          ? { text: "Disagrees with the engine", cls: "bg-warn-soft text-warn ring-warn/50" }
          : { text: sc.confident ? "Engine did not commit" : "No clear single shift", cls: "bg-sheet text-ink-2 ring-rule-strong" };

  return (
    <div className="panel p-4">
      <div className="panel-title mb-1">Second opinion: metric shift</div>
      <p className="text-xs text-muted">
        Which service&apos;s metrics moved most at the first onset{sc ? ` (${istTimeMs(sc.failure_ts)})` : ""}, by median
        before vs after (shift = how many times its normal spread it moved). Independent of the dependency graph.
      </p>
      {err && <div className="mt-2 text-sm text-muted">Not available: {err}</div>}
      {!sc && !err && <div className="mt-2 text-sm text-muted">Checking...</div>}
      {sc && verdict && (
        <>
          <span className={`mt-3 inline-block rounded-md px-2.5 py-1 text-sm font-bold ring-1 ${verdict.cls}`}>
            {verdict.text}
            {sc.root_service ? `: ${sc.root_service}` : ""}
          </span>
          <ol className="mt-3 space-y-1.5">
            {sc.ranking.slice(0, 3).map((c, i) => (
              <li key={c.service} className="text-sm">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate font-semibold text-ink">
                    {i + 1}. {c.service}
                  </span>
                  <span className="shrink-0 font-mono text-xs text-muted">shift {num(c.score)}x</span>
                </div>
                <div className="truncate font-mono text-xs text-subtle" title={`evidence: ${c.evidence.join(", ")}`}>
                  {c.metric} {num(c.before_median)} &rarr; {num(c.after_median)}
                </div>
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
}
