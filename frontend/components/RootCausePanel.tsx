"use client";
import { useState } from "react";
import type { Candidate, Incident, Recommendation } from "@/lib/contracts/types";
import { ABSTAIN_MIN_MARGIN, ABSTAIN_MIN_SCORE } from "@/lib/contracts/types";
import { istTimeMs, signatureLabel } from "@/lib/format";
import { EvidenceChips } from "./Evidence";
import { COMPONENT_COLORS, COMPONENT_LABELS, ConfidenceBar, Empty, StatusBadge } from "./ui";

const KEYS = ["upstream", "precedence", "change", "severity"] as const;

function Legend() {
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-400">
      {KEYS.map((k) => (
        <span key={k} className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: COMPONENT_COLORS[k] }} />
          {COMPONENT_LABELS[k]}
        </span>
      ))}
      <span className="flex items-center gap-1.5">
        <span className="hatch h-2.5 w-4 rounded-sm ring-1 ring-slate-600" />
        victim penalty
      </span>
    </div>
  );
}

/** Stacked bar on a 0..1 scale. Raw components are drawn full; the penalty strikes out the lost share. */
function ScoreBar({ c, height = "h-4" }: { c: Candidate; height?: string }) {
  const raw = KEYS.reduce((s, k) => s + c.components[k], 0);
  const kept = raw * c.penalty;
  return (
    <div className={`relative flex ${height} w-full overflow-hidden rounded bg-slate-800/80`}>
      {KEYS.map((k) =>
        c.components[k] > 0 ? (
          <div
            key={k}
            className="bar-fill h-full"
            title={`${k} ${c.components[k].toFixed(3)}`}
            style={{ width: `${c.components[k] * 100}%`, background: COMPONENT_COLORS[k] }}
          />
        ) : null,
      )}
      {c.penalty < 1 && (
        <div
          className="hatch absolute top-0 h-full border-l-2 border-white/80 bg-black/55"
          style={{ left: `${kept * 100}%`, width: `${(raw - kept) * 100}%` }}
        />
      )}
    </div>
  );
}

function CandidateRow({ c, rank, top }: { c: Candidate; rank: number; top: boolean }) {
  return (
    <div className={`rounded-lg p-2.5 ${top ? "bg-red-500/[0.06] ring-1 ring-red-500/30" : "bg-slate-900/50"}`}>
      <div className="flex items-baseline gap-2">
        <span className="w-5 font-mono text-sm text-slate-500">{rank}</span>
        <span className={`text-base font-bold ${top ? "text-red-200" : "text-slate-100"}`}>{c.service}</span>
        <span className="truncate text-xs text-slate-500">{signatureLabel(c.signature)}</span>
        <span className="ml-auto font-mono text-lg font-bold tabular-nums text-white">{c.score.toFixed(2)}</span>
      </div>
      <div className="mt-1.5 pl-7">
        <ScoreBar c={c} />
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
          {c.penalty < 1 && (
            <span className="rounded bg-slate-800 px-1.5 py-0.5 text-xs font-semibold text-amber-200">
              x{c.penalty}, victim of {c.victim_of}
            </span>
          )}
          <EvidenceChips ids={c.evidence} max={top ? 6 : 4} />
        </div>
      </div>
    </div>
  );
}

function ComponentBreakdown({ c }: { c: Candidate }) {
  return (
    <div className="grid grid-cols-4 gap-2">
      {KEYS.map((k) => (
        <div key={k} className="rounded-md bg-slate-900/70 p-2">
          <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-slate-400">
            <span className="h-2 w-2 rounded-sm" style={{ background: COMPONENT_COLORS[k] }} />
            {k}
          </div>
          <div className="font-mono text-lg font-bold text-white">{c.components[k].toFixed(2)}</div>
        </div>
      ))}
    </div>
  );
}

function Recommendations({ recs }: { recs: Recommendation[] }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!recs.length) return null;
  return (
    <div className="mt-4">
      <div className="panel-title mb-2">Recommended actions</div>
      <ol className="space-y-2">
        {recs.map((r, i) => (
          <li key={i} className="rounded-lg bg-slate-900/60 p-2.5 ring-1 ring-slate-800">
            <div className="flex gap-2">
              <span className="font-mono text-sm font-bold text-cyan-300">{i + 1}.</span>
              <div className="min-w-0 flex-1">
                <div className="text-[15px] font-semibold leading-snug text-white">{r.action}</div>
                <div className="mt-0.5 text-sm text-slate-400">{r.why}</div>
                <div className="mt-1.5 flex flex-wrap items-center gap-2">
                  <EvidenceChips ids={r.evidence} />
                  <button
                    type="button"
                    onClick={() => setOpen(open === i ? null : i)}
                    className="ml-auto text-xs font-semibold text-cyan-300 hover:text-cyan-100"
                  >
                    {open === i ? "Hide runbook" : "Runbook"}
                  </button>
                </div>
                {open === i && (
                  <pre className="fade-up mt-2 whitespace-pre-wrap rounded-md bg-black/60 p-2.5 font-mono text-xs leading-relaxed text-emerald-200 ring-1 ring-slate-800">
                    {r.runbook}
                  </pre>
                )}
              </div>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

function OpenView({ inc }: { inc: Incident }) {
  const rc = inc.root_cause!;
  const top = inc.candidates[0];
  const second = inc.candidates[1];
  return (
    <>
      <div className="rounded-xl border border-red-500/40 bg-gradient-to-br from-red-500/15 to-transparent p-4">
        <div className="flex items-center justify-between">
          <span className="panel-title text-red-300">Root cause</span>
          <StatusBadge status={inc.status} />
        </div>
        <div className="mt-1 flex items-baseline gap-3">
          <span className="text-4xl font-black text-white">{rc.service}</span>
          <span className="text-lg font-semibold text-red-200">{signatureLabel(rc.signature)}</span>
        </div>
        <div className="mt-3 flex items-center gap-3">
          <span className="w-24 text-sm text-slate-400">confidence</span>
          <ConfidenceBar value={rc.confidence} />
          <span className="w-16 text-right font-mono text-2xl font-black text-white">{Math.round(rc.confidence * 100)}%</span>
        </div>
        <div className="mt-1 pl-[108px] text-xs text-slate-500">
          margin {second ? (top.score - second.score).toFixed(2) : "n/a"} over {second?.service ?? "none"} (abstains under{" "}
          {ABSTAIN_MIN_MARGIN} margin or {ABSTAIN_MIN_SCORE} score)
        </div>
        {top && (
          <div className="mt-3">
            <ComponentBreakdown c={top} />
          </div>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-sm text-slate-400">evidence</span>
          <EvidenceChips ids={rc.evidence} />
        </div>
      </div>
      <Ranked inc={inc} />
      <Recommendations recs={inc.recommendations} />
    </>
  );
}

function Ranked({ inc, skip = 0 }: { inc: Incident; skip?: number }) {
  const list = inc.candidates.slice(skip);
  if (!list.length) return null;
  return (
    <div className="mt-4">
      <div className="mb-2 flex items-center justify-between">
        <span className="panel-title">{skip ? "Other candidates" : "Ranked candidates"}</span>
        <Legend />
      </div>
      <div className="space-y-1.5">
        {list.map((c, i) => (
          <CandidateRow key={c.service} c={c} rank={i + 1 + skip} top={!skip && i === 0} />
        ))}
      </div>
    </div>
  );
}

function AbstainCard({ c, rank }: { c: Candidate; rank: number }) {
  return (
    <div className="rounded-xl border border-amber-400/50 bg-amber-400/[0.06] p-3">
      <div className="flex items-baseline justify-between">
        <span className="font-mono text-xs text-amber-300">#{rank}</span>
        <span className="font-mono text-2xl font-black text-white">{c.score.toFixed(3)}</span>
      </div>
      <div className="text-2xl font-black text-white">{c.service}</div>
      <div className="text-sm text-amber-100/80">{signatureLabel(c.signature)}</div>
      <div className="mt-1 font-mono text-xs text-slate-400">onset {istTimeMs(c.onset)}</div>
      <div className="mt-2">
        <ScoreBar c={c} height="h-3" />
      </div>
      <div className="mt-2 space-y-0.5 text-xs">
        {KEYS.map((k) => (
          <div key={k} className="flex justify-between">
            <span className="flex items-center gap-1.5 text-slate-400">
              <span className="h-2 w-2 rounded-sm" style={{ background: COMPONENT_COLORS[k] }} />
              {k}
            </span>
            <span className="font-mono text-slate-200">{c.components[k].toFixed(3)}</span>
          </div>
        ))}
      </div>
      {c.penalty < 1 && (
        <div className="mt-2 rounded bg-slate-900 px-1.5 py-1 text-xs font-semibold text-amber-200">
          x{c.penalty}, victim of {c.victim_of}
        </div>
      )}
      <div className="mt-2">
        <EvidenceChips ids={c.evidence} max={5} />
      </div>
    </div>
  );
}

function AbstainedView({ inc }: { inc: Incident }) {
  const [a, b] = inc.candidates;
  return (
    <>
      <div className="rounded-xl border-2 border-amber-400/60 bg-gradient-to-br from-amber-400/15 to-transparent p-4">
        <div className="flex items-center justify-between">
          <span className="panel-title text-amber-300">Verdict</span>
          <StatusBadge status="ABSTAINED" big />
        </div>
        <div className="mt-1 text-3xl font-black leading-tight text-white">Not enough evidence to name one cause</div>
        <div className="mt-1 text-sm text-amber-100/90">{inc.abstain_reason}</div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3">
        {a && <AbstainCard c={a} rank={1} />}
        {b && <AbstainCard c={b} rank={2} />}
      </div>
      {inc.disambiguation.length > 0 && (
        <div className="mt-4 rounded-xl border border-cyan-500/30 bg-cyan-500/[0.05] p-3">
          <div className="panel-title mb-2 text-cyan-300">What data would disambiguate</div>
          <ul className="space-y-1.5">
            {inc.disambiguation.map((d) => (
              <li key={d} className="flex gap-2 text-[15px] leading-snug text-slate-100">
                <span className="text-cyan-400">?</span>
                {d}
              </li>
            ))}
          </ul>
        </div>
      )}
      <Ranked inc={inc} skip={2} />
      <Recommendations recs={inc.recommendations} />
    </>
  );
}

export function RootCausePanel({ incident }: { incident: Incident | null }) {
  return (
    <section className="panel flex min-h-0 flex-col p-3">
      <div className="mb-2 flex items-center justify-between px-1">
        <span className="panel-title">Root cause analysis</span>
        {incident && <span className="font-mono text-xs text-slate-500">{incident.id}</span>}
      </div>
      <div className="thin-scroll min-h-0 flex-1 overflow-y-auto pr-1">
        {!incident ? (
          <Empty>
            <div>
              <div className="text-lg font-semibold text-slate-300">No incident selected</div>
              <div className="mt-1 text-sm">The engine ranks every anomalous service and shows its evidence here.</div>
            </div>
          </Empty>
        ) : (
          <div key={incident.id} className="fade-up">
            {incident.status === "ABSTAINED" ? <AbstainedView inc={incident} /> : incident.root_cause ? <OpenView inc={incident} /> : <Ranked inc={incident} />}
          </div>
        )}
      </div>
    </section>
  );
}
