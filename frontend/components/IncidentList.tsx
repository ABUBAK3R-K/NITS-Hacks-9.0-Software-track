"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { Event, Incident, IncidentSummary, Noise } from "@/lib/contracts/types";
import type { Storm } from "@/lib/live";
import { istTime, pct } from "@/lib/format";
import { HINTS, Hint } from "./Hint";
import { LifecycleBadge } from "./Lifecycle";
import { Empty, SeverityPill, StatusBadge } from "./ui";

export function headline(i: Pick<IncidentSummary, "alert_count" | "status" | "services">, possible = 2): string {
  // Log-only incidents (uploaded logs, no alert events) have nothing to count as alerts,
  // so the headline counts the failing services the engine grouped instead.
  const n = i.alert_count > 0 ? i.alert_count : i.services.length;
  const noun = i.alert_count > 0 ? "alert" : "failing service";
  const a = `${n} ${noun}${n === 1 ? "" : "s"}`;
  if (i.status === "ABSTAINED") return `${a}, ${possible} possible causes`;
  return `${a}, 1 cause`;
}

function AlertRow({ e, i, collapsing, total }: { e: Event; i: number; collapsing: boolean; total: number }) {
  return (
    <div
      className={`${collapsing ? "alert-collapse" : "alert-in"} flex items-center gap-2 rounded-md border border-danger/40 px-2.5 py-1.5`}
      style={
        collapsing
          ? ({ animationDelay: `${(total - i) * 28}ms`, "--collapse-y": `${-(i * 38 + 40)}px` } as React.CSSProperties)
          : undefined
      }
    >
      <span className="h-2 w-2 shrink-0 rounded-full bg-danger" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold text-danger">{e.name}</div>
        <div className="truncate font-mono text-[11px] text-danger">
          {e.service} · {istTime(e.ts)}
        </div>
      </div>
      <span className="font-mono text-xs text-danger">{e.value ?? ""}</span>
    </div>
  );
}

/** U1/U5 noise reduction, straight from the engine's Incident.noise. */
export function NoiseChip({ noise }: { noise: Noise }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-md bg-accent-soft px-2 py-0.5 font-mono text-xs font-semibold text-accent ring-1 ring-accent/40"
      title="alerts / incidents, computed by the engine"
    >
      {noise.alerts} alert{noise.alerts === 1 ? "" : "s"} → {noise.incidents} incident{noise.incidents === 1 ? "" : "s"} ({noise.ratio.toFixed(1)}x)
    </span>
  );
}

function StormFeed({ storm, collapsing, noun }: { storm: Storm; collapsing: boolean; noun: string }) {
  const services = new Set(storm.alerts.map((a) => a.service));
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    listRef.current?.scrollTo({ top: 0 });
  }, [storm.alerts.length]);
  const rows = [...storm.alerts].reverse();
  return (
    <div className={`storm-flash mb-3 rounded-lg border-2 bg-danger-soft p-3 ${collapsing ? "opacity-90" : ""}`}>
      <div className="flex items-baseline justify-between">
        <span className="text-sm font-black tracking-widest text-danger">{noun === "alerts" ? "ALERT STORM" : "SIGNAL STORM"}</span>
        <span className="font-mono text-xs text-danger">{services.size} teams paged</span>
      </div>
      <div className="mt-0.5 text-3xl font-black tabular-nums text-ink">
        {storm.alerts.length} <span className="text-lg font-bold text-danger">{noun} firing</span>
      </div>
      <div ref={listRef} className="mt-2 max-h-[560px] space-y-1.5 overflow-hidden">
        {rows.map((e, i) => (
          <AlertRow key={e.id} e={e} i={i} total={rows.length} collapsing={collapsing} />
        ))}
      </div>
      {!collapsing && <div className="mt-2 text-xs text-danger">correlating against the dependency graph...</div>}
    </div>
  );
}

export function IncidentList({
  incidents,
  details,
  storm,
  stormNoun = "alerts",
  sandbox = false,
  selectedId,
  onSelect,
  possibleCauses,
}: {
  incidents: IncidentSummary[];
  details: Record<string, Incident>;
  storm: Storm;
  stormNoun?: string;
  sandbox?: boolean;
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
    <section data-tour="incidents" className="panel flex min-h-0 flex-col p-3">
      <div className="mb-2 flex items-center justify-between px-1">
        <span className="panel-title">Incidents</span>
        <span className="font-mono text-xs text-subtle">{incidents.length}</span>
      </div>
      <div className="thin-scroll min-h-0 flex-1 overflow-y-auto overflow-x-hidden pr-1">
        {flooding && <StormFeed storm={storm} collapsing={collapsing} noun={stormNoun} />}
        {!flooding && incidents.length === 0 && (
          <Empty>
            <div>
              <div className="text-lg font-semibold text-ink-2">{sandbox ? "Sandbox run" : "All quiet"}</div>
              <div className="mt-1 text-sm">
                {sandbox ? "Waiting for the engine's answer for this run." : "Inject a fault from the Chaos Lab to start an incident."}
              </div>
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
                className={`${i.id === popId && !collapsing ? "card-pop" : ""} min-w-0 cursor-pointer rounded-lg border-2 p-3 transition ${
                  sel
                    ? abst
                      ? "border-warn/80 bg-warn-soft"
                      : "border-danger/80 bg-danger-soft"
                    : "border-rule bg-sheet hover:border-rule-strong"
                }`}
              >
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <SeverityPill severity={i.severity} />
                  <StatusBadge status={i.status} />
                  {/* the lifecycle chip only adds information when it differs from the status (no "RESOLVED RESOLVED") */}
                  {details[i.id] && String(details[i.id].lifecycle) !== String(i.status) && (
                    <LifecycleBadge state={details[i.id].lifecycle} />
                  )}
                  <span className="ml-auto font-mono text-xs text-subtle">{istTime(i.opened_at)}</span>
                </div>
                <div className="mt-2 break-words text-[22px] font-black leading-tight text-ink">
                  {headline(i, possibleCauses(i.id))}
                </div>
                <div className="mt-1 text-sm text-ink-2">
                  {abst ? (
                    <span className="text-warn">no confident root cause, abstained</span>
                  ) : (
                    <>
                      root cause <span className="font-bold text-danger">{i.root_service}</span>
                      <span className="text-subtle"> at </span>
                      <span className="font-mono">{pct(i.confidence)}</span>
                    </>
                  )}
                </div>
                <div className="mt-1 truncate text-xs text-subtle">
                  {i.services.length} services: {i.services.join(", ")}
                </div>
                {details[i.id]?.noise && details[i.id].noise!.alerts > 0 && (
                  <div className="mt-2 flex items-center gap-1.5">
                    <NoiseChip noise={details[i.id].noise!} />
                    <Hint text={HINTS.noise} label="What is the noise chip?" />
                  </div>
                )}
                <div className="mt-2 flex items-center justify-between gap-2">
                  <span className="min-w-0 truncate font-mono text-[11px] text-subtle">{i.id}</span>
                  <Link
                    href={`/incident/${i.id}`}
                    onClick={(e) => e.stopPropagation()}
                    className="rounded-md px-3 py-1.5 text-sm font-bold text-accent ring-1 ring-accent/40 transition hover:bg-accent-soft hover:ring-accent/70"
                  >
                    Report &rarr;
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
