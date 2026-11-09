"use client";
// Loading state for the slowest wait a judge can hit: the free-tier backend waking from sleep
// (about 30 to 50 s). Polls /health; appears only if the first answer takes longer than 1.5 s,
// and disappears the moment the engine responds.
import { useEffect, useState } from "react";
import { API_URL, USE_FIXTURES } from "@/lib/api";

export function WakeBanner() {
  const [state, setState] = useState<"checking" | "waking" | "up">("checking");
  const [secs, setSecs] = useState(0);

  useEffect(() => {
    if (USE_FIXTURES) return;
    let alive = true;
    const started = Date.now();
    const slow = setTimeout(() => alive && setState((s) => (s === "up" ? s : "waking")), 1500);
    const tick = setInterval(() => alive && setSecs(Math.round((Date.now() - started) / 1000)), 1000);
    const poll = async () => {
      while (alive) {
        try {
          const r = await fetch(`${API_URL}/health`, { cache: "no-store" });
          if (r.ok) {
            if (alive) setState("up");
            return;
          }
        } catch {
          /* still asleep */
        }
        await new Promise((res) => setTimeout(res, 2500));
      }
    };
    void poll();
    return () => {
      alive = false;
      clearTimeout(slow);
      clearInterval(tick);
    };
  }, []);

  if (state !== "waking") return null;
  return (
    <div role="status" className="fixed inset-x-0 top-0 z-[120] flex justify-center px-4 pt-3">
      <div className="flex items-center gap-3 rounded-full border border-accent/50 bg-sheet px-5 py-2.5 text-sm text-ink shadow-2xl">
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent" aria-hidden="true" />
        <span>
          <b>Waking up the engine</b> (free hosting sleeps when idle, usually 30 to 50 s)...
        </span>
        <span className="font-mono text-xs text-muted">{secs}s</span>
      </div>
    </div>
  );
}
