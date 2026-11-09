"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { FAULT_TYPES, type FaultType, type IngestResult } from "@/lib/contracts/types";
import { useLiveData } from "@/lib/live";
import { FAULT_LABELS, istTime } from "@/lib/format";

const FAULT_HINT: Record<FaultType, string> = {
  db_pool_exhaustion: "postgres runs out of connections",
  bad_deploy: "payments-svc v2.3.1 ships a 5xx bug",
  redis_down: "session cache goes away",
  red_herring: "harmless ledger deploy, then a DB problem",
  psp_slow: "the bank's PSP slows down",
};

export function TopBar() {
  const live = useLiveData();
  const [now, setNow] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [upload, setUpload] = useState<{ name: string; res?: IngestResult; err?: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const tick = () => setNow(Date.now());
    tick();
    const iv = setInterval(tick, 1000);
    return () => clearInterval(iv);
  }, []);

  const streaming = now > 0 && now - live.lastEventAt < 5000;
  let indicator: { label: string; cls: string; dot: string };
  if (live.connection === "down") indicator = { label: "RECONNECTING", cls: "text-danger", dot: "bg-danger" };
  else if (live.connection === "connecting") indicator = { label: "CONNECTING", cls: "text-warn", dot: "bg-warn" };
  else if (streaming) indicator = { label: "LIVE", cls: "text-safe", dot: "bg-safe live-dot" };
  else indicator = { label: "PAUSED", cls: "text-ink-2", dot: "bg-subtle" };

  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    try {
      await fn();
    } finally {
      setBusy(null);
    }
  };

  const onFile = async (f: File | undefined) => {
    if (!f) return;
    setUpload({ name: f.name });
    try {
      const res = await live.upload(f);
      setUpload({ name: f.name, res });
    } catch (e) {
      setUpload({ name: f.name, err: (e as Error).message });
    }
    if (fileRef.current) fileRef.current.value = "";
  };

  const active = new Set(live.sim?.active_faults ?? []);

  return (
    <header data-tour="topbar" className="relative flex h-[68px] shrink-0 items-center gap-4 border-b border-rule bg-paper px-5">
      <div className="flex items-baseline gap-3 pr-2">
        <Link
          href="/"
          title="Back to the case study landing page"
          className="text-2xl font-black tracking-[0.18em] text-ink transition hover:text-accent"
        >
          J.A.R.V.I.S.
        </Link>
        <span className="whitespace-nowrap text-sm font-semibold text-ink-2">Many alerts. One cause. Proof attached.</span>
      </div>

      <div data-tour="chaos" className="flex items-center gap-2 rounded-lg border border-rule bg-sheet px-3 py-1.5">
        <span className="panel-title mr-1 text-accent">Chaos Lab</span>
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => run("start", live.start)}
          className="whitespace-nowrap rounded-md bg-paper px-3 py-1.5 text-sm font-semibold text-ink ring-1 ring-rule-strong hover:bg-sheet disabled:opacity-50"
        >
          Start
        </button>
        {FAULT_TYPES.map((t) => (
          <button
            key={t}
            type="button"
            title={FAULT_HINT[t]}
            disabled={busy !== null}
            onClick={() => run(t, () => live.fault(t))}
            className={`whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-semibold ring-1 transition disabled:opacity-50 ${
              active.has(t)
                ? "bg-danger text-paper ring-danger"
                : "bg-paper text-ink ring-rule-strong hover:bg-sheet"
            }`}
          >
            {FAULT_LABELS[t]}
          </button>
        ))}
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => run("reset", live.reset)}
          className="whitespace-nowrap rounded-md bg-paper px-3 py-1.5 text-sm font-semibold text-ink ring-1 ring-rule-strong hover:bg-sheet"
        >
          Reset
        </button>
      </div>

      <div className="relative">
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          className="whitespace-nowrap rounded-md bg-paper px-3 py-1.5 text-sm font-semibold text-ink ring-1 ring-rule-strong hover:bg-sheet"
        >
          Upload logs
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".jsonl,.json,.csv,.log,.txt"
          className="hidden"
          onChange={(e) => onFile(e.target.files?.[0])}
        />
        {upload && (
          <div className="fade-up absolute left-0 top-11 z-40 w-80 rounded-lg border border-rule-strong bg-sheet p-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="truncate font-mono text-ink-2">{upload.name}</span>
              <button type="button" onClick={() => setUpload(null)} className="text-subtle hover:text-ink">
                x
              </button>
            </div>
            {!upload.res && !upload.err && <div className="mt-1 text-muted">Uploading...</div>}
            {upload.err && <div className="mt-1 text-danger">{upload.err}</div>}
            {upload.res && (
              <div className="mt-1">
                <span className="font-semibold text-safe">{upload.res.accepted} accepted</span>
                <span className="mx-2 text-subtle">|</span>
                <span className={upload.res.rejected ? "font-semibold text-warn" : "text-muted"}>
                  {upload.res.rejected} rejected
                </span>
                {upload.res.errors.length > 0 && (
                  <ul className="mt-1 list-disc pl-4 text-xs text-muted">
                    {upload.res.errors.slice(0, 4).map((e) => (
                      <li key={e}>{e}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="ml-auto flex items-center gap-4">
        <Link
          href="/"
          className="whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-semibold text-ink-2 ring-1 ring-rule-strong transition hover:bg-sheet hover:text-ink"
        >
          &larr; Case study
        </Link>
        {live.fixtureMode && (
          <span className="rounded bg-raised px-2 py-0.5 text-xs font-semibold tracking-wider text-muted">
            FIXTURE MODE
          </span>
        )}
        <span className="font-mono text-lg text-ink-2 tabular-nums">
          {now ? istTime(new Date(now).toISOString()) : "--:--:--"} <span className="text-sm text-subtle">IST</span>
        </span>
        <span className={`flex items-center gap-2 text-sm font-bold tracking-widest ${indicator.cls}`}>
          <span className={`h-3 w-3 rounded-full ${indicator.dot}`} />
          {indicator.label}
        </span>
      </div>

      {live.notice && (
        <div className="fade-up absolute right-5 top-[72px] z-40 flex max-w-xl items-start gap-3 rounded-lg border border-warn/40 bg-warn-soft px-4 py-2 text-sm text-warn">
          <span>{live.notice}</span>
          <button type="button" onClick={live.dismissNotice} className="text-warn hover:text-ink">
            x
          </button>
        </div>
      )}
    </header>
  );
}
