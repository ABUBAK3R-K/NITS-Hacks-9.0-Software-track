"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import type { Incident, Report } from "@/lib/contracts/types";
import { api } from "@/lib/api";
import { inr, int, istDateTime, istTimeMs, pct, signatureLabel } from "@/lib/format";
import { EvidenceChips, EvidenceProvider } from "./Evidence";
import { NaiveCompare } from "./NaiveCompare";
import { ShieldIcon } from "./Sandbox";
import { HINTS, Hint } from "./Hint";
import { FallbackNote, ProviderBadge, StatusBadge } from "./ui";
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
    api
      .getIncident(id)
      .then((i) => alive && setIncident(i))
      .catch((e: Error) => alive && setError(e.message));
    api
      .getReport(id)
      .then((r) => alive && setReport(r))
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [id, nonce]);

  const regenerate = () => {
    setReport(null);
    setError(null);
    setNonce((n) => n + 1);
  };

  return (
    <EvidenceProvider events={incident?.evidence_events ?? []}>
      <div className="mx-auto w-full max-w-[1280px] px-4 py-6 md:px-8 md:py-8">
        <div className="flex items-center justify-between">
          <Link href="/dashboard" className="text-sm font-semibold text-accent hover:text-accent">
            &larr; Back to dashboard
          </Link>
          <Link href="/" title="Back to the case study landing page" className="text-xl font-black tracking-[0.18em] text-ink transition hover:text-accent">
            J.A.R.V.I.S.
          </Link>
        </div>

        <header className="mt-6 flex flex-wrap items-end justify-between gap-4 border-b border-rule pb-5">
          <div>
            <div className="panel-title">Incident report · {id}</div>
            <h1 className="mt-1 text-2xl font-black text-ink md:text-4xl">{incident?.title ?? id}</h1>
            {incident && (
              <div className="mt-2 flex flex-wrap items-center gap-3 text-ink-2">
                <StatusBadge status={incident.status} big />
                <span>opened {istDateTime(incident.opened_at)}</span>
                {incident.root_cause && (
                  <span>
                    root cause <b className="text-danger">{incident.root_cause.service}</b> at{" "}
                    <b className="font-mono">{pct(incident.root_cause.confidence)}</b>
                  </span>
                )}
              </div>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              role="switch"
              aria-checked={naiveMode}
              disabled={!incident}
              onClick={() => setNaiveMode((m) => !m)}
              className={`flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-bold ring-1 disabled:opacity-40 ${
                naiveMode ? "bg-accent-soft text-accent ring-accent/50" : "text-ink ring-rule-strong hover:bg-sheet"
              }`}
            >
              <span className={`relative h-4 w-7 rounded-full ${naiveMode ? "bg-accent" : "bg-rule-strong"}`}>
                <span className={`absolute top-0.5 h-3 w-3 rounded-full bg-ink transition-all ${naiveMode ? "left-3.5" : "left-0.5"}`} />
              </span>
              Naive AI mode
            </button>
            <Hint text={HINTS.naive} label="What is Naive AI mode?" />
            {report && <ProviderBadge provider={report.provider} />}
            <button
              type="button"
              onClick={regenerate}
              className="rounded-md px-3 py-1.5 text-sm font-semibold text-ink ring-1 ring-rule-strong hover:bg-raised"
            >
              Regenerate
            </button>
            <button
              type="button"
              disabled={!report}
              onClick={() => report && download(`jarvis-${id}-report.md`, toMarkdown(incident, report))}
              className="rounded-md bg-ink px-4 py-1.5 text-sm font-bold text-paper hover:bg-ink-2 disabled:opacity-40"
            >
              Export Markdown
            </button>
          </div>
        </header>

        {error && <div className="mt-6 rounded-lg border border-danger/40 bg-danger-soft p-4 text-danger">{error}</div>}

        {naiveMode && incident && <NaiveCompare incident={incident} />}

        {!naiveMode && !report && !error && (
          <div className="mt-10 flex items-center gap-3 text-lg text-muted">
            <span className="live-dot h-3 w-3 rounded-full bg-accent" />
            Narrating from evidence, every sentence must cite an event...
          </div>
        )}

        {!naiveMode && report && (
          <div className="fade-up mt-6 grid grid-cols-1 gap-8 md:grid-cols-[minmax(0,1fr)_340px]">
            <article>
              <p className="text-xl font-semibold leading-relaxed text-ink md:text-2xl">{report.summary}</p>

              <FallbackNote provider={report.provider} className="mt-3" />

              <div className="mt-5 flex flex-wrap items-center gap-3">
                <span className="inline-flex items-center gap-1.5 rounded-md bg-safe-soft px-3 py-1 text-sm font-bold text-safe ring-1 ring-safe/50">
                  <ShieldIcon />
                  Guardrail blocked {report.dropped} uncited sentence{report.dropped === 1 ? "" : "s"}
                </span>
                <span className="text-sm text-subtle">
                  every sentence below cites events from this incident; uncited claims are dropped
                </span>
              </div>

              <ol className="mt-6 space-y-4">
                {report.sentences.map((s, i) => (
                  <li key={i} className="flex gap-3 rounded-lg bg-sheet p-3 ring-1 ring-rule md:gap-4 md:p-4">
                    <span className="font-mono text-lg font-bold text-accent">{i + 1}</span>
                    <div className="min-w-0">
                      <p className="text-base leading-relaxed text-ink md:text-lg">{s.text}</p>
                      <div className="mt-2">
                        <EvidenceChips ids={s.cites} max={10} />
                      </div>
                    </div>
                  </li>
                ))}
              </ol>
              <div className="mt-4 text-sm text-subtle">generated {istDateTime(report.generated_at)}</div>
            </article>

            <aside className="space-y-4">
              {incident && (
                <div className="panel p-4">
                  <div className="panel-title mb-2">Impact</div>
                  <dl className="grid grid-cols-2 gap-3">
                    <div>
                      <dt className="text-xs text-muted">Users affected</dt>
                      <dd className="text-2xl font-black text-ink">{int(incident.impact.users_affected)}</dd>
                    </div>
                    <div>
                      <dt className="text-xs text-muted">Failed payments</dt>
                      <dd className="text-2xl font-black text-danger">{int(incident.impact.failed_payments)}</dd>
                    </div>
                    <div className="col-span-2">
                      <dt className="text-xs text-muted">INR at risk</dt>
                      <dd className="text-2xl font-black text-danger">{inr(incident.impact.failed_value_inr)}</dd>
                    </div>
                  </dl>
                </div>
              )}
              {incident && (
                <div className="panel p-4">
                  <div className="panel-title mb-2">Evidence set ({incident.evidence_events.length})</div>
                  <ul className="space-y-1.5">
                    {[...incident.evidence_events]
                      .sort((a, b) => a.ts.localeCompare(b.ts))
                      .map((e) => (
                        <li key={e.id} className="flex items-center gap-2 text-sm">
                          <EvidenceChips ids={[e.id]} />
                          <span className="truncate text-muted">
                            {e.service} {e.name === "log" ? "log" : e.name}
                          </span>
                        </li>
                      ))}
                  </ul>
                </div>
              )}
            </aside>
          </div>
        )}
      </div>
    </EvidenceProvider>
  );
}
