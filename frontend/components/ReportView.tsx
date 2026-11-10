"use client";
import Link from "next/link";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { Event, Incident, Report } from "@/lib/contracts/types";
import { api } from "@/lib/api";
import { inr, int, istDateTime, istTimeMs, pct, signatureLabel } from "@/lib/format";
import { EvidenceChips, EvidenceProvider } from "./Evidence";
import { ApplyFixButton, LifecycleBadge, LifecycleStrip, MetricChips } from "./Lifecycle";
import { NaiveCompare } from "./NaiveCompare";
import { ShiftCheck } from "./ShiftCheck";
import { ShieldIcon } from "./Sandbox";
import { HINTS, Hint } from "./Hint";
import { WhyNot } from "./RootCausePanel";
import { ConfidenceBar, FallbackNote, ProviderBadge, SeverityPill, StatusBadge } from "./ui";
import { markStep } from "@/lib/progress";

function toMarkdown(inc: Incident | null, r: Report): string {
  const lines: string[] = [];
  lines.push(`# Incident report: ${inc?.title ?? r.incident_id}`);
  lines.push("");
  if (inc) {
    lines.push(`- Incident: ${inc.id} (${inc.status}, ${inc.severity})`);
    lines.push(`- Opened: ${istDateTime(inc.opened_at)}`);
    if (inc.root_cause)
      lines.push(`- Root cause: ${inc.root_cause.service}, ${signatureLabel(inc.root_cause.signature)}, confidence ${pct(inc.root_cause.confidence)}`);
    else lines.push(`- Root cause: not named (${inc.abstain_reason ?? "abstained"})`);
    lines.push(
      `- Impact: ${inc.impact.services.length} services, ${int(inc.impact.users_affected)} users, ${int(inc.impact.failed_payments)} failed payments, ${inr(inc.impact.failed_value_inr)} at risk`,
    );
  }
  lines.push(`- Narrated by: ${r.provider}, generated ${istDateTime(r.generated_at)}`);
  lines.push(`- Guardrail blocked ${r.dropped} uncited sentence${r.dropped === 1 ? "" : "s"}`);
  lines.push("");
  lines.push("## Summary");
  lines.push("");
  lines.push(r.summary);
  lines.push("");
  lines.push("## What happened");
  lines.push("");
  r.sentences.forEach((s, i) => lines.push(`${i + 1}. ${s.text} ${s.cites.map((c) => `[${c}]`).join(" ")}`));
  if (inc?.disambiguation.length) {
    lines.push("");
    lines.push("## What data would disambiguate");
    lines.push("");
    inc.disambiguation.forEach((d) => lines.push(`- ${d}`));
  }
  if (inc?.recommendations.length) {
    lines.push("");
    lines.push("## Recommended actions");
    lines.push("");
    inc.recommendations.forEach((rec, i) => {
      lines.push(`${i + 1}. **${rec.action}** ${rec.evidence.map((c) => `[${c}]`).join(" ")}`);
      lines.push(`   - Why: ${rec.why}`);
      lines.push(`   - Runbook: \`${rec.runbook.replaceAll("`", "'")}\``);
    });
  }
  if (inc) {
    lines.push("");
    lines.push("## Evidence");
    lines.push("");
    lines.push("| id | time (IST) | service | kind | name | value | message |");
    lines.push("|---|---|---|---|---|---|---|");
    [...inc.evidence_events]
      .sort((a, b) => a.ts.localeCompare(b.ts))
      .forEach((e) =>
        lines.push(
          `| ${e.id} | ${istTimeMs(e.ts)} | ${e.service} | ${e.kind} | ${e.name} | ${e.value ?? ""} | ${(e.message ?? "").replaceAll("|", "/")} |`,
        ),
      );
  }
  lines.push("");
  return lines.join("\n");
}

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function Section({
  title,
  subtitle,
  action,
  children,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-rule bg-sheet">
      <div className="flex flex-wrap items-end justify-between gap-2 border-b border-rule px-5 py-4">
        <div className="min-w-0">
          <h2 className="text-[13px] font-bold uppercase tracking-[0.14em] text-ink">{title}</h2>
          {subtitle && <p className="mt-1 text-sm leading-snug text-muted">{subtitle}</p>}
        </div>
        {action}
      </div>
      <div className="p-5">{children}</div>
    </section>
  );
}

function SideCard({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-rule bg-sheet">
      <div className="flex items-center justify-between gap-2 border-b border-rule px-4 py-3">
        <h2 className="panel-title">{title}</h2>
        {action}
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-subtle">{label}</div>
      <div className="mt-1 truncate text-sm font-semibold text-ink">{children}</div>
    </div>
  );
}

function Impact({ incident }: { incident: Incident }) {
  const im = incident.impact;
  const tiles = [
    { label: "Services impacted", value: int(im.services.length), sub: im.services.join(", ") || "none", tone: "text-warn" },
    { label: "Users affected", value: int(im.users_affected), sub: "failed login or payment", tone: "text-ink" },
    { label: "Failed payments", value: int(im.failed_payments), sub: "on payments-svc", tone: "text-danger" },
    { label: "INR at risk", value: inr(im.failed_value_inr), sub: "value of failed payments", tone: "text-danger" },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {tiles.map((t) => (
        <div key={t.label} className="min-w-0 rounded-xl border border-rule bg-sheet px-5 py-4">
          <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-subtle">{t.label}</div>
          <div className={`mt-1 truncate text-2xl font-black tabular-nums md:text-3xl ${t.tone}`}>{t.value}</div>
          <div className="mt-0.5 truncate text-xs text-subtle">{t.sub}</div>
        </div>
      ))}
    </div>
  );
}

function Verdict({ incident }: { incident: Incident }) {
  const rc = incident.root_cause;
  const top = incident.candidates[0];
  const second = incident.candidates[1];

  if (incident.status === "ABSTAINED") {
    return (
      <section className="overflow-hidden rounded-xl border border-warn/40 bg-sheet">
        <div className="p-5 md:p-6">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="panel-title text-warn">Verdict</span>
            <StatusBadge status="ABSTAINED" big />
          </div>
          <h2 className="mt-2 max-w-3xl text-2xl font-black leading-tight text-ink md:text-3xl">
            Not enough evidence to name one cause
          </h2>
          {incident.abstain_reason && <p className="mt-1 text-sm text-warn">{incident.abstain_reason}</p>}

          <div className="mt-5 grid gap-3 md:grid-cols-2">
            {incident.candidates.slice(0, 2).map((c, i) => (
              <div key={c.service} className="rounded-lg border border-warn/30 bg-warn-soft p-4">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="font-mono text-xs font-bold text-warn">candidate #{i + 1}</span>
                  <span className="font-mono text-2xl font-black text-ink">{c.score.toFixed(3)}</span>
                </div>
                <div className="mt-1 text-2xl font-black text-ink">{c.service}</div>
                <div className="text-sm text-warn">{signatureLabel(c.signature)}</div>
                <div className="mt-3">
                  <EvidenceChips ids={c.evidence} max={6} />
                </div>
              </div>
            ))}
          </div>

          {incident.disambiguation.length > 0 && (
            <div className="mt-4 rounded-lg border border-accent/30 bg-accent-soft p-4">
              <div className="panel-title text-accent">What data would disambiguate</div>
              <ul className="mt-2 space-y-1.5">
                {incident.disambiguation.map((d) => (
                  <li key={d} className="flex gap-2 text-sm leading-snug text-ink-2">
                    <span className="font-bold text-accent">?</span>
                    <span>{d}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </section>
    );
  }

  if (!rc) {
    return (
      <section className="rounded-xl border border-rule bg-sheet p-5 text-muted md:p-6">
        No root cause named yet. The engine is still ranking candidates for this incident.
      </section>
    );
  }

  return (
    <section className="overflow-hidden rounded-xl border border-danger/30 bg-sheet">
      <div className="grid md:grid-cols-[minmax(0,1fr)_340px]">
        <div className="p-5 md:p-6">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="panel-title text-danger">Root cause</span>
            <StatusBadge status={incident.status} big />
          </div>
          <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h2 className="text-3xl font-black leading-tight text-ink md:text-4xl">{rc.service}</h2>
            <span className="text-lg font-semibold text-danger">{signatureLabel(rc.signature)}</span>
          </div>

          <div className="mt-5 flex items-center gap-3">
            <span className="w-24 shrink-0 text-sm text-muted">Confidence</span>
            <div className="max-w-md flex-1">
              <ConfidenceBar value={rc.confidence} />
            </div>
            <span className="w-16 text-right font-mono text-2xl font-black text-ink">{pct(rc.confidence)}</span>
          </div>
          <div className="mt-1 pl-[108px] text-xs text-subtle">
            margin {second ? (top.score - second.score).toFixed(2) : "n/a"} over {second?.service ?? "no second candidate"}
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="text-sm text-muted">Evidence</span>
            <EvidenceChips ids={rc.evidence} max={12} />
          </div>
        </div>

        <div className="border-t border-rule bg-paper/40 p-5 md:border-l md:border-t-0">
          <div className="panel-title">Ranked candidates</div>
          <ol className="mt-3 space-y-3">
            {incident.candidates.slice(0, 4).map((c, i) => (
              <li key={c.service}>
                <div className="flex items-baseline justify-between gap-2 text-sm">
                  <span className="truncate font-semibold text-ink">
                    {i + 1}. {c.service}
                  </span>
                  <span className="shrink-0 font-mono text-ink">{c.score.toFixed(3)}</span>
                </div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-raised">
                  <div className="h-full rounded-full bg-accent" style={{ width: `${Math.min(1, c.score) * 100}%` }} />
                </div>
                <div className="mt-0.5 text-xs text-subtle">{signatureLabel(c.signature)}</div>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  );
}

function Narrative({ report }: { report: Report }) {
  return (
    <Section
      title="What happened"
      subtitle="Narrated only from the engine's evidence. Every sentence must cite its events."
      action={<ProviderBadge provider={report.provider} />}
    >
      <p className="text-xl font-semibold leading-relaxed text-ink md:text-2xl">{report.summary}</p>
      <FallbackNote provider={report.provider} className="mt-3" />

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1.5 rounded-md bg-safe-soft px-3 py-1 text-sm font-bold text-safe ring-1 ring-safe/50">
          <ShieldIcon />
          Guardrail blocked {report.dropped} uncited sentence{report.dropped === 1 ? "" : "s"}
        </span>
        <span className="text-sm text-subtle">uncited claims are dropped, not reworded</span>
      </div>

      <ol className="mt-6 space-y-3">
        {report.sentences.map((s, i) => (
          <li key={i} className="flex gap-4 rounded-lg bg-paper p-4 ring-1 ring-rule">
            <span className="shrink-0 font-mono text-sm font-bold text-accent">{String(i + 1).padStart(2, "0")}</span>
            <div className="min-w-0">
              <p className="text-base leading-relaxed text-ink md:text-lg">{s.text}</p>
              <div className="mt-2">
                <EvidenceChips ids={s.cites} max={10} />
              </div>
            </div>
          </li>
        ))}
      </ol>

      <div className="mt-4 text-xs text-subtle">Generated {istDateTime(report.generated_at)}</div>
    </Section>
  );
}

function Recommendations({ incident }: { incident: Incident }) {
  if (!incident.recommendations.length) return null;
  return (
    <Section title="Recommended actions" subtitle="Each action carries the evidence that justifies it.">
      <ol className="space-y-3">
        {incident.recommendations.map((r, i) => (
          <li key={i} className="rounded-lg border border-rule bg-paper p-4">
            <div className="flex gap-3">
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent-soft font-mono text-sm font-bold text-accent ring-1 ring-accent/40">
                {i + 1}
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-base font-bold leading-snug text-ink">{r.action}</div>
                <p className="mt-1 text-sm leading-relaxed text-muted">{r.why}</p>
                <div className="mt-2">
                  <EvidenceChips ids={r.evidence} max={8} />
                </div>
                <details className="group mt-3">
                  <summary className="cursor-pointer list-none text-sm font-semibold text-accent [&::-webkit-details-marker]:hidden">
                    Runbook <span className="text-subtle transition group-open:rotate-180">&#9662;</span>
                  </summary>
                  <pre className="mt-2 whitespace-pre-wrap rounded-md bg-sheet p-3 font-mono text-xs leading-relaxed text-safe ring-1 ring-rule">
                    {r.runbook}
                  </pre>
                </details>
                <ApplyFixButton inc={incident} rec={r} />
              </div>
            </div>
          </li>
        ))}
      </ol>
    </Section>
  );
}

function Timeline({ incident }: { incident: Incident }) {
  if (!incident.timeline.length) return null;
  return (
    <Section title="How it unfolded" subtitle="Ordered by the first anomaly, not the first page.">
      <ol className="space-y-4">
        {incident.timeline.map((e) => (
          <li key={`${e.service}-${e.ts}`} className="flex gap-4">
            <span
              className={`mt-1.5 h-3.5 w-3.5 shrink-0 rounded-full ring-4 ring-sheet ${
                e.service === incident.root_cause?.service ? "bg-danger" : "bg-warn"
              }`}
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <span className="font-mono text-xs text-subtle">{istTimeMs(e.ts)}</span>
                <span className="text-sm font-bold text-ink">{e.service}</span>
                {e.caused_by ? (
                  <span className="rounded bg-raised px-1.5 py-0.5 text-xs font-semibold text-warn">caused by {e.caused_by}</span>
                ) : (
                  <span className="rounded bg-danger-soft px-1.5 py-0.5 text-xs font-bold text-danger">first cause</span>
                )}
              </div>
              <p className="mt-0.5 text-sm leading-snug text-ink-2">{e.what}</p>
              <div className="mt-1">
                <EvidenceChips ids={e.event_ids} max={4} />
              </div>
            </div>
          </li>
        ))}
      </ol>
    </Section>
  );
}

function EvidenceTable({ events }: { events: Event[] }) {
  return (
    <Section title={`Evidence (${events.length})`} subtitle="Raw events the engine used. Click any chip to inspect the full JSON.">
      <div className="thin-scroll -mx-1 overflow-x-auto">
        <table className="w-full min-w-[720px] border-collapse text-sm">
          <thead>
            <tr className="border-b border-rule text-left text-[11px] uppercase tracking-wider text-subtle">
              <th className="px-2 py-2 font-semibold">Event</th>
              <th className="px-2 py-2 font-semibold">Time</th>
              <th className="px-2 py-2 font-semibold">Service</th>
              <th className="px-2 py-2 font-semibold">Kind</th>
              <th className="px-2 py-2 font-semibold">Signal</th>
              <th className="px-2 py-2 font-semibold">Value or message</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <tr key={e.id} className="border-b border-rule/70 last:border-0 hover:bg-raised/40">
                <td className="px-2 py-2">
                  <EvidenceChips ids={[e.id]} />
                </td>
                <td className="whitespace-nowrap px-2 py-2 font-mono text-xs text-muted">{istTimeMs(e.ts)}</td>
                <td className="px-2 py-2 text-ink-2">{e.service}</td>
                <td className="px-2 py-2">
                  <span className="rounded bg-raised px-1.5 py-0.5 text-xs text-muted">{e.kind}</span>
                </td>
                <td className="px-2 py-2 text-ink-2">{e.name}</td>
                <td className="max-w-[280px] truncate px-2 py-2 font-mono text-xs text-ink" title={e.message ?? String(e.value ?? "")}>
                  {e.value ?? e.message ?? ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

function Similar({ incident }: { incident: Incident }) {
  if (!incident.similar?.length) return null;
  return (
    <SideCard title="Seen before">
      <ul className="space-y-2">
        {incident.similar.map((s) => (
          <li key={s.incident_id}>
            <Link
              href={`/incident/${s.incident_id}`}
              className="block rounded-lg bg-paper px-3 py-2 ring-1 ring-rule transition hover:ring-rule-strong"
            >
              <div className="flex items-baseline justify-between gap-2">
                <span className="truncate text-sm font-semibold text-ink">{s.root_service ?? "no single root"}</span>
                <span className="shrink-0 font-mono text-xs font-bold text-accent">{pct(s.similarity)}</span>
              </div>
              <div className="truncate text-xs text-subtle">
                {signatureLabel(s.signature)} · opened {istDateTime(s.opened_at)}
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </SideCard>
  );
}

function ReportDetails({ report }: { report: Report }) {
  return (
    <SideCard title="Report details">
      <dl className="space-y-2.5 text-sm">
        <div className="flex items-center justify-between gap-3">
          <dt className="text-muted">Narrator</dt>
          <dd>
            <ProviderBadge provider={report.provider} />
          </dd>
        </div>
        <div className="flex items-center justify-between gap-3">
          <dt className="text-muted">Generated</dt>
          <dd className="text-right font-mono text-xs text-ink-2">{istDateTime(report.generated_at)}</dd>
        </div>
        <div className="flex items-center justify-between gap-3">
          <dt className="text-muted">Sentences kept</dt>
          <dd className="font-mono text-ink">{report.sentences.length}</dd>
        </div>
        <div className="flex items-center justify-between gap-3">
          <dt className="text-muted">Uncited blocked</dt>
          <dd className="font-mono text-ink">{report.dropped}</dd>
        </div>
      </dl>
      <FallbackNote provider={report.provider} className="mt-3" />
    </SideCard>
  );
}

function Loading({ label }: { label: string }) {
  return (
    <div className="rounded-xl border border-rule bg-sheet p-8">
      <div className="flex items-center gap-3 text-ink-2">
        <span className="live-dot h-2.5 w-2.5 rounded-full bg-accent" />
        <span className="text-sm font-semibold">{label}</span>
      </div>
      <div className="mt-4 space-y-2.5">
        <div className="h-3 w-11/12 animate-pulse rounded bg-raised" />
        <div className="h-3 w-4/5 animate-pulse rounded bg-raised" />
        <div className="h-3 w-2/3 animate-pulse rounded bg-raised" />
      </div>
    </div>
  );
}

function ErrorCard({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="rounded-xl border border-danger/40 bg-danger-soft p-5">
      <div className="text-lg font-bold text-danger">Could not load this report</div>
      <p className="mt-1 text-sm text-ink-2">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-3 rounded-md bg-ink px-4 py-1.5 text-sm font-bold text-paper hover:bg-ink-2"
      >
        Try again
      </button>
    </div>
  );
}

export function ReportView({ id }: { id: string }) {
  const [incident, setIncident] = useState<Incident | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const [naiveMode, setNaiveMode] = useState(false);

  // "Try it in 60 seconds" step 5: the visitor opened a report page
  useEffect(() => markStep("report"), []);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      const [incRes, repRes] = await Promise.allSettled([api.getIncident(id), api.getReport(id)]);
      if (!alive) return;
      if (incRes.status === "fulfilled") setIncident(incRes.value);
      if (repRes.status === "fulfilled") setReport(repRes.value);
      const failure = incRes.status === "rejected" ? incRes.reason : repRes.status === "rejected" ? repRes.reason : null;
      setError(failure ? (failure instanceof Error ? failure.message : String(failure)) : null);
    };
    load();
    return () => {
      alive = false;
    };
  }, [id, nonce]);

  const regenerate = () => {
    setReport(null);
    setError(null);
    setNonce((n) => n + 1);
  };

  const evidence = useMemo(
    () => (incident ? [...incident.evidence_events].sort((a, b) => a.ts.localeCompare(b.ts)) : []),
    [incident],
  );

  return (
    <EvidenceProvider events={incident?.evidence_events ?? []}>
      <div className="min-h-screen bg-paper">
        <div className="sticky top-0 z-30 border-b border-rule bg-paper/90 backdrop-blur">
          <div className="mx-auto flex max-w-[1280px] flex-wrap items-center gap-3 px-4 py-3 md:px-8">
            <Link
              href="/dashboard"
              className="rounded-md px-2 py-1 text-sm font-semibold text-ink-2 transition hover:bg-sheet hover:text-ink"
            >
              &larr; Dashboard
            </Link>
            <span className="hidden text-rule-strong sm:inline" aria-hidden="true">
              /
            </span>
            <Link href="/" title="Back to the case study landing page" className="text-base font-black tracking-[0.18em] text-ink transition hover:text-accent">
              J.A.R.V.I.S.
            </Link>
            <span className="hidden text-sm text-subtle md:inline">Incident report</span>

            <div className="ml-auto flex flex-wrap items-center gap-2">
              <button
                type="button"
                role="switch"
                aria-checked={naiveMode}
                disabled={!incident}
                onClick={() => setNaiveMode((m) => !m)}
                className={`flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-bold ring-1 transition disabled:opacity-40 ${
                  naiveMode ? "bg-accent-soft text-accent ring-accent/50" : "text-ink ring-rule-strong hover:bg-sheet"
                }`}
              >
                <span className={`relative h-4 w-7 rounded-full ${naiveMode ? "bg-accent" : "bg-rule-strong"}`}>
                  <span className={`absolute top-0.5 h-3 w-3 rounded-full bg-ink transition-all ${naiveMode ? "left-3.5" : "left-0.5"}`} />
                </span>
                Naive AI mode
              </button>
              <Hint text={HINTS.naive} label="What is Naive AI mode?" />
              <button
                type="button"
                onClick={regenerate}
                disabled={!incident}
                className="rounded-md px-3 py-1.5 text-sm font-semibold text-ink ring-1 ring-rule-strong transition hover:bg-raised disabled:opacity-40"
              >
                Regenerate
              </button>
              <button
                type="button"
                disabled={!report}
                onClick={() => report && download(`jarvis-${id}-report.md`, toMarkdown(incident, report))}
                className="rounded-md bg-ink px-4 py-1.5 text-sm font-bold text-paper transition hover:bg-ink-2 disabled:opacity-40"
              >
                Export Markdown
              </button>
            </div>
          </div>
        </div>

        <main className="mx-auto w-full max-w-[1280px] px-4 pb-16 pt-8 md:px-8">
          {!incident && !error && (
            <div className="mx-auto max-w-md py-24 text-center">
              <div className="mx-auto h-10 w-10 animate-spin rounded-full border-2 border-rule border-t-accent" />
              <div className="mt-4 text-lg font-semibold text-ink">Building the incident report</div>
              <p className="mt-1 text-sm leading-relaxed text-muted">
                Resolving evidence, ranking candidates and narrating only what the engine proved.
              </p>
            </div>
          )}

          {error && !incident && <ErrorCard message={error} onRetry={regenerate} />}

          {incident && (
            <>
              <header className="border-b border-rule pb-6">
                <div className="flex flex-wrap items-start justify-between gap-6">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.18em] text-accent">
                      <span className="h-1.5 w-1.5 rounded-full bg-accent" />
                      Incident report
                    </div>
                    <h1 className="mt-2 max-w-4xl text-3xl font-black leading-tight text-ink md:text-4xl">
                      {incident.title}
                    </h1>
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <StatusBadge status={incident.status} big />
                      <SeverityPill severity={incident.severity} />
                      <LifecycleBadge state={incident.lifecycle} />
                      <MetricChips inc={incident} />
                      <span className="text-sm text-muted">Opened {istDateTime(incident.opened_at)}</span>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-x-8 gap-y-3 sm:grid-cols-4">
                    <Meta label="Incident">{id}</Meta>
                    <Meta label="Alerts">{int(incident.alert_count)}</Meta>
                    <Meta label="Services">{incident.services.length}</Meta>
                    <Meta label="Updated">{istDateTime(incident.updated_at)}</Meta>
                  </div>
                </div>
              </header>

              <div className="mt-6 space-y-6">
                <Verdict incident={incident} />
                <Impact incident={incident} />
              </div>

              {naiveMode ? (
                <div className="mt-6">
                  <div className="mb-3 rounded-xl border border-accent/30 bg-accent-soft px-4 py-3 text-sm text-accent">
                    Naive AI mode: one plain LLM prompt over the raw events, next to the deterministic engine and its evidence.
                  </div>
                  <NaiveCompare incident={incident} />
                </div>
              ) : (
                <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
                  <div className="min-w-0 space-y-6">
                    {report ? (
                      <Narrative report={report} />
                    ) : error ? (
                      <ErrorCard message={error} onRetry={regenerate} />
                    ) : (
                      <Loading label="Narrating from evidence, every sentence must cite an event" />
                    )}
                    <Recommendations incident={incident} />
                    <Timeline incident={incident} />
                    <EvidenceTable events={evidence} />
                  </div>

                  <aside className="space-y-4 lg:sticky lg:top-24 lg:self-start">
                    <LifecycleStrip inc={incident} />
                    <ShiftCheck incident={incident} />
                    <div className="rounded-xl border border-rule bg-sheet p-4">
                      <WhyNot inc={incident} className="" />
                    </div>
                    <Similar incident={incident} />
                    {report && <ReportDetails report={report} />}
                  </aside>
                </div>
              )}
            </>
          )}
        </main>
      </div>
    </EvidenceProvider>
  );
}
