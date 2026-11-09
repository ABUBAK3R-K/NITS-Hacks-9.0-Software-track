// J.A.R.V.I.S. data contracts, TypeScript mirror of contracts/schemas.py.
// Field for field identical. Change both files (plus fixtures) in the same commit.
// The frontend gets a synced copy at frontend/lib/contracts/types.ts (npm run sync-contracts).

export type Source = "sim" | "api" | "upload";
export type EventKind = "metric" | "log" | "alert" | "change" | "request";
export type Severity = "info" | "warn" | "error" | "critical";
export type Tier = "edge" | "app" | "data" | "external";
export type IncidentStatus = "OPEN" | "ABSTAINED" | "RESOLVED";
export type Health = "healthy" | "degraded" | "critical";
export type Provider = "groq" | "gemini" | "template";
export type FaultType =
  | "db_pool_exhaustion"
  | "bad_deploy"
  | "redis_down"
  | "red_herring"
  | "psp_slow";

export const SERVICE_IDS = [
  "api-gateway",
  "auth-svc",
  "redis-session",
  "postgres",
  "payments-svc",
  "ledger-svc",
  "psp-adapter",
] as const;

export const FAULT_TYPES: FaultType[] = [
  "db_pool_exhaustion",
  "bad_deploy",
  "redis_down",
  "red_herring",
  "psp_slow",
];

// ---------------------------------------------------------------- events
export interface Event {
  id: string; // "evt_..."
  ts: string; // ISO 8601 UTC, ms precision, trailing Z
  source: Source;
  service: string;
  kind: EventKind;
  severity: Severity;
  name: string; // latency_p95_ms | error_rate | db_pool_in_use_pct | deploy | log | <AlertName> | http_request ...
  value?: number | null;
  message?: string | null;
  attrs: Record<string, string | number | boolean | null>;
}

// ---------------------------------------------------------------- topology
export interface Service {
  id: string;
  label: string;
  tier: Tier;
}

/** `from` calls `to` (caller -> callee). */
export interface Edge {
  from: string;
  to: string;
}

export interface Topology {
  services: Service[];
  edges: Edge[];
}

export interface ServiceHealth {
  service: string;
  health: Health;
  is_root_cause: boolean;
}

export interface TopologyState {
  ts: string;
  services: ServiceHealth[];
  /** [caller, callee] pairs on the propagation path of the top OPEN incident */
  hot_edges: [string, string][];
}

// ---------------------------------------------------------------- incidents
/** Weighted contributions: upstream <= 0.40, precedence <= 0.30, change <= 0.20, severity <= 0.10.
 *  score = (upstream + precedence + change + severity) * penalty */
export interface ScoreComponents {
  upstream: number;
  precedence: number;
  change: number;
  severity: number;
}

export interface ComponentEvidence {
  upstream: string[];
  precedence: string[];
  change: string[];
  severity: string[];
}

export interface Candidate {
  service: string;
  score: number;
  components: ScoreComponents;
  penalty: number; // 1.0 or 0.5
  victim_of: string | null;
  onset: string | null;
  signature: string | null;
  evidence: string[];
  component_evidence: ComponentEvidence;
}

export interface RootCause {
  service: string;
  signature: string;
  confidence: number;
  evidence: string[];
}

export interface Impact {
  services: string[];
  users_affected: number;
  failed_payments: number;
  failed_value_inr: number;
}

export interface TimelineEntry {
  ts: string;
  service: string;
  what: string;
  event_ids: string[];
  caused_by: string | null;
}

export interface Recommendation {
  action: string;
  why: string;
  evidence: string[];
  runbook: string;
}

/** Item of GET /api/incidents */
export interface IncidentSummary {
  id: string;
  title: string;
  status: IncidentStatus;
  opened_at: string;
  updated_at: string;
  services: string[];
  alert_count: number;
  severity: Severity;
  root_service: string | null;
  confidence: number | null;
}

/** GET /api/incidents/{id} and SSE `incident_update` */
export interface Incident {
  id: string;
  title: string;
  status: IncidentStatus;
  opened_at: string;
  updated_at: string;
  services: string[];
  alert_count: number;
  severity: Severity;
  root_cause: RootCause | null;
  candidates: Candidate[];
  abstain_reason: string | null;
  disambiguation: string[];
  impact: Impact;
  timeline: TimelineEntry[];
  recommendations: Recommendation[];
  /** every event id referenced above, resolved */
  evidence_events: Event[];
}

// ---------------------------------------------------------------- report
export interface ReportSentence {
  text: string;
  cites: string[];
}

/** POST /api/incidents/{id}/report */
export interface Report {
  incident_id: string;
  summary: string;
  sentences: ReportSentence[];
  dropped: number;
  provider: Provider;
  generated_at: string;
}

// ---------------------------------------------------------------- api io
export interface FaultRequest {
  type: FaultType;
}

export interface SimStatus {
  running: boolean;
  active_faults: FaultType[];
  events_in_store: number;
}

export interface IngestResult {
  accepted: number;
  rejected: number;
  errors: string[];
}

export interface HealthResponse {
  status: "ok";
  version: string;
}

/** SSE event names on GET /api/stream */
export type SseEventName = "event" | "incident_update" | "topology_state";
export interface SsePayloads {
  event: Event;
  incident_update: Incident;
  topology_state: TopologyState;
}

export const ENGINE_WEIGHTS = { upstream: 0.4, precedence: 0.3, change: 0.2, severity: 0.1 } as const;
export const VICTIM_PENALTY = 0.5;
export const ABSTAIN_MIN_MARGIN = 0.15;
export const ABSTAIN_MIN_SCORE = 0.45;
