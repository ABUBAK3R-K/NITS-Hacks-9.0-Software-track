"use client";
import type { Incident } from "@/lib/contracts/types";
import { inr, int } from "@/lib/format";

function Tile({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent: string }) {
  return (
    <div className="flex min-w-0 flex-1 flex-col justify-center rounded-xl border border-slate-800 bg-slate-900/50 px-4 py-2">
      <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">{label}</span>
      <span className="truncate text-[28px] font-black leading-tight tabular-nums" style={{ color: accent }}>
        {value}
      </span>
      {sub && <span className="truncate text-xs text-slate-500">{sub}</span>}
    </div>
  );
}

export function ImpactStrip({ incident }: { incident: Incident | null }) {
  const im = incident?.impact;
  return (
    <div className="flex h-[92px] shrink-0 gap-2">
      <Tile
        label="Services impacted"
        value={im ? int(im.services.length) : "0"}
        sub={im ? im.services.join(", ") : "all healthy"}
        accent={im ? "#fbbf24" : "#64748b"}
      />
      <Tile label="Users affected" value={im ? int(im.users_affected) : "0"} sub="failed login or payment" accent={im ? "#f8fafc" : "#64748b"} />
      <Tile label="Failed payments" value={im ? int(im.failed_payments) : "0"} sub="on payments-svc" accent={im ? "#ff6b6b" : "#64748b"} />
      <Tile label="INR at risk" value={im ? inr(im.failed_value_inr) : inr(0)} sub="value of failed payments" accent={im ? "#ff4d4d" : "#64748b"} />
    </div>
  );
}
