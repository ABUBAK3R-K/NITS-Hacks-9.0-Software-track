"use client";
import type { Incident } from "@/lib/contracts/types";
import { deltaSeconds, istTimeMs } from "@/lib/format";
import { EvidenceChips } from "./Evidence";
import { Empty } from "./ui";

export function Timeline({ incident }: { incident: Incident | null }) {
  const entries = incident?.timeline ?? [];
  const root = incident?.root_cause?.service;
  const t0 = entries[0]?.ts;
  return (
    <section className="panel flex min-h-0 flex-col p-3">
      <div className="mb-1.5 flex items-center justify-between px-1">
        <span className="panel-title">Timeline of onsets</span>
        <span className="text-xs text-slate-500">first anomaly, not first page</span>
      </div>
      {!entries.length ? (
        <Empty>Onsets appear here in causal order.</Empty>
      ) : (
        <ol className="thin-scroll relative min-h-0 flex-1 overflow-y-auto pr-1">
          <span className="absolute bottom-3 left-[118px] top-3 w-px bg-slate-700" />
          {entries.map((e, i) => {
            const isRoot = e.service === root || (i === 0 && !e.caused_by && incident?.status !== "ABSTAINED");
            return (
              <li key={`${e.service}-${e.ts}`} className="fade-up relative flex items-start gap-3 py-[3px]" style={{ animationDelay: `${i * 80}ms` }}>
                <div className="w-[104px] shrink-0 text-right">
                  <div className="font-mono text-[13px] text-slate-200">{istTimeMs(e.ts)}</div>
                  <div className="font-mono text-[11px] text-slate-500">{t0 ? deltaSeconds(t0, e.ts) : ""}</div>
                </div>
                <span
                  className={`relative z-10 mt-1 h-3.5 w-3.5 shrink-0 rounded-full ring-4 ring-[#0b0f17] ${isRoot ? "bg-red-500" : "bg-amber-400"}`}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2">
                    <span className={`text-[15px] font-bold ${isRoot ? "text-red-300" : "text-white"}`}>{e.service}</span>
                    {e.caused_by ? (
                      <span className="rounded bg-slate-800 px-1.5 py-0.5 text-xs font-semibold text-amber-200">
                        &larr; caused by {e.caused_by}
                      </span>
                    ) : (
                      <span className="rounded bg-red-500/20 px-1.5 py-0.5 text-xs font-bold text-red-200">no earlier cause</span>
                    )}
                    <EvidenceChips ids={e.event_ids} max={3} />
                  </div>
                  <div className="truncate text-[13px] text-slate-400" title={e.what}>
                    {e.what}
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
