"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { Event, IncidentSummary } from "@/lib/contracts/types";
import type { Storm } from "@/lib/live";
import { istTime, pct } from "@/lib/format";
import { Empty, SeverityPill, StatusBadge } from "./ui";

export function headline(i: Pick<IncidentSummary, "alert_count" | "status">, possible = 2): string {
  const a = `${i.alert_count} alert${i.alert_count === 1 ? "" : "s"}`;
  if (i.status === "ABSTAINED") return `${a}, ${possible} possible causes`;
  return `${a}, 1 cause`;
}

function AlertRow({ e, i, collapsing, total }: { e: Event; i: number; collapsing: boolean; total: number }) {
  return (
    <div
      className={`${collapsing ? "alert-collapse" : "alert-in"} flex items-center gap-2 rounded-md border border-red-500/40 px-2.5 py-1.5`}
      style={
        collapsing
          ? ({ animationDelay: `${(total - i) * 28}ms`, "--collapse-y": `${-(i * 38 + 40)}px` } as React.CSSProperties)
          : undefined
      }
    >
      <span className="h-2 w-2 shrink-0 rounded-full bg-red-500" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold text-red-100">{e.name}</div>
        <div className="truncate font-mono text-[11px] text-red-300/80">
          {e.service} · {istTime(e.ts)}
        </div>
      </div>
      <span className="font-mono text-xs text-red-200">{e.value ?? ""}</span>
    </div>
  );
}

function StormFeed({ storm, collapsing }: { storm: Storm; collapsing: boolean }) {
  const services = new Set(storm.alerts.map((a) => a.service));
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    listRef.current?.scrollTo({ top: 0 });
  }, [storm.alerts.length]);
  const rows = [...storm.alerts].reverse();
  return (
    <div className={`storm-flash mb-3 rounded-xl border-2 bg-red-950/30 p-3 ${collapsing ? "opacity-90" : ""}`}>
      <div className="flex items-baseline justify-between">
        <span className="text-sm font-black tracking-widest text-red-300">ALERT STORM</span>
        <span className="font-mono text-xs text-red-300/80">{services.size} teams paged</span>
      </div>
      <div className="mt-0.5 text-3xl font-black tabular-nums text-white">
        {storm.alerts.length} <span className="text-lg font-bold text-red-200">alerts firing</span>
      </div>
      <div ref={listRef} className="mt-2 max-h-[560px] space-y-1.5 overflow-hidden">
        {rows.map((e, i) => (
          <AlertRow key={e.id} e={e} i={i} total={rows.length} collapsing={collapsing} />
        ))}
      </div>
      {!collapsing && <div className="mt-2 text-xs text-red-200/70">correlating against the dependency graph...</div>}
    </div>
  );
}

export function IncidentList({
  incidents,
  storm,
  selectedId,
  onSelect,
  possibleCauses,
}: {
  incidents: IncidentSummary[];
  storm: Storm;
  selectedId: string | null;
  onSelect: (id: string) => void;
  possibleCauses: (id: string) => number;
}) {
  // play the collapse animation for a moment after the storm resolves
  const [collapsing, setCollapsing] = useState(false);
  const [popId, setPopId] = useState<string | null>(null);
  const [prevPhase, setPrevPhase] = useState(storm.phase);
  if (prevPhase !== storm.phase) {
    setPrevPhase(storm.phase);
    if (prevPhase === "flood" && storm.phase === "collapsed") {
      setCollapsing(true);
      setPopId(storm.incidentId);
    }
  }
  useEffect(() => {
    if (!collapsing) return;
    const t = setTimeout(() => setCollapsing(false), 750);
    return () => clearTimeout(t);
  }, [collapsing]);

  const flooding = storm.phase === "flood" || collapsing;
  const visible = flooding ? [] : incidents;

  return (
    <section className="panel flex min-h-0 flex-col p-3">
      <div className="mb-2 flex items-center justify-between px-1">
        <span className="panel-title">Incidents</span>
        <span className="font-mono text-xs text-slate-500">{incidents.length}</span>
      </div>
      <div className="thin-scroll min-h-0 flex-1 overflow-y-auto pr-1">
        {flooding && <StormFeed storm={storm} collapsing={collapsing} />}
        {!flooding && incidents.length === 0 && (
          <Empty>
            <div>
              <div className="text-lg font-semibold text-slate-300">All quiet</div>
              <div className="mt-1 text-sm">Inject a fault from the Chaos Lab to start an incident.</div>
            </div>
          </Empty>
        )}
        <div className="space-y-2">
          {visible.map((i) => {
            const sel = i.id === selectedId;
            const abst = i.status === "ABSTAINED";
            return (
              <div
                key={i.id}
                role="button"
                tabIndex={0}
                onClick={() => onSelect(i.id)}
                onKeyDown={(e) => e.key === "Enter" && onSelect(i.id)}
                className={`${i.id === popId && !collapsing ? "card-pop" : ""} cursor-pointer rounded-xl border-2 p-3 transition ${
                  sel
                    ? abst
                      ? "border-amber-400/80 bg-amber-400/[0.07]"
                      : "border-red-500/80 bg-red-500/[0.07]"
                    : "border-slate-800 bg-slate-900/40 hover:border-slate-600"
                }`}
              >
                <div className="flex items-center gap-2">
                  <SeverityPill severity={i.severity} />
                  <StatusBadge status={i.status} />
                  <span className="ml-auto font-mono text-xs text-slate-500">{istTime(i.opened_at)}</span>
                </div>
                <div className="mt-2 text-[22px] font-black leading-tight text-white">
                  {headline(i, possibleCauses(i.id))}
                </div>
                <div className="mt-1 text-sm text-slate-300">
                  {abst ? (
                    <span className="text-amber-200">no confident root cause, abstained</span>
                  ) : (
                    <>
                      root cause <span className="font-bold text-red-300">{i.root_service}</span>
                      <span className="text-slate-500"> at </span>
                      <span className="font-mono">{pct(i.confidence)}</span>
                    </>
                  )}
                </div>
                <div className="mt-1 truncate text-xs text-slate-500">
                  {i.services.length} services: {i.services.join(", ")}
                </div>
                <div className="mt-2 flex items-center justify-between">
                  <span className="font-mono text-[11px] text-slate-600">{i.id}</span>
                  <Link
                    href={`/incident/${i.id}`}
                    onClick={(e) => e.stopPropagation()}
                    className="rounded-md px-2 py-0.5 text-xs font-semibold text-cyan-300 ring-1 ring-cyan-500/40 hover:bg-cyan-500/15"
                  >
                    Report
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
