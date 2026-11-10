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
  metric: "text-accent ring-accent/40 bg-accent-soft hover:bg-accent-soft",
  log: "text-accent ring-accent/40 bg-accent-soft hover:bg-accent-soft",
  alert: "text-danger ring-danger/50 bg-danger-soft hover:bg-danger-soft",
  change: "text-accent ring-accent/50 bg-accent-soft hover:bg-accent-soft",
  request: "text-ink ring-rule-strong/40 bg-raised hover:bg-raised",
};

export function EvidenceChip({ id }: { id: string }) {
  const { lookup, open } = useContext(Ctx);
  const ev = lookup(id);
  const style = ev ? KIND_STYLE[ev.kind] : "text-muted ring-rule-strong bg-raised/50 hover:bg-rule-strong/50";
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
      {uniq.length > max && <span className="text-xs text-subtle">+{uniq.length - max}</span>}
    </span>
  );
}

function Drawer({ id, event, onClose }: { id: string; event?: Event; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true">
      <button type="button" aria-label="Close" className="absolute inset-0 bg-paper/60 backdrop-blur-[1px]" onClick={onClose} />
      <aside className="drawer-in relative flex h-full w-[560px] flex-col border-l border-rule-strong bg-sheet">
        <header className="flex items-start justify-between border-b border-rule p-5">
          <div>
            <div className="panel-title">Raw evidence event</div>
            <div className="mt-1 font-mono text-xl font-semibold text-ink">{id}</div>
            {event && (
              <div className="mt-1 text-sm text-muted">
                {event.service} · {event.kind} · {event.name} · {istDateTime(event.ts)}
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md px-3 py-1 text-ink-2 ring-1 ring-rule-strong hover:bg-raised"
          >
            Esc
          </button>
        </header>
        <div className="thin-scroll flex-1 overflow-auto p-5">
          {event ? (
            <>
              {event.message && (
                <p className="mb-4 rounded-lg bg-sheet p-3 font-mono text-sm text-warn ring-1 ring-rule">
                  {event.message}
                </p>
              )}
              {event.value !== null && event.value !== undefined && (
                <p className="mb-4 text-lg">
                  <span className="text-muted">value </span>
                  <span className="font-mono font-semibold text-ink">{event.value}</span>
                  <span className="ml-3 text-sm text-subtle">at {istTimeMs(event.ts)} IST</span>
                </p>
              )}
              <pre className="whitespace-pre-wrap break-all rounded-lg bg-paper p-4 font-mono text-[13px] leading-relaxed text-safe ring-1 ring-rule">
                {JSON.stringify(event, null, 2)}
              </pre>
            </>
          ) : (
            <p className="text-muted">This id is not in the incident evidence set.</p>
          )}
        </div>
      </aside>
    </div>
  );
}
