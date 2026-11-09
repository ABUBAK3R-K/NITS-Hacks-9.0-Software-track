"use client";
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
  if (live.connection === "down") indicator = { label: "RECONNECTING", cls: "text-red-300", dot: "bg-red-500" };
  else if (live.connection === "connecting") indicator = { label: "CONNECTING", cls: "text-amber-200", dot: "bg-amber-400" };
  else if (streaming) indicator = { label: "LIVE", cls: "text-emerald-300", dot: "bg-emerald-400 live-dot" };
  else indicator = { label: "PAUSED", cls: "text-slate-300", dot: "bg-slate-500" };

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
    <header className="relative flex h-[68px] shrink-0 items-center gap-4 border-b border-slate-800 bg-[#070a10] px-5">
      <div className="flex items-baseline gap-3 pr-2">
        <span className="text-2xl font-black tracking-[0.18em] text-cyan-300">J.A.R.V.I.S.</span>
        <span className="hidden text-sm text-slate-400 2xl:inline">many alerts, one cause, every claim with evidence</span>
      </div>

      <div className="flex items-center gap-2 rounded-xl border border-slate-800 bg-slate-900/60 px-3 py-1.5">
        <span className="panel-title mr-1 text-fuchsia-300">Chaos Lab</span>
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => run("start", live.start)}
          className="whitespace-nowrap rounded-lg bg-emerald-500/15 px-3 py-1.5 text-sm font-semibold text-emerald-200 ring-1 ring-emerald-500/50 hover:bg-emerald-500/30 disabled:opacity-50"
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
                ? "bg-red-600 text-white ring-red-400"
                : "bg-red-500/10 text-red-200 ring-red-500/40 hover:bg-red-500/25"
            }`}
          >
            {FAULT_LABELS[t]}
          </button>
        ))}
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => run("reset", live.reset)}
          className="whitespace-nowrap rounded-lg bg-slate-700/40 px-3 py-1.5 text-sm font-semibold text-slate-200 ring-1 ring-slate-600 hover:bg-slate-700"
        >
          Reset
        </button>
      </div>

      <div className="relative">
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          className="whitespace-nowrap rounded-lg bg-cyan-500/10 px-3 py-1.5 text-sm font-semibold text-cyan-200 ring-1 ring-cyan-500/40 hover:bg-cyan-500/25"
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
          <div className="fade-up absolute left-0 top-11 z-40 w-80 rounded-lg border border-slate-700 bg-[#0d131d] p-3 text-sm shadow-xl">
            <div className="flex items-center justify-between">
              <span className="truncate font-mono text-slate-300">{upload.name}</span>
              <button type="button" onClick={() => setUpload(null)} className="text-slate-500 hover:text-white">
                x
              </button>
            </div>
            {!upload.res && !upload.err && <div className="mt-1 text-slate-400">Uploading...</div>}
            {upload.err && <div className="mt-1 text-red-300">{upload.err}</div>}
            {upload.res && (
              <div className="mt-1">
                <span className="font-semibold text-emerald-300">{upload.res.accepted} accepted</span>
                <span className="mx-2 text-slate-600">|</span>
                <span className={upload.res.rejected ? "font-semibold text-amber-300" : "text-slate-400"}>
                  {upload.res.rejected} rejected
                </span>
                {upload.res.errors.length > 0 && (
                  <ul className="mt-1 list-disc pl-4 text-xs text-slate-400">
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
        {live.fixtureMode && (
          <span className="rounded bg-slate-800 px-2 py-0.5 text-xs font-semibold tracking-wider text-slate-400">
            FIXTURE MODE
          </span>
        )}
        <span className="font-mono text-lg text-slate-300 tabular-nums">
          {now ? istTime(new Date(now).toISOString()) : "--:--:--"} <span className="text-sm text-slate-500">IST</span>
        </span>
        <span className={`flex items-center gap-2 text-sm font-bold tracking-widest ${indicator.cls}`}>
          <span className={`h-3 w-3 rounded-full ${indicator.dot}`} />
          {indicator.label}
        </span>
      </div>

      {live.notice && (
        <div className="fade-up absolute right-5 top-[72px] z-40 flex max-w-xl items-start gap-3 rounded-lg border border-amber-500/40 bg-[#1a1408] px-4 py-2 text-sm text-amber-100 shadow-xl">
          <span>{live.notice}</span>
          <button type="button" onClick={live.dismissNotice} className="text-amber-300 hover:text-white">
            x
          </button>
        </div>
      )}
    </header>
  );
}
