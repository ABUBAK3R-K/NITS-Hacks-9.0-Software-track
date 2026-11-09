"use client";
// Evidence chips and the raw-event drawer. Any chip anywhere opens the drawer with the
// exact Event JSON from incident.evidence_events (or the live event buffer).
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Event } from "@/lib/contracts/types";
import { istDateTime, istTimeMs, shortId } from "@/lib/format";

interface EvidenceApi {
  lookup: (id: string) => Event | undefined;
  open: (id: string) => void;
}

const Ctx = createContext<EvidenceApi>({ lookup: () => undefined, open: () => {} });

export function EvidenceProvider({ events, children }: { events: Event[]; children: ReactNode }) {
  const [openId, setOpenId] = useState<string | null>(null);
  const map = useMemo(() => {
    const m = new Map<string, Event>();
    for (const e of events) m.set(e.id, e);
    return m;
  }, [events]);
  const lookup = useCallback((id: string) => map.get(id), [map]);
  const open = useCallback((id: string) => setOpenId(id), []);
  const api = useMemo(() => ({ lookup, open }), [lookup, open]);

  useEffect(() => {
    if (!openId) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpenId(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openId]);

  return (
    <Ctx.Provider value={api}>
      {children}
      {openId && <Drawer id={openId} event={map.get(openId)} onClose={() => setOpenId(null)} />}
    </Ctx.Provider>
  );
}

const KIND_STYLE: Record<string, string> = {
  metric: "text-sky-300 ring-sky-500/40 bg-sky-500/10 hover:bg-sky-500/25",
  log: "text-violet-200 ring-violet-500/40 bg-violet-500/10 hover:bg-violet-500/25",
  alert: "text-red-200 ring-red-500/50 bg-red-500/10 hover:bg-red-500/25",
  change: "text-pink-200 ring-pink-500/50 bg-pink-500/10 hover:bg-pink-500/25",
  request: "text-slate-200 ring-slate-500/40 bg-slate-500/10 hover:bg-slate-500/25",
};

export function EvidenceChip({ id }: { id: string }) {
  const { lookup, open } = useContext(Ctx);
  const ev = lookup(id);
  const style = ev ? KIND_STYLE[ev.kind] : "text-slate-400 ring-slate-600 bg-slate-800/50 hover:bg-slate-700/50";
  return (
    <button
      type="button"
      onClick={() => open(id)}
      title={ev ? `${ev.service} ${ev.kind} ${ev.name}` : id}
      className={`inline-flex cursor-pointer items-center gap-1 rounded-md px-1.5 py-0.5 font-mono text-xs ring-1 transition ${style}`}
    >
      <span className="opacity-60">{ev?.kind ?? "evt"}</span>
      {shortId(id)}
    </button>
  );
}

export function EvidenceChips({ ids, max = 8 }: { ids: string[]; max?: number }) {
  const uniq = Array.from(new Set(ids));
  const shown = uniq.slice(0, max);
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {shown.map((id) => (
        <EvidenceChip key={id} id={id} />
      ))}
      {uniq.length > max && <span className="text-xs text-slate-500">+{uniq.length - max}</span>}
    </span>
  );
}

function Drawer({ id, event, onClose }: { id: string; event?: Event; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true">
      <button type="button" aria-label="Close" className="absolute inset-0 bg-black/60" onClick={onClose} />
      <aside className="drawer-in relative flex h-full w-[560px] flex-col border-l border-slate-700 bg-[#0b1018] shadow-2xl">
        <header className="flex items-start justify-between border-b border-slate-800 p-5">
          <div>
            <div className="panel-title">Raw evidence event</div>
            <div className="mt-1 font-mono text-xl font-semibold text-white">{id}</div>
            {event && (
              <div className="mt-1 text-sm text-slate-400">
                {event.service} · {event.kind} · {event.name} · {istDateTime(event.ts)}
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md px-3 py-1 text-slate-300 ring-1 ring-slate-700 hover:bg-slate-800"
          >
            Esc
          </button>
        </header>
        <div className="thin-scroll flex-1 overflow-auto p-5">
          {event ? (
            <>
              {event.message && (
                <p className="mb-4 rounded-lg bg-slate-900 p-3 font-mono text-sm text-amber-100 ring-1 ring-slate-800">
                  {event.message}
                </p>
              )}
              {event.value !== null && event.value !== undefined && (
                <p className="mb-4 text-lg">
                  <span className="text-slate-400">value </span>
                  <span className="font-mono font-semibold text-white">{event.value}</span>
                  <span className="ml-3 text-sm text-slate-500">at {istTimeMs(event.ts)} IST</span>
                </p>
              )}
              <pre className="whitespace-pre-wrap break-all rounded-lg bg-black/60 p-4 font-mono text-[13px] leading-relaxed text-emerald-200 ring-1 ring-slate-800">
                {JSON.stringify(event, null, 2)}
              </pre>
            </>
          ) : (
            <p className="text-slate-400">This id is not in the incident evidence set.</p>
          )}
        </div>
      </aside>
    </div>
  );
}
