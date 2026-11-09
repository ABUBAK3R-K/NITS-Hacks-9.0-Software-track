"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useLiveData } from "@/lib/live";
import { EvidenceProvider } from "./Evidence";
import { ImpactStrip } from "./ImpactStrip";
import { IncidentList } from "./IncidentList";
import { RootCausePanel } from "./RootCausePanel";
import { JudgeBar, RevealCard } from "./Sandbox";
import { ServiceGraph } from "./ServiceGraph";
import { Sparklines } from "./Sparklines";
import { Timeline } from "./Timeline";
import { TopBar } from "./TopBar";
import { Tour } from "./Tour";

// The dashboard is laid out on a 1920 px wide canvas and zoomed to the window width, so a
// 1536 px laptop (1920 at 125% scaling) records the same picture as a 1080p monitor.
const DESIGN_W = 1920;
function useDesignCanvas(enabled: boolean) {
  const [fit, setFit] = useState<{ scale: number; h: number } | null>(null);
  useEffect(() => {
    if (!enabled) return;
    const prev = document.documentElement.style.overflow;
    document.documentElement.style.overflow = "hidden";
    const on = () => {
      const el = document.documentElement;
      const scale = el.clientWidth / DESIGN_W;
      setFit({ scale, h: Math.max(860, el.clientHeight / scale) });
    };
    on();
    window.addEventListener("resize", on);
    return () => {
      window.removeEventListener("resize", on);
      document.documentElement.style.overflow = prev;
    };
  }, [enabled]);
  return enabled ? fit : null;
}

// Secondary sections on phones fold away so the answer (incident + root cause) comes first.
function MobileSection({ title, open = false, children }: { title: string; open?: boolean; children: React.ReactNode }) {
  return (
    <details open={open} className="group rounded-lg border border-rule bg-sheet">
      <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-sm font-semibold text-ink [&::-webkit-details-marker]:hidden">
        <span className="panel-title">{title}</span>
        <span className="text-muted transition-transform group-open:rotate-180" aria-hidden="true">&#9662;</span>
      </summary>
      <div className="border-t border-rule">{children}</div>
    </details>
  );
}

// Phones get their own single-column layout instead of a shrunken 1920 px canvas.
const MOBILE_MAX = 900;
function useIsMobile() {
  const [mobile, setMobile] = useState(false);
  useEffect(() => {
    const on = () => setMobile(window.innerWidth < MOBILE_MAX);
    on();
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  return mobile;
}

function Screen() {
  const live = useLiveData();
  // a sandbox run (Judge's Chaos / designed outage) takes over the panels until "Back to live"
  const sandbox = live.sandbox.active;
  const view = sandbox ? live.sandbox.view : live;
  const { selected, storm } = view;
  const stormNoun = sandbox && live.sandbox.signalStorm ? "signals" : "alerts";

  const evidence = useMemo(() => [...view.events, ...(selected?.evidence_events ?? [])], [view.events, selected]);

  // per-service alert badges on the graph while the storm is on screen
  const alertCounts = useMemo(() => {
    const m: Record<string, number> = {};
    if (storm.phase === "flood") for (const a of storm.alerts) m[a.service] = (m[a.service] ?? 0) + 1;
    return m;
  }, [storm]);

  const possibleCauses = useCallback(
    (id: string) => {
      const d = view.details[id];
      if (!d) return 2;
      return Math.max(2, d.candidates.filter((c) => d.candidates[0].score - c.score < 0.15).length);
    },
    [view.details],
  );

  const graphIncident = storm.phase === "flood" ? null : selected;
  const mobile = useIsMobile();
  const fit = useDesignCanvas(!mobile);

  const graphSection = (cls: string) => (
            <section data-tour="graph" className={`panel relative overflow-hidden ${cls} ${storm.phase === "flood" ? "storm-flash border-2" : ""}`}>
              <div className="absolute left-4 top-3 z-10 flex flex-wrap items-center gap-3">
                <span className="panel-title hidden md:inline">Service graph</span>
                {sandbox && (
                  <span className="rounded bg-accent-soft px-2 py-0.5 text-xs font-black tracking-widest text-accent ring-1 ring-accent/60">
                    SANDBOX RUN
                  </span>
                )}
                {graphIncident && (
                  <span className="text-sm text-ink-2">
                    {graphIncident.status === "ABSTAINED" ? (
                      <span className="text-warn">two possible causes highlighted</span>
                    ) : (
                      <>
                        failure path from <span className="font-bold text-danger">{graphIncident.root_cause?.service}</span>
                      </>
                    )}
                  </span>
                )}
              </div>
              {storm.phase === "flood" && (
                <div className="absolute right-4 top-3 z-10 rounded-lg bg-danger px-3 py-1 text-sm font-black tracking-widest text-paper">
                  {storm.alerts.length} {stormNoun.toUpperCase()} FIRING
                </div>
              )}
              {storm.phase !== "flood" && <RevealCard />}
              <div className="absolute bottom-3 left-4 z-10 flex flex-wrap gap-3 text-xs text-muted">
                <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-safe" />healthy</span>
                <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-warn" />degraded</span>
                <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-danger" />critical</span>
                <span className="hidden text-subtle md:inline">arrows point from caller to dependency</span>
              </div>
              <ServiceGraph
                key={`${live.topology ? "ready" : "empty"}-${fit?.scale ?? 1}-${fit?.h ?? 0}-${mobile ? "m" : "d"}`}
                topology={live.topology}
                topo={view.topo}
                incident={graphIncident}
                alertCounts={alertCounts}
              />
            </section>
  );

  const incidentList = (
          <IncidentList
            incidents={view.incidents}
            details={view.details}
            storm={storm}
            stormNoun={stormNoun}
            sandbox={sandbox}
            selectedId={view.selectedId}
            onSelect={view.select}
            possibleCauses={possibleCauses}
          />
  );

  if (mobile) {
    // Phone: one scrollable column, quickest path first (sandbox actions, then the answer, then detail).
    return (
      <EvidenceProvider events={evidence}>
        <div className="flex min-h-screen flex-col">
          <TopBar />
          <JudgeBar />
          <main className="flex flex-col gap-3 p-3">
            {incidentList}
            <RootCausePanel incident={graphIncident} />
            <ImpactStrip incident={graphIncident} />
            <MobileSection title="Service graph" open>
              {graphSection("h-[360px] shrink-0 rounded-none border-0")}
            </MobileSection>
            <MobileSection title="Timeline of onsets">
              <div className="h-[300px]"><Timeline incident={graphIncident} /></div>
            </MobileSection>
            <MobileSection title="Symptoms">
              <div className="h-[260px]"><Sparklines events={view.events} incident={graphIncident} /></div>
            </MobileSection>
          </main>
        </div>
        <Tour />
      </EvidenceProvider>
    );
  }

  return (
    <EvidenceProvider events={evidence}>
      <div
        className="flex h-screen min-h-0 flex-col overflow-hidden"
        style={fit ? { width: DESIGN_W, height: fit.h, zoom: fit.scale } : undefined}
      >
        <TopBar />
        <JudgeBar />
        <main className="grid min-h-0 flex-1 grid-cols-[330px_minmax(0,1fr)_560px] gap-3 p-3">
          {incidentList}
          <div className="flex min-h-0 flex-col gap-3">
            <ImpactStrip incident={graphIncident} />
            {graphSection("min-h-0 flex-1")}
            <div className="grid h-[285px] shrink-0 grid-cols-[minmax(0,1fr)_320px] gap-3">
              <Timeline incident={graphIncident} />
              <Sparklines events={view.events} incident={graphIncident} />
            </div>
          </div>
          <RootCausePanel incident={graphIncident} />
        </main>
      </div>
      <Tour />
    </EvidenceProvider>
  );
}

export function Dashboard() {
  return <Screen />;
}
