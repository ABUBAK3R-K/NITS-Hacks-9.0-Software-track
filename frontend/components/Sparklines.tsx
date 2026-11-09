"use client";
import { useMemo } from "react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, YAxis } from "recharts";
import type { Event, Incident } from "@/lib/contracts/types";
import { istTime } from "@/lib/format";

interface Series {
  key: string;
  title: string;
  unit: string;
  color: string;
  fmt: (v: number) => string;
  points: { t: number; v: number; ts: string }[];
}

const SPECS = [
  {
    service: "api-gateway",
    name: "latency_p95_ms",
    title: "gateway p95 latency",
    unit: "ms",
    color: "#38bdf8",
    fmt: (v: number) => `${Math.round(v)}`,
  },
  {
    service: "payments-svc",
    name: "error_rate",
    title: "payments error rate",
    unit: "%",
    color: "#ff4d4d",
    fmt: (v: number) => `${(v * 100).toFixed(1)}`,
  },
];

function build(events: Event[], incident: Incident | null): Series[] {
  return SPECS.map((s) => {
    const pick = (list: Event[]) =>
      list
        .filter((e) => e.kind === "metric" && e.service === s.service && e.name === s.name && typeof e.value === "number")
        .map((e) => ({ t: new Date(e.ts).getTime(), v: e.value as number, ts: e.ts }))
        .sort((a, b) => a.t - b.t);
    let points = pick(events).slice(-60);
    // no live samples for this metric: fall back to the metric values in the incident's evidence
    if (points.length < 2 && incident) points = pick(incident.evidence_events);
    return { key: `${s.service}:${s.name}`, title: s.title, unit: s.unit, color: s.color, fmt: s.fmt, points };
  });
}

export function Sparklines({ events, incident }: { events: Event[]; incident: Incident | null }) {
  const series = useMemo(() => build(events, incident), [events, incident]);
  return (
    <section className="panel flex min-h-0 flex-col gap-2 p-3">
      <span className="panel-title px-1">Symptoms</span>
      {series.map((s) => {
        const last = s.points[s.points.length - 1];
        return (
          <div key={s.key} className="flex min-h-0 flex-1 flex-col">
            <div className="flex items-baseline justify-between px-1">
              <span className="text-xs text-slate-400">{s.title}</span>
              <span className="font-mono text-lg font-bold tabular-nums" style={{ color: s.color }}>
                {last ? s.fmt(last.v) : "--"}
                <span className="ml-0.5 text-xs text-slate-500">{s.unit}</span>
              </span>
            </div>
            <div className="min-h-0 flex-1">
              {s.points.length >= 2 ? (
                <ResponsiveContainer width="100%" height="100%" minHeight={40}>
                  <AreaChart data={s.points} margin={{ top: 4, right: 2, bottom: 0, left: 2 }}>
                    <defs>
                      <linearGradient id={`g-${s.key.replace(/[^a-z]/gi, "")}`} x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={s.color} stopOpacity={0.45} />
                        <stop offset="100%" stopColor={s.color} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <YAxis hide domain={[0, "auto"]} />
                    <Tooltip
                      cursor={{ stroke: "#475569" }}
                      contentStyle={{ background: "#0b1018", border: "1px solid #334155", borderRadius: 8, fontSize: 12 }}
                      labelFormatter={(_, p) => (p?.[0]?.payload ? `${istTime(p[0].payload.ts)} IST` : "")}
                      formatter={(v) => [`${s.fmt(Number(v))} ${s.unit}`, s.title]}
                    />
                    <Area
                      type="monotone"
                      dataKey="v"
                      stroke={s.color}
                      strokeWidth={2}
                      fill={`url(#g-${s.key.replace(/[^a-z]/gi, "")})`}
                      isAnimationActive={false}
                      dot={false}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex h-full items-center justify-center text-xs text-slate-600">waiting for samples</div>
              )}
            </div>
          </div>
        );
      })}
    </section>
  );
}
