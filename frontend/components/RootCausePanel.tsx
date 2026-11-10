"use client";
import { useState } from "react";
import type { Candidate, Incident, Recommendation, SimilarIncident } from "@/lib/contracts/types";
import { ABSTAIN_MIN_MARGIN, ABSTAIN_MIN_SCORE } from "@/lib/contracts/types";
import { istDateTime, istTimeMs, pct, signatureLabel } from "@/lib/format";
import { useLiveData } from "@/lib/live";
import { EvidenceChips } from "./Evidence";
import { HINTS, Hint } from "./Hint";
import { ApplyFixButton, LifecycleStrip } from "./Lifecycle";
import { COMPONENT_COLORS, COMPONENT_LABELS, ConfidenceBar, Empty, StatusBadge } from "./ui";

const KEYS = ["upstream", "precedence", "change", "severity"] as const;

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
      <Hint text={HINTS.score} label="What do the score terms mean?" />
      {KEYS.map((k) => (
        <span key={k} className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: COMPONENT_COLORS[k] }} />
          {COMPONENT_LABELS[k]}
        </span>
      ))}
      <span className="flex items-center gap-1.5">
        <span className="hatch h-2.5 w-4 rounded-sm ring-1 ring-rule-strong" />
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
    <div className={`relative flex ${height} w-full overflow-hidden rounded bg-raised/80`}>
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
          className="hatch absolute top-0 h-full border-l-2 border-ink/80 bg-paper"
          style={{ left: `${kept * 100}%`, width: `${(raw - kept) * 100}%` }}
        />
      )}
    </div>
  );
}

function CandidateRow({ c, rank, top }: { c: Candidate; rank: number; top: boolean }) {
  return (
    <div className={`rounded-lg p-2.5 ${top ? "bg-danger-soft ring-1 ring-danger/30" : "bg-sheet"}`}>
      <div className="flex items-baseline gap-2">
        <span className="w-5 font-mono text-sm text-subtle">{rank}</span>
        <span className={`text-base font-bold ${top ? "text-danger" : "text-ink"}`}>{c.service}</span>
        <span className="truncate text-xs text-subtle">{signatureLabel(c.signature)}</span>
        <span className="ml-auto font-mono text-lg font-bold tabular-nums text-ink">{c.score.toFixed(2)}</span>
      </div>
      <div className="mt-1.5 pl-7">
        <ScoreBar c={c} />
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
          {c.penalty < 1 && (
            <span className="rounded bg-raised px-1.5 py-0.5 text-xs font-semibold text-warn">
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
        <div key={k} className="rounded-md bg-sheet p-2">
          <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-muted">
            <span className="h-2 w-2 rounded-sm" style={{ background: COMPONENT_COLORS[k] }} />
            {k}
          </div>
          <div className="font-mono text-lg font-bold text-ink">{c.components[k].toFixed(2)}</div>
        </div>
      ))}
    </div>
  );
}

function Recommendations({ inc, recs }: { inc: Incident; recs: Recommendation[] }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!recs.length) return null;
  return (
    <div className="mt-4">
      <div className="panel-title mb-2">Recommended actions</div>
      <ol className="space-y-2">
        {recs.map((r, i) => (
          <li key={i} className="rounded-lg bg-sheet p-2.5 ring-1 ring-rule">
            <div className="flex gap-2">
              <span className="font-mono text-sm font-bold text-accent">{i + 1}.</span>
              <div className="min-w-0 flex-1">
                <div className="text-[15px] font-semibold leading-snug text-ink">{r.action}</div>
                <div className="mt-0.5 text-sm text-muted">{r.why}</div>
                <div className="mt-1.5 flex flex-wrap items-center gap-2">
                  <EvidenceChips ids={r.evidence} />
                  <button
                    type="button"
                    onClick={() => setOpen(open === i ? null : i)}
                    className="ml-auto text-xs font-semibold text-accent hover:text-accent"
                  >
                    {open === i ? "Hide runbook" : "Runbook"}
                  </button>
                </div>
                <ApplyFixButton inc={inc} rec={r} />
                {open === i && (
                  <pre className="fade-up mt-2 whitespace-pre-wrap rounded-md bg-paper p-2.5 font-mono text-xs leading-relaxed text-safe ring-1 ring-rule">
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

/** U2 "Why not X?": the engine's own one-line reason per ruled-out candidate. */
export function WhyNot({ inc, className = "mt-4" }: { inc: Incident; className?: string }) {
  const ruled = inc.candidates.filter((c) => c.ruled_out_reason);
  if (!ruled.length) return null;
  return (
    <div className={className}>
      <div className="panel-title mb-2">Why not the others?</div>
      <ul className="space-y-1.5">
        {ruled.map((c) => (
          <li key={c.service} className="rounded-lg bg-sheet px-2.5 py-2 ring-1 ring-rule">
            <div className="text-[14px] leading-snug text-ink">
              {/* bold service label, then the engine's sentence without its "<service>: " prefix */}
              <span className="font-bold text-ink">{c.service}</span>{" "}
              {c.ruled_out_reason!.startsWith(`${c.service}: `) ? c.ruled_out_reason!.slice(c.service.length + 2) : c.ruled_out_reason}
            </div>
            {c.ruled_out_evidence.length > 0 && (
              <div className="mt-1">
                <EvidenceChips ids={c.ruled_out_evidence} max={4} />
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function OpenView({ inc }: { inc: Incident }) {
  const rc = inc.root_cause!;
  const top = inc.candidates[0];
  const second = inc.candidates[1];
  return (
    <>
      <div className="rounded-lg border border-danger/40 bg-danger-soft p-4">
        <div className="flex items-center justify-between">
          <span className="panel-title text-danger">Root cause</span>
          <StatusBadge status={inc.status} />
        </div>
        <div className="mt-1 flex items-baseline gap-3">
          <span className="text-4xl font-black text-ink">{rc.service}</span>
          <span className="text-lg font-semibold text-danger">{signatureLabel(rc.signature)}</span>
        </div>
        <div className="mt-3 flex items-center gap-3">
          <span className="w-24 text-sm text-muted">confidence</span>
          <ConfidenceBar value={rc.confidence} />
          <span className="w-16 text-right font-mono text-2xl font-black text-ink">{Math.round(rc.confidence * 100)}%</span>
        </div>
        <div className="mt-1 pl-[108px] text-xs text-subtle">
          margin {second ? (top.score - second.score).toFixed(2) : "n/a"} over {second?.service ?? "none"} (abstains under{" "}
          {ABSTAIN_MIN_MARGIN} margin or {ABSTAIN_MIN_SCORE} score)
        </div>
        {top && (
          <div className="mt-3">
            <ComponentBreakdown c={top} />
          </div>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-sm text-muted">evidence</span>
          <EvidenceChips ids={rc.evidence} />
        </div>
      </div>
      <WhyNot inc={inc} />
      <Ranked inc={inc} />
      <Recommendations inc={inc} recs={inc.recommendations} />
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
    <div className="rounded-lg border border-warn/50 bg-warn-soft p-3">
      <div className="flex items-baseline justify-between">
        <span className="font-mono text-xs text-warn">#{rank}</span>
        <span className="font-mono text-xl font-black text-ink md:text-2xl">{c.score.toFixed(3)}</span>
      </div>
      <div className="text-xl font-black text-ink md:text-2xl">{c.service}</div>
      <div className="text-sm text-warn">{signatureLabel(c.signature)}</div>
      <div className="mt-1 font-mono text-xs text-muted">onset {istTimeMs(c.onset)}</div>
      <div className="mt-2">
        <ScoreBar c={c} height="h-3" />
      </div>
      <div className="mt-2 space-y-0.5 text-xs">
        {KEYS.map((k) => (
          <div key={k} className="flex justify-between">
            <span className="flex items-center gap-1.5 text-muted">
              <span className="h-2 w-2 rounded-sm" style={{ background: COMPONENT_COLORS[k] }} />
              {k}
            </span>
            <span className="font-mono text-ink">{c.components[k].toFixed(3)}</span>
          </div>
        ))}
      </div>
      {c.penalty < 1 && (
        <div className="mt-2 rounded bg-sheet px-1.5 py-1 text-xs font-semibold text-warn">
          x{c.penalty}, victim of {c.victim_of}
        </div>
      )}
      <div className="mt-2">
        <EvidenceChips ids={c.evidence} max={5} />
      </div>
    </div>
  );
}

export function AbstainedView({ inc, readOnly = false }: { inc: Incident; readOnly?: boolean }) {
  const [a, b] = inc.candidates;
  return (
    <>
      <div className="rounded-lg border-2 border-warn/60 bg-warn-soft p-4">
        <div className="flex items-center justify-between">
          <span className="panel-title text-warn">Verdict</span>
          <StatusBadge status="ABSTAINED" big />
        </div>
        <div className="mt-1 text-2xl font-black leading-tight text-ink md:text-3xl">Not enough evidence to name one cause</div>
        <div className="mt-1 text-sm text-warn">{inc.abstain_reason}</div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 md:gap-3">
        {a && <AbstainCard c={a} rank={1} />}
        {b && <AbstainCard c={b} rank={2} />}
      </div>
      {inc.disambiguation.length > 0 && (
        <div className="mt-4 rounded-lg border border-accent/30 bg-sheet p-3">
          <div className="panel-title mb-2 text-accent">What data would disambiguate</div>
          <ul className="space-y-1.5">
            {inc.disambiguation.map((d) => (
              <li key={d} className="flex gap-2 text-[15px] leading-snug text-ink">
                <span className="text-accent">?</span>
                {d}
              </li>
            ))}
          </ul>
        </div>
      )}
      <WhyNot inc={inc} />
      <Ranked inc={inc} skip={2} />
      {!readOnly && <Recommendations inc={inc} recs={inc.recommendations} />}
    </>
  );
}

/** "Seen before": past incidents the store found similar. Selects one if it is still listed. */
function SeenBefore({ similar }: { similar: SimilarIncident[] }) {
  const live = useLiveData();
  const view = live.sandbox.active ? live.sandbox.view : live;
  const [shown, setShown] = useState<string | null>(null);
  if (!similar.length) return null;
  const listed = new Set(view.incidents.map((i) => i.id));
  return (
    <div className="mt-4">
      <div className="panel-title mb-2">Seen before</div>
      <ul className="space-y-1.5">
        {similar.map((s) => {
          const here = listed.has(s.incident_id);
          const open = shown === s.incident_id;
          return (
            <li key={s.incident_id}>
              <button
                type="button"
                onClick={() => (here ? view.select(s.incident_id) : setShown(open ? null : s.incident_id))}
                title={here ? "Select this incident" : "No longer in the incident list; showing what was recorded"}
                className="w-full rounded-lg bg-sheet px-2.5 py-2 text-left ring-1 ring-rule transition hover:ring-rule-strong"
              >
                <div className="flex items-baseline gap-2">
                  <span className="font-mono text-xs text-subtle">{s.incident_id}</span>
                  <span className="ml-auto font-mono text-sm font-bold text-accent">{pct(s.similarity)}</span>
                </div>
                <div className="text-sm text-ink">
                  <b>{s.root_service ?? "no single root"}</b>{" "}
                  <span className="text-muted">{s.signature ? signatureLabel(s.signature) : s.status.toLowerCase()}</span>
                </div>
                <div className="text-xs text-subtle">opened {istDateTime(s.opened_at)}</div>
                {open && (
                  <div className="mt-1 text-xs text-ink-2">
                    {s.status} · services: {s.services.join(", ") || "none recorded"}
                  </div>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function RootCausePanel({ incident }: { incident: Incident | null }) {
  return (
    <section data-tour="rootcause" className="panel flex min-h-0 flex-col p-3">
      <div className="mb-2 flex items-center justify-between px-1">
        <span className="panel-title">Root cause analysis</span>
        {incident && <span className="font-mono text-xs text-subtle">{incident.id}</span>}
      </div>
      <div className="thin-scroll min-h-0 flex-1 overflow-y-auto pr-1">
        {!incident ? (
          <Empty>
            <div>
              <div className="text-lg font-semibold text-ink-2">No incident selected</div>
              <div className="mt-1 text-sm">The engine ranks every anomalous service and shows its evidence here.</div>
            </div>
          </Empty>
        ) : (
          <div key={incident.id} className="fade-up">
            <LifecycleStrip inc={incident} />
            {incident.status === "ABSTAINED" ? <AbstainedView inc={incident} /> : incident.root_cause ? <OpenView inc={incident} /> : <Ranked inc={incident} />}
            <SeenBefore similar={incident.similar ?? []} />
          </div>
        )}
      </div>
    </section>
  );
}
