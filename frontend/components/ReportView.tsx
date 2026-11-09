"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import type { Incident, Report } from "@/lib/contracts/types";
import { api } from "@/lib/api";
import { inr, int, istDateTime, istTimeMs, pct, signatureLabel } from "@/lib/format";
import { EvidenceChips, EvidenceProvider } from "./Evidence";
import { ProviderBadge, StatusBadge } from "./ui";

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
  lines.push(`- Grounding: ${r.dropped} unsupported sentence${r.dropped === 1 ? "" : "s"} removed`);
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
      <div className="mx-auto w-full max-w-[1280px] px-8 py-8">
        <div className="flex items-center justify-between">
          <Link href="/" className="text-sm font-semibold text-cyan-300 hover:text-cyan-100">
            &larr; Back to dashboard
          </Link>
          <span className="text-xl font-black tracking-[0.18em] text-cyan-300">J.A.R.V.I.S.</span>
        </div>

        <header className="mt-6 flex flex-wrap items-end justify-between gap-4 border-b border-slate-800 pb-5">
          <div>
            <div className="panel-title">Incident report · {id}</div>
            <h1 className="mt-1 text-4xl font-black text-white">{incident?.title ?? id}</h1>
            {incident && (
              <div className="mt-2 flex flex-wrap items-center gap-3 text-slate-300">
                <StatusBadge status={incident.status} big />
                <span>opened {istDateTime(incident.opened_at)}</span>
                {incident.root_cause && (
                  <span>
                    root cause <b className="text-red-300">{incident.root_cause.service}</b> at{" "}
                    <b className="font-mono">{pct(incident.root_cause.confidence)}</b>
                  </span>
                )}
              </div>
            )}
          </div>
          <div className="flex items-center gap-3">
            {report && <ProviderBadge provider={report.provider} />}
            <button
              type="button"
              onClick={regenerate}
              className="rounded-lg px-3 py-1.5 text-sm font-semibold text-slate-200 ring-1 ring-slate-600 hover:bg-slate-800"
            >
              Regenerate
            </button>
            <button
              type="button"
              disabled={!report}
              onClick={() => report && download(`jarvis-${id}-report.md`, toMarkdown(incident, report))}
              className="rounded-lg bg-cyan-500 px-4 py-1.5 text-sm font-bold text-black hover:bg-cyan-300 disabled:opacity-40"
            >
              Export Markdown
            </button>
          </div>
        </header>

        {error && <div className="mt-6 rounded-lg border border-red-500/40 bg-red-950/40 p-4 text-red-200">{error}</div>}

        {!report && !error && (
          <div className="mt-10 flex items-center gap-3 text-lg text-slate-400">
            <span className="live-dot h-3 w-3 rounded-full bg-cyan-400" />
            Narrating from evidence, every sentence must cite an event...
          </div>
        )}

        {report && (
          <div className="fade-up mt-6 grid grid-cols-[minmax(0,1fr)_340px] gap-8">
            <article>
              <p className="text-2xl font-semibold leading-relaxed text-white">{report.summary}</p>

              <div className="mt-5 flex items-center gap-3">
                <span
                  className={`rounded-md px-3 py-1 text-sm font-bold ring-1 ${
                    report.dropped
                      ? "bg-amber-400/15 text-amber-200 ring-amber-400/50"
                      : "bg-emerald-500/15 text-emerald-200 ring-emerald-500/50"
                  }`}
                >
                  {report.dropped} unsupported sentence{report.dropped === 1 ? "" : "s"} removed
                </span>
                <span className="text-sm text-slate-500">
                  every sentence below cites events from this incident; uncited claims are dropped
                </span>
              </div>

              <ol className="mt-6 space-y-4">
                {report.sentences.map((s, i) => (
                  <li key={i} className="flex gap-4 rounded-xl bg-slate-900/50 p-4 ring-1 ring-slate-800">
                    <span className="font-mono text-lg font-bold text-cyan-300">{i + 1}</span>
                    <div>
                      <p className="text-lg leading-relaxed text-slate-100">{s.text}</p>
                      <div className="mt-2">
                        <EvidenceChips ids={s.cites} max={10} />
                      </div>
                    </div>
                  </li>
                ))}
              </ol>
              <div className="mt-4 text-sm text-slate-500">generated {istDateTime(report.generated_at)}</div>
            </article>

            <aside className="space-y-4">
              {incident && (
                <div className="panel p-4">
                  <div className="panel-title mb-2">Impact</div>
                  <dl className="grid grid-cols-2 gap-3">
                    <div>
                      <dt className="text-xs text-slate-400">Users affected</dt>
                      <dd className="text-2xl font-black text-white">{int(incident.impact.users_affected)}</dd>
                    </div>
                    <div>
                      <dt className="text-xs text-slate-400">Failed payments</dt>
                      <dd className="text-2xl font-black text-red-300">{int(incident.impact.failed_payments)}</dd>
                    </div>
                    <div className="col-span-2">
                      <dt className="text-xs text-slate-400">INR at risk</dt>
                      <dd className="text-2xl font-black text-red-400">{inr(incident.impact.failed_value_inr)}</dd>
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
                          <span className="truncate text-slate-400">
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
