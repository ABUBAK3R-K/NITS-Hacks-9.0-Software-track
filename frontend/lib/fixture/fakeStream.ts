// Fixture-mode stand-in for GET /api/stream. Replays recorded scenes (extracted from
// contracts/fixtures/events_*.json into scenes.json) with the same SSE payload shapes the
// backend sends, so components cannot tell the difference.
import type {
  Event,
  FaultType,
  Health,
  Incident,
  SimStatus,
  SsePayloads,
  SseEventName,
  Topology,
  TopologyState,
} from "@/lib/contracts/types";
import { FX } from "@/lib/api";
import scenes from "./scenes.json";

type Emit = <K extends SseEventName>(name: K, payload: SsePayloads[K]) => void;

const SPEED = 2; // replay speed for the fault window
const MAX_GAP_MS = 2500; // quiet gaps longer than this are compressed

export interface Scene {
  events: Event[];
  incident: Incident;
  health: Record<string, Health>;
}

const SCENES: Partial<Record<FaultType, Scene>> = {
  db_pool_exhaustion: {
    events: (scenes as unknown as { db_pool: Event[] }).db_pool,
    incident: FX.hero,
    health: {
      postgres: "critical",
      "auth-svc": "degraded",
      "ledger-svc": "critical",
      "payments-svc": "critical",
      "api-gateway": "critical",
    },
  },
  red_herring: {
    events: (scenes as unknown as { ambiguous: Event[] }).ambiguous,
    incident: FX.ambiguous,
    health: {
      "ledger-svc": "critical",
      "payments-svc": "degraded",
      "api-gateway": "degraded",
      postgres: "degraded",
    },
  },
};

/** Scene for a sandbox run's incident. The recorded hero run replays its real alert stream;
 *  any other run replays only the incident's own evidence events (all we have of it). */
export function sceneForIncident(incident: Incident): Scene {
  const hero = SCENES.db_pool_exhaustion!;
  if (incident.id === hero.incident.id) return { ...hero, incident };
  const health: Record<string, Health> = {};
  for (const s of incident.services) health[s] = "degraded";
  const top = incident.root_cause?.service ?? (incident.status === "ABSTAINED" ? null : incident.candidates[0]?.service);
  if (top) health[top] = "critical";
  if (incident.status === "ABSTAINED") for (const c of incident.candidates.slice(0, 2)) health[c.service] = "critical";
  const events = [...incident.evidence_events].sort((a, b) => a.ts.localeCompare(b.ts));
  return { events, incident, health };
}

let seq = 0;
function noise(i: number): number {
  const x = Math.sin(i * 12.9898) * 43758.5453;
  return x - Math.floor(x);
}

export class FakeStream {
  private timers: ReturnType<typeof setTimeout>[] = [];
  private heartbeat: ReturnType<typeof setInterval> | null = null;
  private running = false;
  private faults: FaultType[] = [];
  private tick = 0;

  constructor(
    private emit: Emit,
    private topology: Topology,
  ) {}

  private status(): SimStatus {
    return { running: this.running, active_faults: [...this.faults], events_in_store: seq };
  }

  private healthy(): TopologyState {
    return {
      ts: new Date().toISOString(),
      services: this.topology.services.map((s) => ({ service: s.id, health: "healthy", is_root_cause: false })),
      hot_edges: [],
    };
  }

  private later(ms: number, fn: () => void) {
    this.timers.push(setTimeout(fn, ms));
  }

  start(): SimStatus {
    if (this.running) return this.status();
    this.running = true;
    this.emit("topology_state", this.healthy());
    // steady baseline traffic so the sparkline and the live dot move before any fault
    this.heartbeat = setInterval(() => {
      if (this.faults.length) return;
      this.tick += 1;
      const ts = new Date().toISOString();
      this.emit("event", {
        id: `evt_fx${++seq}`,
        ts,
        source: "sim",
        service: "api-gateway",
        kind: "metric",
        severity: "info",
        name: "latency_p95_ms",
        value: 110 + noise(this.tick) * 25,
        message: null,
        attrs: {},
      });
      this.emit("event", {
        id: `evt_fx${++seq}`,
        ts,
        source: "sim",
        service: "payments-svc",
        kind: "metric",
        severity: "info",
        name: "error_rate",
        value: 0.003 + noise(this.tick + 99) * 0.006,
        message: null,
        attrs: {},
      });
    }, 2000);
    return this.status();
  }

  fault(type: FaultType): { status: SimStatus; supported: boolean } {
    this.start();
    const scene = SCENES[type];
    if (!scene) return { status: this.status(), supported: false };
    this.clearTimers();
    this.faults = [type];
    this.replay(scene);
    return { status: this.status(), supported: true };
  }

  reset(): SimStatus {
    this.clearTimers();
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.heartbeat = null;
    this.running = false;
    this.faults = [];
    this.emit("topology_state", this.healthy());
    return this.status();
  }

  dispose() {
    this.clearTimers();
    if (this.heartbeat) clearInterval(this.heartbeat);
  }

  private clearTimers() {
    this.timers.forEach(clearTimeout);
    this.timers = [];
  }

  /** Replay one scene (used by the sandbox player for Judge's Chaos runs). */
  play(scene: Scene) {
    this.clearTimers();
    this.emit("topology_state", this.healthy());
    this.replay(scene);
  }

  private replay(scene: Scene) {
    const { incident, events } = scene;
    const firstOnset = new Date(incident.timeline[0]?.ts ?? events[0].ts).getTime();
    const prerollEnd = firstOnset - 2000;
    // a sandbox run without raw alerts storms on its non-info evidence events instead
    const realAlerts = events.filter((e) => e.kind === "alert");
    const alerts = realAlerts.length ? realAlerts : events.filter((e) => e.severity !== "info");
    const lastAlert = alerts.length ? new Date(alerts[alerts.length - 1].ts).getTime() : firstOnset;

    // shift recorded timestamps so the replay reads as "now" in IST
    const shift = Date.now() - prerollEnd;
    const restamp = (e: Event): Event => ({ ...e, ts: new Date(new Date(e.ts).getTime() + shift).toISOString() });

    // baseline history arrives at once, so the chart has a flat line before the spike
    for (const e of events) if (new Date(e.ts).getTime() < prerollEnd) this.emit("event", restamp(e));

    // compressed clock: delay(t) for recorded time t after preroll
    let prev = prerollEnd;
    let clock = 0;
    const delayFor = new Map<string, number>();
    const at = (t: number) => {
      const gap = Math.min(t - prev, MAX_GAP_MS);
      clock += t <= lastAlert ? gap / SPEED : gap;
      prev = t;
      return clock;
    };

    type Item = { t: number; run: () => void };
    const items: Item[] = [];
    for (const e of events) {
      const t = new Date(e.ts).getTime();
      if (t < prerollEnd) continue;
      items.push({ t, run: () => this.emit("event", restamp(e)) });
    }
    // topology lights up hop by hop at each timeline onset
    const lit = new Set<string>();
    incident.timeline.forEach((entry) => {
      const t = new Date(entry.ts).getTime();
      items.push({
        t,
        run: () => {
          lit.add(entry.service);
          this.emit("topology_state", this.stateFor(scene, lit, false));
        },
      });
    });
    // the engine's verdict lands just after the last alert of the storm
    items.push({
      t: lastAlert + 600,
      run: () => {
        incident.services.forEach((s) => lit.add(s));
        this.emit("topology_state", this.stateFor(scene, lit, true));
        const now = new Date().toISOString();
        this.emit("incident_update", {
          ...incident,
          opened_at: restamp({ ts: incident.opened_at } as Event).ts,
          updated_at: now,
          timeline: incident.timeline.map((x) => ({ ...x, ts: restamp({ ts: x.ts } as Event).ts })),
          lifecycle_history: (incident.lifecycle_history ?? []).map((h) => ({ ...h, ts: restamp({ ts: h.ts } as Event).ts })),
          candidates: incident.candidates.map((c) => ({ ...c, onset: c.onset ? restamp({ ts: c.onset } as Event).ts : null })),
          evidence_events: incident.evidence_events.map(restamp),
        });
      },
    });
    items.sort((a, b) => a.t - b.t);
    for (const it of items) {
      const key = `${it.t}`;
      const d = delayFor.get(key) ?? at(it.t);
      delayFor.set(key, d);
      this.later(d, it.run);
    }
  }

  private stateFor(scene: Scene, lit: Set<string>, final: boolean): TopologyState {
    const root = final ? scene.incident.root_cause?.service ?? null : null;
    return {
      ts: new Date().toISOString(),
      services: this.topology.services.map((s) => ({
        service: s.id,
        health: lit.has(s.id) ? (scene.health[s.id] ?? "degraded") : "healthy",
        is_root_cause: s.id === root,
      })),
      hot_edges: final && !root
        ? []
        : this.topology.edges
            .filter((e) => lit.has(e.from) && lit.has(e.to))
            .map((e) => [e.from, e.to] as [string, string]),
    };
  }
}
