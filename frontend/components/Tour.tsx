"use client";
// First-visit guided tour plus an always-available help button that explains the current screen.
// Both drive the same step list, so the tour and the help panel never disagree about a section.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLiveData } from "@/lib/live";
import { signatureLabel } from "@/lib/format";

const SEEN_KEY = "jarvis_tour_seen_v1";
const CARD_W = 380;
const GAP = 16;
const MARGIN = 16;

interface Step {
  target: string;
  title: string;
  body: string;
}

const STEPS: Step[] = [
  {
    target: "topbar",
    title: "Live status and your own data",
    body:
      "The dot tells you whether the stream is live, paused or reconnecting. Upload logs feeds your own .jsonl, .csv or plain log lines into the same engine, and the case study link returns to the landing page.",
  },
  {
    target: "chaos",
    title: "Chaos Lab",
    body:
      "Start the simulated payments platform, then inject a fault such as DB pool exhaustion. Every fault produces the same stream of metrics, logs and alerts a real system would.",
  },
  {
    target: "judge",
    title: "Judge's Chaos",
    body:
      "The engine answers a hidden fault blind. You reveal whether its verdict matched the injected cause. The AI never decides here, it only explains what the evidence proves.",
  },
  {
    target: "incidents",
    title: "Incidents",
    body:
      "Alerts that are connected in the dependency graph become one incident card. Click a card to inspect it. When two causes are too close to call, the card says ABSTAINED instead of guessing.",
  },
  {
    target: "impact",
    title: "Impact at a glance",
    body:
      "Services touched, users affected, failed payments and INR at risk. Every number is counted from the failed requests in the incident's evidence.",
  },
  {
    target: "graph",
    title: "Service graph",
    body:
      "Red is the root cause, amber is degraded and green is healthy. Arrows point from caller to dependency, and the failure path lights up when an incident is selected.",
  },
  {
    target: "timeline",
    title: "Timeline of onsets",
    body:
      "Entries are ordered by the first anomaly, not the first page. Each entry names the earliest cause it can see, so you can read how the failure spread.",
  },
  {
    target: "sparklines",
    title: "Key signals over time",
    body:
      "Gateway p95 latency and payments error rate. Spikes line up with the incident window, so you can sanity check the engine's timing against the raw signals.",
  },
  {
    target: "rootcause",
    title: "Root cause analysis",
    body:
      "The verdict, every score component and the exact event ids behind each claim. Recommendations carry evidence too, so nothing is taken on trust. Tip: the ? button explains whatever is on screen at any time.",
  },
];

interface Rect {
  top: number;
  left: number;
  width: number;
  height: number;
}

type Side = "top" | "bottom" | "left" | "right";

function measure(target: string): Rect | null {
  const el = document.querySelector(`[data-tour="${target}"]`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  if (r.width === 0 && r.height === 0) return null;
  const pad = 8;
  return { top: r.top - pad, left: r.left - pad, width: r.width + pad * 2, height: r.height + pad * 2 };
}

function place(rect: Rect, cardH: number): { top: number; left: number } {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const cardW = Math.min(CARD_W, vw - MARGIN * 2);
  const narrow = rect.width < vw * 0.5;
  const space = {
    right: vw - (rect.left + rect.width),
    left: rect.left,
    bottom: vh - (rect.top + rect.height),
    top: rect.top,
  };
  const needX = cardW + GAP + MARGIN;
  const needY = cardH + GAP + MARGIN;
  let side: Side;
  if (narrow) {
    if (space.right >= needX) side = "right";
    else if (space.left >= needX) side = "left";
    else if (space.bottom >= needY) side = "bottom";
    else side = "top";
  } else {
    if (space.bottom >= needY) side = "bottom";
    else if (space.top >= needY) side = "top";
    else if (space.right >= needX) side = "right";
    else side = "left";
  }

  let top = 0;
  let left = 0;
  if (side === "right") {
    left = rect.left + rect.width + GAP;
    top = rect.top;
  } else if (side === "left") {
    left = rect.left - GAP - cardW;
    top = rect.top;
  } else if (side === "bottom") {
    top = rect.top + rect.height + GAP;
    left = rect.left + rect.width / 2 - cardW / 2;
  } else {
    top = rect.top - GAP - cardH;
    left = rect.left + rect.width / 2 - cardW / 2;
  }

  top = Math.max(MARGIN, Math.min(top, vh - cardH - MARGIN));
  left = Math.max(MARGIN, Math.min(left, vw - cardW - MARGIN));
  return { top, left };
}

function useWhatAmISeeing() {
  const live = useLiveData();
  const view = live.sandbox.active ? live.sandbox.view : live;
  const inc = view.selected;
  const storm = view.storm;
  const alerts = storm.alerts.length;
  const services = new Set(storm.alerts.map((a) => a.service)).size;

  const lines: string[] = [];
  if (live.sandbox.active) lines.push("You are viewing an isolated sandbox run. The live Chaos Lab is untouched.");
  else if (live.connection === "open") lines.push("You are connected to the live stream.");
  else if (live.connection === "connecting") lines.push("The dashboard is connecting to the backend stream.");
  else lines.push("The stream is reconnecting. Panels show the last known state.");

  if (storm.phase === "flood")
    lines.push(
      `${alerts} alert${alerts === 1 ? "" : "s"} are firing across ${services} service${services === 1 ? "" : "s"}. The engine is grouping them into one incident.`,
    );

  if (inc) {
    if (inc.status === "ABSTAINED") {
      lines.push(
        `Selected incident ${inc.id} is ABSTAINED: ${inc.abstain_reason ?? "the top two causes are too close to call"}. Both candidates are shown with the data that would disambiguate.`,
      );
    } else if (inc.root_cause) {
      lines.push(
        `Selected incident ${inc.id}: root cause is ${inc.root_cause.service} (${signatureLabel(inc.root_cause.signature)}) at ${Math.round(inc.root_cause.confidence * 100)}% confidence, backed by ${inc.root_cause.evidence.length} evidence events.`,
      );
    } else {
      lines.push(`Selected incident ${inc.id} is ${inc.status}. The ranked candidates are on the right.`);
    }
  } else {
    lines.push(
      `There ${view.incidents.length === 1 ? "is" : "are"} ${view.incidents.length} incident${view.incidents.length === 1 ? "" : "s"} on screen. Click a card on the left, or inject a fault from the Chaos Lab.`,
    );
  }

  const reveal = live.sandbox.reveal;
  if (live.sandbox.active && reveal) {
    if (reveal.abstained) lines.push(`The reveal says ABSTAINED. The injected fault was on ${reveal.expected_root}.`);
    else if (reveal.matched) lines.push(`The reveal says MATCH. The engine and the injected fault agree on ${reveal.expected_root}.`);
    else lines.push(`The reveal says NO MATCH. The engine said ${reveal.our_top1 ?? "nothing"}, the injected fault was on ${reveal.expected_root}.`);
  }

  return lines;
}

function HelpPanel({ onStart, onClose }: { onStart: (at: number) => void; onClose: () => void }) {
  const lines = useWhatAmISeeing();
  return (
    <div
      className="fade-up thin-scroll max-h-[70vh] overflow-y-auto rounded-xl border border-rule-strong bg-sheet p-4 shadow-2xl"
      style={{ width: "min(380px, calc(100vw - 32px))" }}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="panel-title text-accent">Help</div>
          <div className="mt-1 text-lg font-bold leading-snug text-ink">What am I seeing right now?</div>
        </div>
        <button type="button" onClick={onClose} aria-label="Close help" className="text-subtle hover:text-ink">
          x
        </button>
      </div>

      <ul className="mt-3 space-y-2">
        {lines.map((line) => (
          <li key={line} className="flex gap-2 text-sm leading-relaxed text-ink-2">
            <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
            <span>{line}</span>
          </li>
        ))}
      </ul>

      <div className="mt-4 border-t border-rule pt-3">
        <div className="panel-title mb-2">Sections on this screen</div>
        <div className="grid gap-1">
          {STEPS.map((s, i) => (
            <button
              key={s.target}
              type="button"
              onClick={() => onStart(i)}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-ink-2 transition hover:bg-raised hover:text-ink"
            >
              <span className="w-5 shrink-0 font-mono text-xs text-subtle">{i + 1}</span>
              <span className="truncate">{s.title}</span>
            </button>
          ))}
        </div>
      </div>

      <button
        type="button"
        onClick={() => onStart(0)}
        className="mt-3 w-full rounded-md bg-ink px-3 py-2 text-sm font-bold text-paper hover:bg-ink-2"
      >
        Take the guided tour
      </button>
    </div>
  );
}

export function Tour() {
  const [active, setActive] = useState(false);
  const [index, setIndex] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const [cardH, setCardH] = useState(220);
  const cardRef = useRef<HTMLDivElement>(null);

  const step = STEPS[index];

  const start = useCallback((at = 0) => {
    setIndex(at);
    setRect(null);
    setActive(true);
    setHelpOpen(false);
  }, []);

  const finish = useCallback(() => {
    setActive(false);
    setRect(null);
    try {
      localStorage.setItem(SEEN_KEY, "1");
    } catch {
      /* storage unavailable, the tour simply shows again next visit */
    }
  }, []);

  // first visit: open the tour once the dashboard has painted
  useEffect(() => {
    let seen = false;
    try {
      seen = localStorage.getItem(SEEN_KEY) === "1";
    } catch {
      /* ignore */
    }
    if (seen) return;
    const t = setTimeout(() => start(0), 1100);
    return () => clearTimeout(t);
  }, [start]);

  // follow the highlighted section while the tour is open (layout, data and zoom can shift)
  useEffect(() => {
    if (!active) return;
    const el = document.querySelector(`[data-tour="${step.target}"]`);
    const update = () =>
      setRect((prev) => {
        const next = measure(step.target);
        if (
          prev &&
          next &&
          prev.top === next.top &&
          prev.left === next.left &&
          prev.width === next.width &&
          prev.height === next.height
        )
          return prev;
        return next;
      });
    update();
    const raf = requestAnimationFrame(update);
    const iv = setInterval(update, 400);
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(update) : null;
    if (ro && el) ro.observe(el);
    return () => {
      cancelAnimationFrame(raf);
      clearInterval(iv);
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
      ro?.disconnect();
    };
  }, [active, index, step.target]);

  // measure the card so the placement math knows how much room it needs
  useEffect(() => {
    if (active && cardRef.current) setCardH(cardRef.current.offsetHeight);
  }, [active, index, rect]);

  const pos = useMemo(() => (rect ? place(rect, cardH) : null), [rect, cardH]);

  return (
    <>
      {active && rect && (
        <>
          <div className="pointer-events-none fixed inset-0 z-[100]" aria-hidden="true">
            <div
              className="absolute rounded-xl border-2 border-accent transition-[top,left,width,height] duration-500 ease-out"
              style={{
                top: rect.top,
                left: rect.left,
                width: rect.width,
                height: rect.height,
                background: "rgba(82, 168, 255, 0.07)",
                boxShadow: "0 0 0 9999px rgba(4, 4, 4, 0.72)",
              }}
            />
          </div>
          <div
            ref={cardRef}
            role="dialog"
            aria-label="Dashboard tour"
            className="fixed z-[101] rounded-xl border border-accent/50 bg-sheet p-4 shadow-2xl transition-[top,left] duration-500 ease-out"
            style={{ width: "min(380px, calc(100vw - 32px))", ...(pos ?? { top: MARGIN, left: MARGIN }) }}
          >
            <div key={index} className="fade-up">
              <div className="flex items-center justify-between">
                <span className="panel-title text-accent">Dashboard guide</span>
                <span className="font-mono text-xs text-subtle">
                  {index + 1} / {STEPS.length}
                </span>
              </div>
              <div className="mt-2 text-lg font-bold leading-snug text-ink">{step.title}</div>
              <p className="mt-1 text-sm leading-relaxed text-ink-2">{step.body}</p>

              <div className="mt-3 h-1 overflow-hidden rounded-full bg-raised">
                <div
                  className="h-full rounded-full bg-accent transition-[width] duration-500 ease-out"
                  style={{ width: `${((index + 1) / STEPS.length) * 100}%` }}
                />
              </div>

              <div className="mt-3 flex items-center gap-2">
                <button type="button" onClick={finish} className="text-xs font-semibold text-subtle hover:text-ink">
                  Skip tour
                </button>
                <div className="ml-auto flex items-center gap-2">
                  <button
                    type="button"
                    disabled={index === 0}
                    onClick={() => setIndex((i) => Math.max(0, i - 1))}
                    className="rounded-md bg-paper px-3 py-1.5 text-sm font-semibold text-ink ring-1 ring-rule-strong transition hover:bg-raised disabled:opacity-40"
                  >
                    Back
                  </button>
                  <button
                    type="button"
                    onClick={() => (index === STEPS.length - 1 ? finish() : setIndex((i) => i + 1))}
                    className="rounded-md bg-ink px-3 py-1.5 text-sm font-bold text-paper transition hover:bg-ink-2"
                  >
                    {index === STEPS.length - 1 ? "Finish" : "Next"}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </>
      )}

      {!active && (
        <div className="fixed bottom-5 right-5 z-[90] flex flex-col items-end gap-3">
          {helpOpen && <HelpPanel onStart={start} onClose={() => setHelpOpen(false)} />}
          <button
            type="button"
            onClick={() => setHelpOpen((o) => !o)}
            aria-expanded={helpOpen}
            aria-label="Help: what am I seeing right now?"
            title="What am I seeing right now?"
            className="flex h-12 w-12 items-center justify-center rounded-full bg-accent text-xl font-black text-paper shadow-lg ring-2 ring-paper/40 transition hover:scale-105 hover:bg-accent/90"
          >
            {helpOpen ? "x" : "?"}
          </button>
        </div>
      )}
    </>
  );
}
