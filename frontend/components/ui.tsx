"use client";
import type { Health, IncidentStatus, Provider, Severity } from "@/lib/contracts/types";
import type { ReactNode } from "react";

export const HEALTH_COLOR: Record<Health, string> = {
  healthy: "#62c073",
  degraded: "#f5a623",
  critical: "#ff6166",
};

export const COMPONENT_COLORS = {
  upstream: "#52a8ff",
  precedence: "#2f6fbf",
  change: "#c7c7c7",
  severity: "#707070",
} as const;

export const COMPONENT_LABELS = {
  upstream: "upstream",
  precedence: "precedence",
  change: "change",
  severity: "severity",
} as const;

const STATUS_STYLE: Record<IncidentStatus, string> = {
  OPEN: "bg-danger-soft text-danger ring-danger/25",
  ABSTAINED: "bg-warn-soft text-warn ring-warn/25",
  RESOLVED: "bg-safe-soft text-safe ring-safe/25",
};

export function StatusBadge({ status, big = false }: { status: IncidentStatus; big?: boolean }) {
  return (
    <span
      className={`inline-flex items-center rounded-full font-bold tracking-wider ring-1 ${STATUS_STYLE[status]} ${
        big ? "px-2.5 py-1 text-sm" : "px-2 py-0.5 text-[11px]"
      }`}
    >
      {status}
    </span>
  );
}

const SEV_STYLE: Record<Severity, string> = {
  critical: "bg-danger-soft text-danger ring-1 ring-danger/25",
  error: "bg-human-soft text-human ring-1 ring-human/25",
  warn: "bg-warn-soft text-warn ring-1 ring-warn/25",
  info: "bg-sheet text-ink-2 ring-1 ring-rule-strong",
};

export function SeverityPill({ severity }: { severity: Severity }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-bold uppercase tracking-wide ${SEV_STYLE[severity]}`}>
      {severity}
    </span>
  );
}

const PROVIDER_STYLE: Record<Provider, string> = {
  groq: "bg-human-soft text-human ring-human/25",
  gemini: "bg-accent-soft text-accent ring-accent/25",
  template: "bg-sheet text-ink-2 ring-rule-strong",
};

export function ProviderBadge({ provider }: { provider: Provider }) {
  const label = provider === "template" ? "deterministic template" : `${provider} LLM`;
  return (
    <span className={`rounded-full px-2.5 py-1 text-sm font-semibold ring-1 ${PROVIDER_STYLE[provider]}`}>
      narrated by {label}
    </span>
  );
}

export function ConfidenceBar({ value, tone = "red" }: { value: number; tone?: "red" | "amber" }) {
  const color = tone === "amber" ? "#f5a623" : value >= 0.6 ? "#ff6166" : "#f5a623";
  return (
    <div className="relative h-3 w-full overflow-hidden rounded-full bg-raised">
      <div className="bar-fill h-full rounded-full" style={{ width: `${Math.min(1, value) * 100}%`, background: color }} />
      {/* abstain threshold marker */}
      <div className="absolute top-0 h-full w-0.5 bg-ink/70" style={{ left: "45%" }} title="abstain below 0.45" />
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="flex h-full items-center justify-center p-6 text-center text-subtle">{children}</div>;
}

/** Neutral note when the narration did not come from the primary LLM. A fallback is not an error. */
export function FallbackNote({ provider, className = "" }: { provider: Provider; className?: string }) {
  if (provider === "groq") return null;
  const text = provider === "gemini" ? "Groq was busy, answered by Gemini fallback" : "Narrated by the deterministic template";
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-md bg-raised px-2 py-0.5 text-xs text-muted ring-1 ring-rule-strong ${className}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-subtle" aria-hidden="true" />
      {text}
    </span>
  );
}
