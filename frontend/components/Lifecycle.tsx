"use client";
// U5: lifecycle badge + compact history, MTTD / MTTR chips (only when the backend computed
// them), and the Apply fix button. The UI never moves the lifecycle itself.
import { useState } from "react";
import type { ApplyFixResult, Incident, Lifecycle, Recommendation } from "@/lib/contracts/types";
import { istTime } from "@/lib/format";
import { useLiveData } from "@/lib/live";
import { HINTS, Hint } from "./Hint";

const STEPS: Lifecycle[] = ["DETECTED", "SPREADING", "CAUSE_IDENTIFIED", "MITIGATING", "RESOLVED"];
const STEP_LABEL: Record<Lifecycle, string> = {
  DETECTED: "detected",
  SPREADING: "spreading",
  CAUSE_IDENTIFIED: "cause identified",
  MITIGATING: "mitigating",
  RESOLVED: "resolved",
};
const STEP_COLOR: Record<Lifecycle, string> = {
  DETECTED: "bg-danger-soft text-danger ring-danger/60",
  SPREADING: "bg-human-soft text-human ring-human/60",
  CAUSE_IDENTIFIED: "bg-accent-soft text-accent ring-accent/60",
  MITIGATING: "bg-accent-soft text-accent ring-accent/60",
  RESOLVED: "bg-safe-soft text-safe ring-safe/60",
};

export function LifecycleBadge({ state }: { state: Lifecycle }) {
  return (
    <span className={`rounded-md px-2 py-0.5 text-[11px] font-bold uppercase tracking-wider ring-1 ${STEP_COLOR[state]}`}>
      {STEP_LABEL[state]}
    </span>
  );
}

function secs(v: number): string {
  return v >= 100 ? `${Math.round(v)} s` : `${v.toFixed(1)} s`;
}

export function MetricChips({ inc }: { inc: Incident }) {
  if (inc.mttd_s === null && inc.mttr_s === null) return null;
  return (
    <span className="flex items-center gap-1.5">
      {inc.mttd_s !== null && (
        <span className="rounded-md bg-raised px-2 py-0.5 font-mono text-xs text-ink ring-1 ring-rule-strong" title="fault injection to DETECTED">
          MTTD <b className="text-ink">{secs(inc.mttd_s)}</b>
        </span>
      )}
      {inc.mttr_s !== null && (
        <span className="rounded-md bg-safe-soft px-2 py-0.5 font-mono text-xs text-safe ring-1 ring-safe/50" title="fault injection to RESOLVED">
          MTTR <b className="text-ink">{secs(inc.mttr_s)}</b>
        </span>
      )}
    </span>
  );
}

/** Stepper: reached states show their time from lifecycle_history, the current one pulses. */
export function LifecycleStrip({ inc }: { inc: Incident }) {
  const at = new Map(inc.lifecycle_history.map((h) => [h.state, h.ts]));
  return (
    <div className="mb-3 rounded-lg bg-sheet p-2.5 ring-1 ring-rule">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5">
          <span className="panel-title">Lifecycle</span>
          <Hint text={HINTS.lifecycle} label="What is the lifecycle?" />
        </span>
        <MetricChips inc={inc} />
      </div>
      <ol className="flex flex-wrap items-stretch gap-1 md:flex-nowrap">
        {STEPS.map((s) => {
          const cur = s === inc.lifecycle;
          const ts = at.get(s);
          const reached = cur || ts !== undefined;
          return (
            <li
              key={s}
              className={`min-w-[30%] flex-1 rounded-md px-1.5 py-1 text-center ring-1 md:min-w-0 transition-colors duration-500 ${
                reached ? STEP_COLOR[s] : "bg-transparent text-subtle ring-rule"
              } ${cur ? "live-dot-soft font-black" : ""}`}
            >
              <div className="text-[10px] font-bold uppercase leading-tight tracking-wider">{STEP_LABEL[s]}</div>
              <div className="font-mono text-[10px] opacity-80">{ts ? istTime(ts) : " "}</div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

export function ApplyFixButton({ inc, rec }: { inc: Incident; rec: Recommendation }) {
  const live = useLiveData();
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<ApplyFixResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  if (!rec.fix_id) return null;
  const fixId = rec.fix_id;
  const resolved = inc.lifecycle === "RESOLVED";

  const go = async () => {
    setBusy(true);
    setErr(null);
    try {
      setRes(await live.applyFix(inc.id, fixId));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-2">
      <button
        type="button"
        disabled={busy || resolved || Boolean(res?.applied)}
        onClick={go}
        className="rounded-md bg-ink px-3 py-1.5 text-sm font-bold text-paper hover:bg-ink-2 disabled:opacity-50"
      >
        {busy ? "Applying..." : res?.applied ? "Fix applied" : "Apply fix"}
      </button>
      <span className="ml-2 font-mono text-xs text-subtle">{fixId}</span>
      {res && (
        <div className={`mt-1.5 text-sm ${res.applied ? "text-safe" : "text-warn"}`}>
          {res.message}
          {res.applied &&
            (resolved ? (
              <span className="ml-1 font-bold text-safe">The engine reports RESOLVED.</span>
            ) : (
              <span className="ml-1 text-muted">
                Waiting for the engine to see recovery (now: {STEP_LABEL[inc.lifecycle]}).
              </span>
            ))}
        </div>
      )}
      {err && <div className="mt-1.5 text-sm text-danger">Apply fix failed: {err}</div>}
    </div>
  );
}
