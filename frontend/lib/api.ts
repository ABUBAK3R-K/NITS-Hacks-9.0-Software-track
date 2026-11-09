// Data access. Fixture mode when NEXT_PUBLIC_API_URL is unset or NEXT_PUBLIC_USE_FIXTURES=1,
// otherwise the real FastAPI backend. Components never call fetch directly.
import type {
  ApplyFixResult,
  EvalResults,
  FaultSpec,
  FaultType,
  Incident,
  IncidentSummary,
  IngestResult,
  NaiveAnswer,
  Report,
  Run,
  RunReveal,
  SimStatus,
  Topology,
} from "@/lib/contracts/types";
import topologyFx from "@/lib/contracts/fixtures/topology.json";
import incidentDbPoolFx from "@/lib/contracts/fixtures/incident_db_pool.json";
import incidentAmbiguousFx from "@/lib/contracts/fixtures/incident_ambiguous.json";
import reportDbPoolFx from "@/lib/contracts/fixtures/report_db_pool.json";
import runBlindFx from "@/lib/contracts/fixtures/run_blind.json";
import runRevealFx from "@/lib/contracts/fixtures/run_reveal.json";
import naiveDbPoolFx from "@/lib/contracts/fixtures/naive_answer_db_pool.json";
import evalResultsFx from "@/lib/contracts/fixtures/eval_results.json";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/+$/, "");
export const USE_FIXTURES = !API_URL || process.env.NEXT_PUBLIC_USE_FIXTURES === "1";

// Fixture incidents are produced by the real engine (contracts/gen_fixtures.py), so their
// ids are engine hashes. Key them by their own id; use FX.hero / FX.ambiguous by role.
const heroFx = incidentDbPoolFx as unknown as Incident;
const ambiguousFx = incidentAmbiguousFx as unknown as Incident;
const runBlind = runBlindFx as unknown as Run;
const runIncidents = Object.fromEntries(runBlind.incidents.map((i) => [i.id, i]));

export const FX = {
  topology: topologyFx as Topology,
  hero: heroFx,
  ambiguous: ambiguousFx,
  incidents: { ...runIncidents, [heroFx.id]: heroFx, [ambiguousFx.id]: ambiguousFx } as Record<string, Incident>,
  report: reportDbPoolFx as Report,
  runBlind,
  runReveal: runRevealFx as RunReveal,
  // the one recorded naive LLM answer, keyed by the incident it was asked about
  naive: { [heroFx.id]: naiveDbPoolFx as NaiveAnswer } as Record<string, NaiveAnswer>,
  evalResults: evalResultsFx as EvalResults,
};

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function toSummary(i: Incident): IncidentSummary {
  return {
    id: i.id,
    title: i.title,
    status: i.status,
    opened_at: i.opened_at,
    updated_at: i.updated_at,
    services: i.services,
    alert_count: i.alert_count,
    severity: i.severity,
    root_service: i.root_cause?.service ?? null,
    confidence: i.root_cause?.confidence ?? i.candidates[0]?.score ?? null,
  };
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${init?.method ?? "GET"} ${path} -> ${res.status} ${body.slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

// Fixture report for incidents without a narrated fixture: the same deterministic
// template idea the backend falls back to, built from the incident's own timeline.
function templateReport(i: Incident): Report {
  const sentences = i.timeline.map((t) => ({
    text: `${t.service}: ${t.what}${t.caused_by ? `, caused by ${t.caused_by}` : ""}.`,
    cites: t.event_ids,
  }));
  const summary =
    i.status === "ABSTAINED"
      ? `J.A.R.V.I.S. did not name a root cause: ${i.abstain_reason ?? "the evidence is split"}. Candidates are ${i.candidates
          .slice(0, 2)
          .map((c) => c.service)
          .join(" and ")}.`
      : `${i.root_cause?.service ?? "unknown"} is the root cause (${i.root_cause?.signature ?? ""}).`;
  return {
    incident_id: i.id,
    summary,
    sentences,
    dropped: 0,
    provider: "template",
    generated_at: i.updated_at,
  };
}

export const api = {
  getTopology(): Promise<Topology> {
    if (USE_FIXTURES) return Promise.resolve(FX.topology);
    return http<Topology>("/api/topology");
  },
  listIncidents(): Promise<IncidentSummary[]> {
    // In fixture mode the list is owned by the fake stream (useLiveData).
    if (USE_FIXTURES) return Promise.resolve([]);
    return http<IncidentSummary[]>("/api/incidents");
  },
  getIncident(id: string): Promise<Incident> {
    if (USE_FIXTURES) {
      const i = FX.incidents[id];
      return i ? Promise.resolve(i) : Promise.reject(new Error(`no fixture for ${id}`));
    }
    return http<Incident>(`/api/incidents/${encodeURIComponent(id)}`);
  },
  async getReport(id: string): Promise<Report> {
    if (USE_FIXTURES) {
      await new Promise((r) => setTimeout(r, 700));
      if (id === FX.report.incident_id) return FX.report;
      const i = FX.incidents[id];
      if (!i) throw new Error(`no fixture for ${id}`);
      return templateReport(i);
    }
    return http<Report>(`/api/incidents/${encodeURIComponent(id)}/report`, { method: "POST" });
  },
  // ---- sandbox runs (U1). Fixture mode can only replay the one recorded run, with its
  // recorded spec; it never pretends to have run a different spec.
  async runBlind(): Promise<Run> {
    if (USE_FIXTURES) {
      await wait(1200);
      return { ...FX.runBlind, mode: "blind", spec_hidden: true, spec: null, created_at: new Date().toISOString() };
    }
    return http<Run>("/api/runs/blind", { method: "POST" });
  },
  async runDesigned(spec: FaultSpec): Promise<Run> {
    if (USE_FIXTURES) {
      await wait(1200);
      return { ...FX.runBlind, mode: "designed", spec_hidden: false, spec: FX.runReveal.spec, created_at: new Date().toISOString() };
    }
    return http<Run>("/api/runs/designed", json(spec));
  },
  getRun(runId: string): Promise<Run> {
    if (USE_FIXTURES) return Promise.reject(new Error("fixture mode has no live run to refetch"));
    return http<Run>(`/api/runs/${encodeURIComponent(runId)}`);
  },
  async revealRun(runId: string): Promise<RunReveal> {
    if (USE_FIXTURES) {
      await wait(400);
      if (runId === FX.runReveal.run_id) return FX.runReveal;
      throw new Error(`no reveal fixture for ${runId}`);
    }
    return http<RunReveal>(`/api/runs/${encodeURIComponent(runId)}/reveal`);
  },
  // ---- naive LLM baseline (U4) and eval (U3)
  async getNaive(id: string): Promise<NaiveAnswer> {
    if (USE_FIXTURES) {
      await wait(600);
      const n = FX.naive[id];
      if (n) return n;
      throw new Error(`fixture mode has a recorded naive answer only for ${FX.hero.id}`);
    }
    return http<NaiveAnswer>(`/api/incidents/${encodeURIComponent(id)}/naive`);
  },
  getEvalResults(): Promise<EvalResults> {
    if (USE_FIXTURES) return Promise.resolve(FX.evalResults);
    return http<EvalResults>("/api/eval/results");
  },
  async applyFix(id: string, fixId: string): Promise<ApplyFixResult> {
    if (USE_FIXTURES) {
      await wait(500);
      return { incident_id: id, fix_id: fixId, applied: false, message: "Fixture mode: no simulator to apply the fix to. Connect the backend." };
    }
    return http<ApplyFixResult>(`/api/incidents/${encodeURIComponent(id)}/apply_fix`, json({ fix_id: fixId }));
  },
  simStart(): Promise<SimStatus> {
    return http<SimStatus>("/api/sim/start", { method: "POST" });
  },
  simFault(type: FaultType): Promise<SimStatus> {
    return http<SimStatus>("/api/sim/fault", json({ type }));
  },
  simReset(): Promise<SimStatus> {
    return http<SimStatus>("/api/sim/reset", { method: "POST" });
  },
  async upload(file: File): Promise<IngestResult> {
    if (USE_FIXTURES) {
      const text = await file.text();
      const lines = text.split(/\r?\n/).filter((l) => l.trim());
      return { accepted: lines.length, rejected: 0, errors: ["fixture mode: file parsed locally, not sent"] };
    }
    const fd = new FormData();
    fd.append("file", file);
    return http<IngestResult>("/api/upload", { method: "POST", body: fd });
  },
  streamUrl(): string {
    return `${API_URL}/api/stream`;
  },
};
