"use client";
import type { Health, IncidentStatus, Provider, Severity } from "@/lib/contracts/types";
import type { ReactNode } from "react";

export const HEALTH_COLOR: Record<Health, string> = {
  healthy: "#22c55e",
  degraded: "#f59e0b",
  critical: "#ff3b3b",
};

export const COMPONENT_COLORS = {
  upstream: "#38bdf8",
  precedence: "#a78bfa",
  change: "#f472b6",
  severity: "#fb923c",
} as const;

export const COMPONENT_LABELS = {
  upstream: "upstream",
  precedence: "precedence",
  change: "change",
  severity: "severity",
} as const;

const STATUS_STYLE: Record<IncidentStatus, string> = {
  OPEN: "bg-red-500/15 text-red-300 ring-red-500/50",
  ABSTAINED: "bg-amber-400/15 text-amber-200 ring-amber-400/50",
  RESOLVED: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/50",
};

export function StatusBadge({ status, big = false }: { status: IncidentStatus; big?: boolean }) {
  return (
    <span
      className={`inline-flex items-center rounded-md font-bold tracking-wider ring-1 ${STATUS_STYLE[status]} ${
        big ? "px-2.5 py-1 text-sm" : "px-2 py-0.5 text-[11px]"
      }`}
    >
      {status}
    </span>
  );
}

const SEV_STYLE: Record<Severity, string> = {
  critical: "bg-red-600 text-white",
  error: "bg-orange-500 text-black",
  warn: "bg-amber-300 text-black",
  info: "bg-slate-600 text-white",
};

export function SeverityPill({ severity }: { severity: Severity }) {
  return (
    <span className={`rounded px-1.5 py-0.5 text-[11px] font-bold uppercase tracking-wide ${SEV_STYLE[severity]}`}>
      {severity}
    </span>
  );
}

const PROVIDER_STYLE: Record<Provider, string> = {
  groq: "bg-orange-500/15 text-orange-300 ring-orange-400/50",
  gemini: "bg-sky-500/15 text-sky-300 ring-sky-400/50",
  template: "bg-slate-500/20 text-slate-200 ring-slate-400/50",
};

export function ProviderBadge({ provider }: { provider: Provider }) {
  const label = provider === "template" ? "deterministic template" : `${provider} LLM`;
  return (
    <span className={`rounded-md px-2.5 py-1 text-sm font-semibold ring-1 ${PROVIDER_STYLE[provider]}`}>
      narrated by {label}
    </span>
  );
}

export function ConfidenceBar({ value, tone = "red" }: { value: number; tone?: "red" | "amber" }) {
  const color = tone === "amber" ? "#fbbf24" : value >= 0.6 ? "#ff4d4d" : "#fbbf24";
  return (
    <div className="relative h-3 w-full overflow-hidden rounded-full bg-slate-800">
      <div className="bar-fill h-full rounded-full" style={{ width: `${Math.min(1, value) * 100}%`, background: color }} />
      {/* abstain threshold marker */}
      <div className="absolute top-0 h-full w-0.5 bg-white/70" style={{ left: "45%" }} title="abstain below 0.45" />
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="flex h-full items-center justify-center p-6 text-center text-slate-500">{children}</div>;
}
