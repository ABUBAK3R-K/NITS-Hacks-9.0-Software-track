"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useLiveData } from "@/lib/live";
import { EvidenceProvider } from "./Evidence";
import { ImpactStrip } from "./ImpactStrip";
import { IncidentList } from "./IncidentList";
import { RootCausePanel } from "./RootCausePanel";
import { ServiceGraph } from "./ServiceGraph";
import { Sparklines } from "./Sparklines";
import { Timeline } from "./Timeline";
import { TopBar } from "./TopBar";

// The dashboard is laid out on a 1920 px wide canvas and zoomed to the window width, so a
// 1536 px laptop (1920 at 125% scaling) records the same picture as a 1080p monitor.
const DESIGN_W = 1920;
function useDesignCanvas() {
  const [fit, setFit] = useState<{ scale: number; h: number } | null>(null);
  useEffect(() => {
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
  }, []);
  return fit;
}

function Screen() {
  const live = useLiveData();
  const { selected, storm } = live;

  const evidence = useMemo(() => [...live.events, ...(selected?.evidence_events ?? [])], [live.events, selected]);

  // per-service alert badges on the graph while the storm is on screen
  const alertCounts = useMemo(() => {
    const m: Record<string, number> = {};
    if (storm.phase === "flood") for (const a of storm.alerts) m[a.service] = (m[a.service] ?? 0) + 1;
    return m;
  }, [storm]);

  const possibleCauses = useCallback(
    (id: string) => {
      const d = live.details[id];
      if (!d) return 2;
      return Math.max(2, d.candidates.filter((c) => d.candidates[0].score - c.score < 0.15).length);
    },
    [live.details],
  );

  const graphIncident = storm.phase === "flood" ? null : selected;
  const fit = useDesignCanvas();

  return (
    <EvidenceProvider events={evidence}>
      <div
        className="flex h-screen min-h-0 flex-col overflow-hidden"
        style={fit ? { width: DESIGN_W, height: fit.h, zoom: fit.scale } : undefined}
      >
        <TopBar />
        <main className="grid min-h-0 flex-1 grid-cols-[330px_minmax(0,1fr)_560px] gap-3 p-3">
          <IncidentList
            incidents={live.incidents}
            storm={storm}
            selectedId={live.selectedId}
            onSelect={live.select}
            possibleCauses={possibleCauses}
          />
          <div className="flex min-h-0 flex-col gap-3">
            <ImpactStrip incident={graphIncident} />
            <section className={`panel relative min-h-0 flex-1 overflow-hidden ${storm.phase === "flood" ? "storm-flash border-2" : ""}`}>
              <div className="absolute left-4 top-3 z-10 flex items-center gap-3">
                <span className="panel-title">Service graph</span>
                {graphIncident && (
                  <span className="text-sm text-slate-300">
                    {graphIncident.status === "ABSTAINED" ? (
                      <span className="text-amber-200">two possible causes highlighted</span>
                    ) : (
                      <>
                        failure path from <span className="font-bold text-red-300">{graphIncident.root_cause?.service}</span>
                      </>
                    )}
                  </span>
                )}
              </div>
              {storm.phase === "flood" && (
                <div className="absolute right-4 top-3 z-10 rounded-lg bg-red-600 px-3 py-1 text-sm font-black tracking-widest text-white">
                  {storm.alerts.length} ALERTS FIRING
                </div>
              )}
              <div className="absolute bottom-3 left-4 z-10 flex gap-3 text-xs text-slate-400">
                <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-[#22c55e]" />healthy</span>
                <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-[#f59e0b]" />degraded</span>
                <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full bg-[#ff3b3b]" />critical</span>
                <span className="text-slate-500">arrows point from caller to dependency</span>
              </div>
              <ServiceGraph
                key={`${live.topology ? "ready" : "empty"}-${fit?.scale ?? 1}-${fit?.h ?? 0}`}
                topology={live.topology}
                topo={live.topo}
                incident={graphIncident}
                alertCounts={alertCounts}
              />
            </section>
            <div className="grid h-[285px] shrink-0 grid-cols-[minmax(0,1fr)_320px] gap-3">
              <Timeline incident={graphIncident} />
              <Sparklines events={live.events} incident={graphIncident} />
            </div>
          </div>
          <RootCausePanel incident={graphIncident} />
        </main>
      </div>
    </EvidenceProvider>
  );
}

export function Dashboard() {
  return <Screen />;
}
