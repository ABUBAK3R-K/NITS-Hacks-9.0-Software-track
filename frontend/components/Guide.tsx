"use client";
// Self-serve helpers for visitors exploring alone: the "Your verdict" scoreboard, the recorded
// abstain case file, and the "Try it in 60 seconds" checklist. Every tick and tally comes from
// real app state (see lib/progress.ts); every number comes from a reveal, the eval, or a fixture.
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import type { EvalResults, Incident, IncidentSummary, IngestResult } from "@/lib/contracts/types";
import ambiguousFx from "@/lib/contracts/fixtures/incident_ambiguous.json";
import { api } from "@/lib/api";
import { useLiveData } from "@/lib/live";
import { int, istDateTime, pct } from "@/lib/format";
import { loadSampleLog } from "@/lib/sample";
import { markStep, recordVerdict, updateProgress, useProgress, useTourDone, type StepId } from "@/lib/progress";
import { EvidenceProvider } from "./Evidence";
import { AbstainedView } from "./RootCausePanel";

// ---------------------------------------------------------------- state watcher
/** Mounted once on the dashboard: turns real state changes into checklist ticks and verdicts. */
export function ProgressWatcher() {
  const live = useLiveData();
  const sb = live.sandbox;
  const run = sb.run;
  const reveal = sb.reveal;

  useEffect(() => {
    if (run) markStep("run");
  }, [run]);

  const answered = sb.status === "answered" && sb.view.selected !== null;
  useEffect(() => {
    if (answered) markStep("read");
  }, [answered]);

  useEffect(() => {
    if (!reveal) return;
    markStep("reveal");
    // the scoreboard counts hidden (blind) faults only; designed runs show their spec up front
    if (run && run.mode === "blind" && run.run_id === reveal.run_id)
      recordVerdict(reveal.run_id, reveal.abstained ? "abstained" : reveal.matched ? "matched" : "wrong");
  }, [reveal, run]);

  // "Apply fix" worked: the engine itself moved an incident through MITIGATING to RESOLVED
  const fixed = [...Object.values(sb.view.details), ...Object.values(live.details)].some(
    (i) => i.lifecycle === "RESOLVED" && i.lifecycle_history.some((h) => h.state === "MITIGATING"),
  );
  useEffect(() => {
    if (fixed) markStep("fix");
  }, [fixed]);

  return null;
}

// ---------------------------------------------------------------- verdict scoreboard
let evalPromise: Promise<EvalResults | null> | null = null;
function loadEval(): Promise<EvalResults | null> {
  evalPromise ??= api.getEvalResults().catch(() => {
    evalPromise = null;
    return null;
  });
  return evalPromise;
}

function useEvalTop1(): { top1: number; n: number } | null {
  const [ev, setEv] = useState<EvalResults | null>(null);
  useEffect(() => {
    let alive = true;
    void loadEval().then((r) => alive && setEv(r));
    return () => {
      alive = false;
    };
  }, []);
  const m = ev && ev.n > 0 ? ev.methods.find((x) => x.name === "jarvis") : undefined;
  return m ? { top1: m.top1, n: m.n || ev!.n } : null;
}

export function VerdictCard({ className = "" }: { className?: string }) {
  const p = useProgress();
  const ours = useEvalTop1();
  const outcomes = Object.values(p.verdicts);
  if (!outcomes.length) return null;
  const count = (o: string) => outcomes.filter((x) => x === o).length;
  const total = outcomes.length;
  return (
    <div
      className={`fade-up flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5 rounded-lg bg-sheet px-3 py-1 text-xs ring-1 ring-rule-strong ${className}`}
      title="Counted in this browser from your own Reveal results"
    >
      <span className="panel-title text-accent">Your verdict</span>
      <span className="text-ink-2">
        You ran <b className="font-mono text-ink">{total}</b> hidden fault{total === 1 ? "" : "s"}:{" "}
        <b className="font-mono text-safe">{count("matched")}</b> matched, <b className="font-mono text-danger">{count("wrong")}</b> wrong,{" "}
        <b className="font-mono text-warn">{count("abstained")}</b> abstained
      </span>
      {ours && (
        <span className="text-muted">
          <b className="font-mono text-ink">{pct(ours.top1)}</b> top-1 over n={int(ours.n)} in our blind eval, own simulator
        </span>
      )}
      <button
        type="button"
        onClick={() => updateProgress((x) => ({ ...x, verdicts: {} }))}
        className="text-subtle underline-offset-2 hover:text-ink hover:underline"
      >
        reset
      </button>
    </div>
  );
}

// ---------------------------------------------------------------- recorded abstain case
const AMBIGUOUS = ambiguousFx as unknown as Incident;

function AbstainCase({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return createPortal(
    <div
      className="fixed inset-0 z-[110] flex items-start justify-center overflow-y-auto bg-paper/80 p-3 md:p-10"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      {/* evidence drawer sits outside the animated panel so it covers the viewport, not the panel */}
      <EvidenceProvider events={AMBIGUOUS.evidence_events}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Recorded abstain case"
        className="fade-up w-full max-w-[640px] rounded-xl border border-rule-strong bg-paper p-4 shadow-2xl"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <span className="inline-block rounded bg-raised px-2 py-0.5 text-[11px] font-bold tracking-wider text-muted ring-1 ring-rule-strong">
              RECORDED CASE (engine output from our fixtures), not live
            </span>
            <div className="mt-2 text-lg font-bold leading-snug text-ink">What an abstain looks like</div>
            <div className="mt-0.5 font-mono text-xs text-subtle">
              {AMBIGUOUS.id} · opened {istDateTime(AMBIGUOUS.opened_at)}
            </div>
          </div>
          <button type="button" onClick={onClose} aria-label="Close recorded case" className="text-subtle hover:text-ink">
            x
          </button>
        </div>
        <p className="mt-2 text-sm leading-relaxed text-ink-2">
          When the top two causes score too close together, the engine refuses to guess. It shows both candidates and the data
          that would settle it.
        </p>
        <div className="mt-3">
          <AbstainedView inc={AMBIGUOUS} readOnly />
        </div>
      </div>
      </EvidenceProvider>
    </div>,
    document.body,
  );
}

export function AbstainCaseButton({ className = "" }: { className?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title="Open a recorded case where the engine abstained instead of guessing"
        className={`whitespace-nowrap rounded-md bg-paper px-3 py-1.5 text-sm font-semibold text-warn ring-1 ring-warn/40 hover:bg-warn-soft ${className}`}
      >
        Show me an abstain
      </button>
      {open && <AbstainCase onClose={() => setOpen(false)} />}
    </>
  );
}

// ---------------------------------------------------------------- task-first checklist
const STEPS: { id: StepId; label: string; how: string }[] = [
  { id: "run", label: "Inject a hidden fault", how: "Press Judge's Chaos in the bar above." },
  { id: "read", label: "Read the root cause and Why not", how: "Wait for the answer, then read the right panel." },
  { id: "reveal", label: "Reveal", how: "Press REVEAL on the card over the graph." },
  { id: "fix", label: "Apply fix", how: "Under Recommended actions, press Apply fix and watch it resolve." },
  { id: "report", label: "Open the report and Naive AI mode", how: "Press Report on the incident card, then switch on Naive AI mode." },
];

function NextUpCard({
  variant,
  onCollapse,
  onDismiss,
}: {
  variant: "floating" | "inline";
  onCollapse: () => void;
  onDismiss: () => void;
}) {
  const live = useLiveData();
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // After an upload, say what the engine made of it: the incident it opened (and select it),
  // or plainly that nothing in the file looked like an incident. Never just "events accepted".
  const [found, setFound] = useState<IncidentSummary | null>(null);
  const report = async (res: IngestResult, what: string) => {
    const head = `${res.accepted} events accepted from ${what}${res.rejected ? `, ${res.rejected} rejected` : ""}.`;
    if (res.accepted === 0) {
      setErr(`${head} ${res.errors[0] ?? "Nothing in the file could be read as an event."}`);
      return;
    }
    let list: IncidentSummary[] = [];
    for (let i = 0; i < 6; i++) {
      list = await api.listIncidents().catch(() => []);
      if (list.length) break;
      await new Promise((r) => setTimeout(r, 700));
    }
    if (!list.length) {
      setNote(`${head} No incident found: nothing in these events looks like a failure (it needs error logs, alerts or metric spikes). Try the sample log to see the format.`);
      return;
    }
    const top = [...list].sort((x, y) => y.alert_count - x.alert_count || y.services.length - x.services.length)[0];
    live.select(top.id);
    setFound(top);
    setNote(
      `${head} Found ${list.length} incident${list.length === 1 ? "" : "s"}: ` +
        (top.root_service ? `root cause ${top.root_service}` : "no confident root cause, abstained") +
        `. It is open on the dashboard now.`,
    );
  };

  // The engine may regroup an incident (new id) while the rest of an upload is analysed, so the
  // report link resolves the current incident at click time instead of reusing the first id.
  const openReport = async () => {
    const list = await api.listIncidents().catch(() => [] as IncidentSummary[]);
    const pick =
      list.find((i) => i.id === found?.id) ??
      list.find((i) => i.root_service === found?.root_service) ??
      [...list].sort((x, y) => y.alert_count - x.alert_count)[0];
    if (!pick) {
      setFound(null);
      setErr("That incident is no longer in the live store (the demo was reset). Upload again.");
      return;
    }
    router.push(`/incident/${pick.id}`);
  };

  const sample = async () => {
    setBusy("sample");
    setNote(null);
    setErr(null);
    setFound(null);
    try {
      if (live.sandbox.active) live.exitSandbox();
      await live.reset();
      await report(await live.upload(await loadSampleLog()), "the sample log");
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const upload = async (f: File | undefined) => {
    if (!f) return;
    setBusy("upload");
    setNote(null);
    setErr(null);
    setFound(null);
    try {
      if (live.sandbox.active) live.exitSandbox();
      await live.reset(); // only the uploaded events, so the result is clearly from this file
      await report(await live.upload(f), f.name);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const design = () => {
    setErr(null);
    setNote("Design form opened in the Judge bar above.");
    window.dispatchEvent(new Event("jarvis:open-design"));
  };

  const box = variant === "floating" ? "fixed bottom-5 left-5 z-[80] w-[300px] shadow-2xl" : "w-full";
  const action =
    "w-full rounded-md bg-paper px-2.5 py-2 text-left ring-1 ring-rule-strong transition hover:bg-raised disabled:opacity-50";

  return (
    <div className={`fade-up rounded-xl border border-safe/40 bg-sheet p-3 ${box}`}>
      <div className="-m-1 rounded-lg bg-safe-soft p-2 ring-1 ring-safe/40">
      <div className="flex items-center gap-2">
        <span className="text-base font-black text-safe">Congo!</span>
        <button type="button" onClick={onCollapse} className="ml-auto text-xs text-subtle hover:text-ink" aria-label="Collapse next steps">
          hide
        </button>
        <button type="button" onClick={onDismiss} className="text-xs text-subtle hover:text-ink" aria-label="Dismiss next steps">
          x
        </button>
      </div>
      <p className="mt-1.5 text-sm font-semibold leading-snug text-safe">
        You have debugged a prod issue, identified it, flagged it, and fixed it, documented it without disrupting other 5 teams.
      </p>
      </div>
      <div className="panel-title mt-3 text-accent">Next up: explore on your own</div>
      <div className="mt-2 space-y-1.5">
        <button type="button" disabled={busy !== null} onClick={() => void sample()} className={action}>
          <span className="block text-sm font-semibold text-ink">{busy === "sample" ? "Loading sample..." : "Try sample logs"}</span>
          <span className="block text-xs text-muted">One click replay of a recorded payments outage.</span>
        </button>
        <button type="button" disabled={busy !== null} onClick={() => fileRef.current?.click()} className={action}>
          <span className="block text-sm font-semibold text-ink">{busy === "upload" ? "Uploading..." : "Use your own logs"}</span>
          <span className="block text-xs text-muted">Upload .jsonl, .csv or plain log lines. Clears the live demo first.</span>
        </button>
        <button type="button" disabled={busy !== null} onClick={design} className={action}>
          <span className="block text-sm font-semibold text-ink">Design your own outage</span>
          <span className="block text-xs text-muted">Pick a service, a fault and an optional decoy.</span>
        </button>
      </div>
      <input
        ref={fileRef}
        type="file"
        accept=".jsonl,.json,.csv,.log,.txt"
        className="hidden"
        onChange={(e) => void upload(e.target.files?.[0])}
      />
      {note && <div className="mt-2 text-xs font-semibold text-safe">{note}</div>}
      {found && (
        <button
          type="button"
          onClick={() => void openReport()}
          className="mt-2 inline-block rounded-md px-2.5 py-1 text-xs font-bold text-accent ring-1 ring-accent/40 hover:bg-accent-soft"
        >
          Open its report &rarr;
        </button>
      )}
      {err && <div className="mt-2 text-xs text-danger">{err}</div>}
    </div>
  );
}

export function Checklist({ variant }: { variant: "floating" | "inline" }) {
  const tourDone = useTourDone();
  const p = useProgress();
  if (!tourDone || p.checklistDismissed) return null;
  const done = STEPS.filter((s) => p.steps[s.id]).length;
  const next = STEPS.find((s) => !p.steps[s.id]);
  const allDone = !next;
  const collapsed = p.checklistCollapsed;
  const setCollapsed = (v: boolean) => updateProgress((x) => ({ ...x, checklistCollapsed: v }));
  const dismiss = () => updateProgress((x) => ({ ...x, checklistDismissed: true }));

  const box =
    variant === "floating"
      ? "fixed bottom-5 left-5 z-[80] w-[300px] shadow-2xl"
      : "w-full";

  if (collapsed)
    return (
      <div className={variant === "floating" ? "fixed bottom-5 left-5 z-[80]" : ""}>
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          className="fade-up rounded-full bg-sheet px-3 py-1.5 text-xs font-semibold text-ink ring-1 ring-accent/50 hover:bg-raised"
        >
          {allDone ? "Next up" : "Try it in 60 seconds"} <span className="font-mono text-accent">{done}/{STEPS.length}</span>
        </button>
      </div>
    );

  if (allDone) return <NextUpCard variant={variant} onCollapse={() => setCollapsed(true)} onDismiss={dismiss} />;

  return (
    <div className={`fade-up rounded-xl border border-accent/40 bg-sheet p-3 ${box}`}>
      <div className="flex items-center gap-2">
        <span className="panel-title text-accent">Try it in 60 seconds</span>
        <span className="font-mono text-xs text-subtle">
          {done}/{STEPS.length}
        </span>
        <button type="button" onClick={() => setCollapsed(true)} className="ml-auto text-xs text-subtle hover:text-ink" aria-label="Collapse checklist">
          hide
        </button>
        <button type="button" onClick={dismiss} className="text-xs text-subtle hover:text-ink" aria-label="Dismiss checklist">
          x
        </button>
      </div>
      <ol className="mt-2 space-y-1">
        {STEPS.map((s, i) => {
          const ok = Boolean(p.steps[s.id]);
          const isNext = next?.id === s.id;
          return (
            <li key={s.id} className="flex gap-2 text-sm leading-snug">
              <span
                className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ${
                  ok ? "bg-safe text-paper" : isNext ? "ring-1 ring-accent text-accent" : "ring-1 ring-rule-strong text-subtle"
                }`}
                aria-hidden="true"
              >
                {ok ? "✓" : i + 1}
              </span>
              <span className="min-w-0">
                <span className={ok ? "text-muted line-through decoration-subtle" : isNext ? "font-semibold text-ink" : "text-ink-2"}>
                  {s.label}
                </span>
                {isNext && <span className="block text-xs text-muted">{s.how}</span>}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
