"use client";
// U1 judge controls: "Judge's Chaos" (blind run, spec hidden), "Design your own outage"
// (designed run), and the Reveal card with the backend's match verdict. Runs execute in an
// isolated backend sandbox; the dashboard panels switch to the run's own state meanwhile.
import { useState } from "react";
import { SERVICE_IDS, type FaultKind, type FaultSpec, type RunReveal } from "@/lib/contracts/types";
import { useLiveData } from "@/lib/live";

export const KIND_LABEL: Record<FaultKind, string> = {
  pool_exhaustion: "connection pool exhaustion",
  latency: "latency",
  error_spike: "error spike",
  down: "down",
  bad_deploy: "bad deploy",
};

const APP_SERVICES = new Set(["auth-svc", "payments-svc", "ledger-svc"]);

/** Same rule as contracts/schemas.py FaultSpec: pool_exhaustion only on postgres, bad_deploy only on app services. */
export function kindsFor(service: string): FaultKind[] {
  const k: FaultKind[] = [];
  if (service === "postgres") k.push("pool_exhaustion");
  if (APP_SERVICES.has(service)) k.push("bad_deploy");
  k.push("latency", "error_spike", "down");
  return k;
}

export function ShieldIcon({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} className={className} aria-hidden="true">
      <path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6l7-3z" strokeLinejoin="round" />
      <path d="M9 12l2 2 4-4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function signed(n: number): string {
  return `${n > 0 ? "+" : ""}${n} s`;
}

function DesignForm({ onClose }: { onClose: () => void }) {
  const live = useLiveData();
  const [target, setTarget] = useState<string>("postgres");
  const [kind, setKind] = useState<FaultKind>("pool_exhaustion");
  const [decoyOn, setDecoyOn] = useState(false);
  const [decoy, setDecoy] = useState<string>("ledger-svc");
  const [offset, setOffset] = useState(-30);
  const [seed, setSeed] = useState(1);

  const kinds = kindsFor(target);
  const decoyOptions = SERVICE_IDS.filter((s) => s !== target);
  const decoySvc = decoyOptions.includes(decoy as (typeof SERVICE_IDS)[number]) ? decoy : decoyOptions[0];

  const pickTarget = (t: string) => {
    setTarget(t);
    const k = kindsFor(t);
    if (!k.includes(kind)) setKind(k[0]);
  };

  const submit = () => {
    const spec: FaultSpec = {
      target,
      kind,
      onset_jitter_s: 0,
      decoy: decoyOn ? { service: decoySvc, offset_s: offset } : null,
      seed,
    };
    onClose();
    void live.runDesigned(spec);
  };

  const field = "w-full rounded-md bg-sheet px-2 py-1.5 text-sm text-ink ring-1 ring-rule-strong focus:outline-none focus:ring-accent";
  return (
    <div className="fade-up absolute right-0 top-11 z-40 w-[400px] rounded-lg border border-rule-strong bg-sheet p-4 text-sm">
      <div className="flex items-center justify-between">
        <span className="panel-title text-accent">Design your own outage</span>
        <button type="button" onClick={onClose} className="text-subtle hover:text-ink">
          x
        </button>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3">
        <label className="block">
          <span className="text-xs text-muted">Service</span>
          <select value={target} onChange={(e) => pickTarget(e.target.value)} className={field}>
            {SERVICE_IDS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-muted">Fault</span>
          <select value={kind} onChange={(e) => setKind(e.target.value as FaultKind)} className={field}>
            {kinds.map((k) => (
              <option key={k} value={k}>
                {KIND_LABEL[k]}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="mt-3 flex items-center gap-2 text-ink">
        <input type="checkbox" checked={decoyOn} onChange={(e) => setDecoyOn(e.target.checked)} className="accent-accent" />
        Add a decoy deploy (a harmless change on another service)
      </label>
      {decoyOn && (
        <div className="mt-2 grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-xs text-muted">Decoy service</span>
            <select value={decoySvc} onChange={(e) => setDecoy(e.target.value)} className={field}>
              {decoyOptions.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-xs text-muted">Offset vs fault onset: {signed(offset)}</span>
            <input
              type="range"
              min={-60}
              max={30}
              step={5}
              value={offset}
              onChange={(e) => setOffset(Number(e.target.value))}
              className="mt-2 w-full accent-accent"
            />
          </label>
        </div>
      )}
      <label className="mt-3 flex items-center gap-2">
        <span className="text-xs text-muted">Seed</span>
        <input
          type="number"
          min={0}
          value={seed}
          onChange={(e) => setSeed(Math.max(0, Math.floor(Number(e.target.value) || 0)))}
          className={`${field} w-24`}
        />
      </label>
      <button
        type="button"
        onClick={submit}
        className="mt-4 w-full rounded-md bg-ink px-3 py-2 font-bold text-paper hover:bg-ink-2"
      >
        Run it in a sandbox
      </button>
      <div className="mt-2 text-xs text-subtle">
        Runs in an isolated, fast-forwarded sandbox. The live Chaos Lab is not touched.
      </div>
    </div>
  );
}

/** Row under the top bar: the one-liner, and the judge controls. */
export function JudgeBar() {
  const live = useLiveData();
  const sb = live.sandbox;
  const [designOpen, setDesignOpen] = useState(false);
  const busy = sb.status === "running" || sb.status === "playing";
  return (
    <div data-tour="judge" className="relative flex h-[50px] shrink-0 items-center gap-4 border-b border-rule bg-paper px-5">
      <div className="flex items-center gap-2 text-[15px] text-ink-2">
        <ShieldIcon className="h-5 w-5 text-safe" />
        Our AI is not allowed to decide anything. It can only explain what the math proved.
      </div>
      <div className="ml-auto flex items-center gap-2">
        {sb.active && (
          <span className="flex items-center gap-2 rounded-lg bg-accent-soft px-3 py-1.5 text-sm text-accent ring-1 ring-accent/50">
            <span className="font-black tracking-widest text-accent">SANDBOX RUN</span>
            {sb.run && <span className="font-mono text-xs text-accent">{sb.run.run_id}</span>}
            <span className="text-accent">isolated, not the live Chaos Lab</span>
          </span>
        )}
        <button
          type="button"
          disabled={busy}
          onClick={() => void live.runBlind()}
          className="whitespace-nowrap rounded-md bg-ink px-4 py-1.5 text-sm font-bold text-paper ring-1 ring-ink hover:bg-ink-2 disabled:opacity-50"
        >
          Judge&apos;s Chaos: inject a hidden fault
        </button>
        <div className="relative">
          <button
            type="button"
            disabled={busy}
            onClick={() => setDesignOpen((o) => !o)}
            className="whitespace-nowrap rounded-md bg-paper px-3 py-1.5 text-sm font-semibold text-ink ring-1 ring-rule-strong hover:bg-sheet disabled:opacity-50"
          >
            Design your own outage
          </button>
          {designOpen && <DesignForm onClose={() => setDesignOpen(false)} />}
        </div>
        {sb.active && (
          <button
            type="button"
            onClick={live.exitSandbox}
            className="whitespace-nowrap rounded-md bg-paper px-3 py-1.5 text-sm font-semibold text-ink ring-1 ring-rule-strong hover:bg-sheet"
          >
            Back to live
          </button>
        )}
      </div>
    </div>
  );
}

function SpecRows({ spec }: { spec: FaultSpec }) {
  const rows: [string, string][] = [
    ["target", spec.target],
    ["fault", KIND_LABEL[spec.kind] ?? spec.kind],
    ["onset jitter", `${spec.onset_jitter_s.toFixed(1)} s`],
    ["decoy", spec.decoy ? `${spec.decoy.service} deploy at ${signed(spec.decoy.offset_s)}` : "none"],
    ["seed", String(spec.seed)],
  ];
  return (
    <dl className="grid grid-cols-[110px_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className={`font-mono ${k === "target" ? "font-bold text-ink" : "text-ink"}`}>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function Verdict({ r }: { r: RunReveal }) {
  if (r.abstained)
    return (
      <div className="rounded-lg bg-warn-soft px-4 py-3 text-center ring-2 ring-warn">
        <div className="text-4xl font-black tracking-widest text-warn">ABSTAINED</div>
        <div className="mt-1 text-sm text-warn">
          The engine declined to name one cause{r.our_top1 ? ` (top candidate ${r.our_top1})` : ""}. Injected: {r.expected_root}.
        </div>
      </div>
    );
  if (r.matched)
    return (
      <div className="rounded-lg bg-safe-soft px-4 py-3 text-center ring-2 ring-safe">
        <div className="text-5xl font-black tracking-widest text-safe">MATCH</div>
        <div className="mt-1 text-sm text-safe">
          Engine said <b>{r.our_top1}</b>, injected fault was on <b>{r.expected_root}</b>.
        </div>
      </div>
    );
  return (
    <div className="rounded-lg bg-danger-soft px-4 py-3 text-center ring-2 ring-danger">
      <div className="text-5xl font-black tracking-widest text-danger">NO MATCH</div>
      <div className="mt-1 text-sm text-danger">
        Engine said <b>{r.our_top1 ?? "nothing"}</b>, injected fault was on <b>{r.expected_root}</b>.
      </div>
    </div>
  );
}

/** Overlay on the service graph while a sandbox run is on screen. */
export function RevealCard() {
  const live = useLiveData();
  const sb = live.sandbox;
  const [hidden, setHidden] = useState<string | null>(null);
  if (!sb.active) return null;
  const run = sb.run;
  // the card can be tucked away so it does not cover graph nodes; a new run shows it again
  if (run && hidden === run.run_id + run.created_at)
    return (
      <button
        type="button"
        onClick={() => setHidden(null)}
        className="absolute right-4 top-14 z-20 rounded-md bg-ink px-3 py-1.5 text-sm font-bold text-paper hover:bg-ink-2"
      >
        {sb.reveal ? (sb.reveal.abstained ? "ABSTAINED" : sb.reveal.matched ? "MATCH" : "NO MATCH") : "Show reveal"}
      </button>
    );
  const spec = sb.reveal?.spec ?? run?.spec ?? null;
  return (
    <div className="fade-up absolute right-4 top-14 z-20 w-[380px] rounded-lg border border-accent/50 bg-sheet p-4">
      <div className="flex items-center justify-between">
        <span className="panel-title text-accent">{run?.mode === "designed" ? "Designed outage" : "Judge's Chaos"}</span>
        {run ? (
          <button type="button" onClick={() => setHidden(run.run_id + run.created_at)} className="text-xs text-subtle hover:text-ink">
            hide
          </button>
        ) : (
          <span className="text-xs text-subtle">sandbox</span>
        )}
      </div>
      {sb.status === "running" && (
        <div className="mt-2 flex items-center gap-2 text-ink-2">
          <span className="live-dot h-2.5 w-2.5 rounded-full bg-accent" />
          Injecting a fault into an isolated sandbox and running the engine...
        </div>
      )}
      {sb.status === "error" && <div className="mt-2 text-sm text-danger">Run failed: {sb.error}</div>}
      {sb.status === "playing" && (
        <div className="mt-2 text-sm text-ink-2">
          {run?.spec_hidden ? "Fault spec is sealed. " : ""}Replaying what the engine saw...
        </div>
      )}
      {sb.status === "answered" && run && (
        <div className="mt-2 space-y-3">
          {run.incidents.length === 0 && <div className="text-sm text-warn">The engine opened no incident for this run.</div>}
          {sb.signalStorm && run.incidents.length > 0 && (
            <div className="text-xs text-subtle">Storm shows the incident&apos;s evidence signals; runs return no raw alert stream.</div>
          )}
          {spec ? (
            <SpecRows spec={spec} />
          ) : (
            <div className="rounded-lg bg-sheet p-3 text-sm text-ink-2 ring-1 ring-rule-strong">
              The engine has answered. The injected fault is still sealed.
            </div>
          )}
          {sb.reveal ? (
            <Verdict r={sb.reveal} />
          ) : (
            <button
              type="button"
              disabled={sb.revealing}
              onClick={() => void live.revealRun()}
              className="w-full rounded-md bg-ink px-4 py-2.5 text-lg font-black tracking-widest text-paper hover:bg-ink-2 disabled:opacity-60"
            >
              {sb.revealing ? "REVEALING..." : run.mode === "designed" ? "CHECK MATCH" : "REVEAL"}
            </button>
          )}
          {sb.error && <div className="text-sm text-danger">{sb.error}</div>}
        </div>
      )}
    </div>
  );
}
