"use client";
// U4 "Naive AI mode": the naive LLM's single answer next to J.A.R.V.I.S.'s, with the naive
// method's MEASURED accuracy from the eval underneath, so one lucky run never stands alone.
// No number here is typed: everything comes from NaiveAnswer, EvalResults or the Incident.
import { useEffect, useState } from "react";
import type { EvalResults, Incident, Measured, MethodScore, NaiveAnswer } from "@/lib/contracts/types";
import { api } from "@/lib/api";
import { int, pct, signatureLabel } from "@/lib/format";
import { EvidenceChips } from "./Evidence";
import { WhyNot } from "./RootCausePanel";
import { StatusBadge } from "./ui";

/** Lane F's eval method names: "jarvis", "latest_deploy", "most_alerts", "llm_only". */
function findMethod(ev: EvalResults | null, name: string): MethodScore | null {
  if (!ev || ev.n <= 0) return null;
  return ev.methods.find((m) => m.name === name) ?? null;
}

function MeasuredLine({ m, what }: { m: Measured | null; what: string }) {
  if (!m)
    return (
      <span>
        {what} measured accuracy: <b className="text-ink">eval pending</b>
      </span>
    );
  return (
    <span>
      {what} measured top-1: <b className="font-mono text-ink">{pct(m.top1)}</b> over n={int(m.n)} blind runs
    </span>
  );
}

export function NaiveCompare({ incident }: { incident: Incident }) {
  const [naive, setNaive] = useState<NaiveAnswer | null>(null);
  const [naiveErr, setNaiveErr] = useState<string | null>(null);
  const [evalRes, setEvalRes] = useState<EvalResults | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .getNaive(incident.id)
      .then((n) => alive && setNaive(n))
      .catch((e: Error) => alive && setNaiveErr(e.message));
    api
      .getEvalResults()
      .then((r) => alive && setEvalRes(r))
      .catch(() => alive && setEvalRes(null));
    return () => {
      alive = false;
    };
  }, [incident.id]);

  const llm = findMethod(evalRes, "llm_only");
  const naiveMeasured: Measured | null = naive?.measured ?? (llm ? { top1: llm.top1, n: llm.n || evalRes!.n } : null);
  const ours = findMethod(evalRes, "jarvis");
  const oursMeasured: Measured | null = ours ? { top1: ours.top1, n: ours.n || evalRes!.n } : null;

  // both LLMs failed or no key: the backend answers provider "template", model "none"
  const naiveDown = naive !== null && (naive.provider === "template" || naive.model === "none");
  const rc = incident.root_cause;
  const abstained = incident.status === "ABSTAINED";

  return (
    <div className="fade-up mt-6 grid grid-cols-2 gap-6">
      {/* ---------------- naive LLM */}
      <section className="flex flex-col rounded-lg border border-rule-strong bg-sheet">
        <header className="border-b border-rule p-5">
          <div className="panel-title text-ink-2">Naive AI: one prompt over the raw events</div>
          {naive && (
            <div className="mt-1 font-mono text-xs text-subtle">
              {naive.provider} · {naive.model} · {int(naive.latency_ms)} ms
            </div>
          )}
        </header>
        <div className="flex-1 p-5">
          {!naive && !naiveErr && (
            <div className="flex items-center gap-2 text-muted">
              <span className="live-dot h-2.5 w-2.5 rounded-full bg-subtle" />
              Asking the LLM directly...
            </div>
          )}
          {naiveErr && <div className="text-sm text-danger">Naive answer unavailable: {naiveErr}</div>}
          {naive && naiveDown && (
            <div className="rounded-lg bg-raised/60 p-4 text-ink-2 ring-1 ring-rule-strong">
              <div className="text-xl font-bold text-ink">Naive LLM unavailable</div>
              <div className="mt-1 font-mono text-xs text-subtle">{naive.raw_text}</div>
            </div>
          )}
          {naive && !naiveDown && (
            <>
              <div className="text-sm text-muted">its answer</div>
              <div className="text-4xl font-black text-ink">{naive.root_cause_service ?? "no single service"}</div>
              <div className="mt-1 text-xs text-subtle">free text: no event ids, no score, no way to abstain</div>
              <pre className="thin-scroll mt-4 max-h-[420px] overflow-auto whitespace-pre-wrap rounded-lg bg-paper p-4 font-mono text-[13px] leading-relaxed text-ink-2 ring-1 ring-rule">
                {naive.raw_text}
              </pre>
            </>
          )}
        </div>
        <footer className="rounded-b-2xl border-t border-rule bg-paper px-5 py-3 text-sm text-muted">
          <MeasuredLine m={naiveMeasured} what="naive LLM" />
        </footer>
      </section>

      {/* ---------------- J.A.R.V.I.S. */}
      <section className="flex flex-col rounded-lg border border-accent/40 bg-sheet">
        <header className="border-b border-rule p-5">
          <div className="panel-title text-accent">J.A.R.V.I.S.: deterministic engine, evidence attached</div>
          <div className="mt-1 font-mono text-xs text-subtle">no LLM in the decision · abstains when the margin is thin</div>
        </header>
        <div className="flex-1 p-5">
          <div className="flex items-center gap-3 text-sm text-muted">
            its answer <StatusBadge status={incident.status} />
          </div>
          {abstained ? (
            <>
              <div className="text-3xl font-black text-warn">Abstained</div>
              <div className="mt-1 text-sm text-warn">{incident.abstain_reason}</div>
              <div className="mt-2 text-sm text-ink-2">
                candidates:{" "}
                {incident.candidates.slice(0, 2).map((c) => (
                  <span key={c.service} className="mr-3 font-mono">
                    {c.service} {c.score.toFixed(3)}
                  </span>
                ))}
              </div>
            </>
          ) : rc ? (
            <>
              <div className="flex items-baseline gap-3">
                <span className="text-4xl font-black text-ink">{rc.service}</span>
                <span className="font-mono text-2xl font-bold text-danger">{pct(rc.confidence)}</span>
              </div>
              <div className="text-sm text-danger">{signatureLabel(rc.signature)}</div>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <span className="text-sm text-muted">evidence</span>
                <EvidenceChips ids={rc.evidence} max={10} />
              </div>
            </>
          ) : (
            <div className="text-muted">No root cause yet.</div>
          )}
          <WhyNot inc={incident} className="mt-5" />
        </div>
        <footer className="rounded-b-2xl border-t border-rule bg-paper px-5 py-3 text-sm text-muted">
          <MeasuredLine m={oursMeasured} what="J.A.R.V.I.S." />
        </footer>
      </section>
    </div>
  );
}
