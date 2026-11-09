"use client";
// One hook for everything live: topology, health, incidents, raw events, the alert storm,
// and the Chaos Lab actions. Live mode reads GET /api/* + SSE /api/stream; fixture mode
// drives the same reducer from FakeStream. Components only ever see useLiveData().
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type {
  ApplyFixResult,
  Event,
  FaultSpec,
  FaultType,
  Incident,
  IncidentSummary,
  IngestResult,
  Run,
  RunReveal,
  SimStatus,
  SseEventName,
  SsePayloads,
  Topology,
  TopologyState,
} from "@/lib/contracts/types";
import { api, FX, toSummary, USE_FIXTURES } from "@/lib/api";
import { FakeStream, sceneForIncident } from "@/lib/fixture/fakeStream";

const MAX_EVENTS = 500;
const STORM_QUIET_MS = 1500;
const STORM_MAX_MS = 14000;

export type Connection = "connecting" | "open" | "down";
export type StormPhase = "idle" | "flood" | "collapsed";

export interface Storm {
  phase: StormPhase;
  alerts: Event[];
  startedAt: number;
  lastAlertAt: number;
  incidentId: string | null;
}

interface State {
  topology: Topology | null;
  topo: TopologyState | null;
  incidents: IncidentSummary[];
  details: Record<string, Incident>;
  events: Event[];
  storm: Storm;
  selectedId: string | null;
  connection: Connection;
  lastEventAt: number;
  sim: SimStatus | null;
  notice: string | null;
  /** last incident_update seen, with local receive time (backend clocks may be simulated) */
  lastIncident: { id: string; at: number } | null;
}

type Action =
  | { type: "topology"; topology: Topology }
  | { type: "topo"; topo: TopologyState }
  | { type: "list"; list: IncidentSummary[] }
  | { type: "incident"; incident: Incident; at: number }
  | { type: "event"; event: Event; at: number; stormable?: boolean }
  | { type: "conn"; connection: Connection }
  | { type: "collapse"; incidentId: string | null }
  | { type: "arm" }
  | { type: "reset" }
  | { type: "select"; id: string | null }
  | { type: "sim"; sim: SimStatus }
  | { type: "notice"; notice: string | null };

const idleStorm: Storm = { phase: "idle", alerts: [], startedAt: 0, lastAlertAt: 0, incidentId: null };

const initial: State = {
  topology: USE_FIXTURES ? FX.topology : null,
  topo: null,
  incidents: [],
  details: {},
  events: [],
  storm: idleStorm,
  selectedId: null,
  connection: USE_FIXTURES ? "open" : "connecting",
  lastEventAt: 0,
  sim: null,
  notice: null,
  lastIncident: null,
};

function upsert(list: IncidentSummary[], s: IncidentSummary): IncidentSummary[] {
  const rest = list.filter((x) => x.id !== s.id);
  return [s, ...rest].sort((a, b) => b.opened_at.localeCompare(a.opened_at));
}

function reducer(state: State, a: Action): State {
  switch (a.type) {
    case "topology":
      return { ...state, topology: a.topology };
    case "topo":
      return { ...state, topo: a.topo };
    case "list":
      return { ...state, incidents: [...a.list].sort((x, y) => y.opened_at.localeCompare(x.opened_at)) };
    case "incident":
      return {
        ...state,
        details: { ...state.details, [a.incident.id]: a.incident },
        incidents: upsert(state.incidents, toSummary(a.incident)),
        selectedId: state.selectedId ?? (state.storm.phase === "flood" ? null : a.incident.id),
        lastIncident: a.at ? { id: a.incident.id, at: a.at } : state.lastIncident,
      };
    case "event": {
      // SSE replays after a reconnect can repeat ids; keep the buffer (and React keys) unique
      if (state.events.some((e) => e.id === a.event.id)) return state;
      const events = state.events.length >= MAX_EVENTS ? [...state.events.slice(-MAX_EVENTS + 1), a.event] : [...state.events, a.event];
      let storm = state.storm;
      if ((a.stormable ?? a.event.kind === "alert") && storm.phase !== "collapsed") {
        storm =
          storm.phase === "idle"
            ? { phase: "flood", alerts: [a.event], startedAt: a.at, lastAlertAt: a.at, incidentId: null }
            : { ...storm, alerts: [...storm.alerts, a.event], lastAlertAt: a.at };
      }
      return { ...state, events, storm, lastEventAt: a.at };
    }
    case "conn":
      return { ...state, connection: a.connection };
    case "collapse":
      return {
        ...state,
        storm: { ...state.storm, phase: "collapsed", incidentId: a.incidentId },
        selectedId: a.incidentId ?? state.selectedId,
      };
    case "arm":
      // fixture replays restamp recorded scenes to "now", so old samples would interleave
      return { ...state, storm: idleStorm, events: USE_FIXTURES ? [] : state.events };
    case "reset":
      return {
        ...initial,
        topology: state.topology,
        connection: state.connection,
        topo: state.topo,
        sim: state.sim,
      };
    case "select":
      return { ...state, selectedId: a.id };
    case "sim":
      return { ...state, sim: a.sim };
    case "notice":
      return { ...state, notice: a.notice };
  }
}

export interface LiveData extends State {
  fixtureMode: boolean;
  selected: Incident | null;
  start: () => Promise<void>;
  fault: (t: FaultType) => Promise<void>;
  reset: () => Promise<void>;
  upload: (f: File) => Promise<IngestResult>;
  select: (id: string | null) => void;
  dismissNotice: () => void;
  sandbox: Sandbox;
  runBlind: () => Promise<void>;
  runDesigned: (spec: FaultSpec) => Promise<void>;
  revealRun: () => Promise<void>;
  exitSandbox: () => void;
  /** POST apply_fix. Never sets lifecycle itself: live mode follows SSE, sandbox refetches the run. */
  applyFix: (incidentId: string, fixId: string) => Promise<ApplyFixResult>;
}

/** Alert storm: collapse into the incident once alerts go quiet. */
function useStormCollapse(state: State, dispatch: (a: Action) => void) {
  const stormRef = useRef(state);
  useEffect(() => {
    stormRef.current = state;
  });
  useEffect(() => {
    if (state.storm.phase !== "flood") return;
    const iv = setInterval(() => {
      const s = stormRef.current;
      const now = Date.now();
      const quiet = now - s.storm.lastAlertAt > STORM_QUIET_MS;
      const tooLong = now - s.storm.startedAt > STORM_MAX_MS;
      const li = s.lastIncident;
      const fresh = li && li.at >= s.storm.startedAt - 5000 ? li.id : null;
      if (fresh && (quiet || tooLong)) dispatch({ type: "collapse", incidentId: fresh });
      else if (!fresh && now - s.storm.startedAt > STORM_MAX_MS * 2) dispatch({ type: "collapse", incidentId: null });
    }, 250);
    return () => clearInterval(iv);
  }, [state.storm.phase, dispatch]);
}

// ---------------------------------------------------------------- sandbox runs (U1)
export type SandboxStatus = "idle" | "running" | "playing" | "answered" | "error";

/** What the dashboard panels read: the live state, or a sandbox run's own state. */
export interface View {
  incidents: IncidentSummary[];
  details: Record<string, Incident>;
  events: Event[];
  storm: Storm;
  topo: TopologyState | null;
  selectedId: string | null;
  selected: Incident | null;
  select: (id: string | null) => void;
}

interface SandboxMeta {
  status: SandboxStatus;
  run: Run | null;
  reveal: RunReveal | null;
  revealing: boolean;
  error: string | null;
  /** true when the storm is the incident's evidence signals, not raw alerts (runs carry no raw stream) */
  signalStorm: boolean;
  /** a fix was applied to a sandbox incident: refetch the run until the engine reports RESOLVED */
  healing: boolean;
}

export interface Sandbox extends SandboxMeta {
  active: boolean;
  view: View;
}

const idleMeta: SandboxMeta = {
  status: "idle",
  run: null,
  reveal: null,
  revealing: false,
  error: null,
  signalStorm: false,
  healing: false,
};

const HEAL_POLL_MS = 2000;
const HEAL_GIVE_UP_MS = 120000;
const HEAL_STEP_MS = 450;

/** The incident a run is "about": the one with the most alerts. */
function mainIncident(run: Run): Incident | null {
  if (!run.incidents.length) return null;
  return [...run.incidents].sort((a, b) => b.alert_count - a.alert_count)[0];
}

const Ctx = createContext<LiveData | null>(null);

export function LiveDataProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initial);
  const [sb, sbDispatch] = useReducer(reducer, initial);
  const [meta, setMeta] = useState<SandboxMeta>(idleMeta);
  const player = useRef<FakeStream | null>(null);
  const fake = useRef<FakeStream | null>(null);
  const listTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refetchList = useCallback(() => {
    if (USE_FIXTURES) return;
    if (listTimer.current) clearTimeout(listTimer.current);
    listTimer.current = setTimeout(() => {
      api
        .listIncidents()
        .then((list) => dispatch({ type: "list", list }))
        .catch(() => {});
    }, 300);
  }, []);

  const onMessage = useCallback(
    <K extends SseEventName>(name: K, payload: SsePayloads[K]) => {
      if (name === "event") dispatch({ type: "event", event: payload as Event, at: Date.now() });
      else if (name === "topology_state") dispatch({ type: "topo", topo: payload as TopologyState });
      else if (name === "incident_update") {
        dispatch({ type: "incident", incident: payload as Incident, at: Date.now() });
        refetchList();
      }
    },
    [refetchList],
  );

  // ---- fixture mode
  useEffect(() => {
    if (!USE_FIXTURES) return;
    const fs = new FakeStream(onMessage, FX.topology);
    fake.current = fs;
    return () => {
      fs.dispose();
      fake.current = null;
    };
  }, [onMessage]);

  // ---- live mode: initial fetch + SSE with reconnect
  useEffect(() => {
    if (USE_FIXTURES) return;
    let es: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let backoff = 1000;
    let closed = false;

    api
      .getTopology()
      .then((topology) => dispatch({ type: "topology", topology }))
      .catch(() => dispatch({ type: "notice", notice: "Backend unreachable: could not load topology" }));

    const names: SseEventName[] = ["event", "incident_update", "topology_state"];
    const connect = () => {
      if (closed) return;
      dispatch({ type: "conn", connection: "connecting" });
      es = new EventSource(api.streamUrl());
      es.onopen = () => {
        backoff = 1000;
        dispatch({ type: "conn", connection: "open" });
        // resync after every (re)connect: anything sent while we were away is in the list
        api
          .listIncidents()
          .then((list) => dispatch({ type: "list", list }))
          .catch(() => {});
      };
      es.onerror = () => {
        dispatch({ type: "conn", connection: "down" });
        if (es && es.readyState === EventSource.CLOSED) {
          es.close();
          retry = setTimeout(connect, backoff);
          backoff = Math.min(backoff * 2, 10000);
        }
      };
      for (const n of names) {
        es.addEventListener(n, (m) => {
          try {
            onMessage(n, JSON.parse((m as MessageEvent<string>).data));
          } catch {
            /* malformed frame, skip */
          }
        });
      }
    };
    connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      es?.close();
    };
  }, [onMessage]);

  // ---- alert storm: collapse into the incident once alerts go quiet
  useStormCollapse(state, dispatch);
  useStormCollapse(sb, sbDispatch);

  // ---- fetch detail for a selected incident we only have a summary of
  useEffect(() => {
    const id = state.selectedId;
    if (!id || state.details[id]) return;
    api
      .getIncident(id)
      .then((incident) => dispatch({ type: "incident", incident, at: 0 }))
      .catch(() => {});
  }, [state.selectedId, state.details]);

  const start = useCallback(async () => {
    try {
      const sim = USE_FIXTURES ? fake.current!.start() : await api.simStart();
      dispatch({ type: "sim", sim });
    } catch (e) {
      dispatch({ type: "notice", notice: `Start failed: ${(e as Error).message}` });
    }
  }, []);

  const fault = useCallback(async (t: FaultType) => {
    dispatch({ type: "arm" });
    try {
      if (USE_FIXTURES) {
        const { status, supported } = fake.current!.fault(t);
        dispatch({ type: "sim", sim: status });
        if (!supported)
          dispatch({
            type: "notice",
            notice: `Fixture mode scripts only "DB pool exhaustion" and "Red herring deploy". Connect the backend for ${t}.`,
          });
      } else {
        const sim = await api.simFault(t);
        dispatch({ type: "sim", sim });
      }
    } catch (e) {
      dispatch({ type: "notice", notice: `Fault failed: ${(e as Error).message}` });
    }
  }, []);

  const reset = useCallback(async () => {
    try {
      const sim = USE_FIXTURES ? fake.current!.reset() : await api.simReset();
      dispatch({ type: "reset" });
      dispatch({ type: "sim", sim });
      refetchList();
    } catch (e) {
      dispatch({ type: "notice", notice: `Reset failed: ${(e as Error).message}` });
    }
  }, [refetchList]);

  const upload = useCallback(
    async (f: File) => {
      const res = await api.upload(f);
      refetchList();
      return res;
    },
    [refetchList],
  );

  // ---- sandbox: play a run's answer through its own reducer, so the panels show the same
  // flood, graph and collapse as the live Chaos Lab without touching live state
  const stopPlayer = useCallback(() => {
    player.current?.dispose();
    player.current = null;
  }, []);
  useEffect(() => stopPlayer, [stopPlayer]);

  const playRun = useCallback(
    (run: Run) => {
      stopPlayer();
      sbDispatch({ type: "reset" });
      const topology = state.topology ?? FX.topology;
      sbDispatch({ type: "topology", topology });
      const main = mainIncident(run);
      if (!main) {
        setMeta((m) => ({ ...m, status: "answered", run }));
        return;
      }
      const scene = sceneForIncident(main);
      const signalStorm = !scene.events.some((e) => e.kind === "alert");
      const extras = run.incidents.filter((i) => i.id !== main.id);
      setMeta((m) => ({ ...m, status: "playing", run, signalStorm }));
      const p = new FakeStream((name, payload) => {
        if (name === "event") {
          const e = payload as Event;
          sbDispatch({ type: "event", event: e, at: Date.now(), stormable: signalStorm ? e.severity !== "info" : e.kind === "alert" });
        } else if (name === "topology_state") sbDispatch({ type: "topo", topo: payload as TopologyState });
        else if (name === "incident_update") {
          sbDispatch({ type: "incident", incident: payload as Incident, at: Date.now() });
          for (const x of extras) sbDispatch({ type: "incident", incident: x, at: 0 });
        }
      }, topology);
      player.current = p;
      p.play(scene);
    },
    [state.topology, stopPlayer],
  );

  const startRun = useCallback(
    async (fn: () => Promise<Run>) => {
      stopPlayer();
      sbDispatch({ type: "reset" });
      setMeta({ ...idleMeta, status: "running" });
      try {
        const run = await fn();
        playRun(run);
      } catch (e) {
        setMeta({ ...idleMeta, status: "error", error: (e as Error).message });
      }
    },
    [playRun, stopPlayer],
  );

  const runBlind = useCallback(() => startRun(() => api.runBlind()), [startRun]);
  const runDesigned = useCallback(
    (spec: FaultSpec) =>
      startRun(async () => {
        const run = await api.runDesigned(spec);
        if (USE_FIXTURES && run.spec && (run.spec.target !== spec.target || run.spec.kind !== spec.kind || spec.decoy))
          dispatch({
            type: "notice",
            notice: `Fixture mode replays the one recorded run (${run.spec.target} ${run.spec.kind}). Connect the backend to run your design.`,
          });
        return run;
      }),
    [startRun],
  );

  const runId = meta.run?.run_id;
  const revealRun = useCallback(async () => {
    if (!runId) return;
    setMeta((m) => ({ ...m, revealing: true }));
    try {
      const reveal = await api.revealRun(runId);
      setMeta((m) => (m.run?.run_id === runId ? { ...m, reveal, revealing: false } : m));
    } catch (e) {
      setMeta((m) => ({ ...m, revealing: false, error: `Reveal failed: ${(e as Error).message}` }));
    }
  }, [runId]);

  const exitSandbox = useCallback(() => {
    stopPlayer();
    sbDispatch({ type: "reset" });
    setMeta(idleMeta);
  }, [stopPlayer]);

  // the run is answered once its incident has landed and the storm has collapsed
  const sbHasIncident = Object.keys(sb.details).length > 0;
  const answered = meta.status === "playing" && sbHasIncident && sb.storm.phase !== "flood";
  useEffect(() => {
    if (answered) setMeta((m) => ({ ...m, status: "answered" }));
  }, [answered]);

  // designed runs show their spec up front, so fetch the match verdict as soon as they answer
  const autoReveal = meta.status === "answered" && meta.run?.mode === "designed" && !meta.reveal && !meta.revealing && !meta.error;
  useEffect(() => {
    if (autoReveal) void revealRun();
  }, [autoReveal, revealRun]);

  const sbSelect = useCallback((id: string | null) => sbDispatch({ type: "select", id }), []);

  // ---- apply fix (U5)
  const sbRef = useRef(sb);
  useEffect(() => {
    sbRef.current = sb;
  });
  const healTimers = useRef<ReturnType<typeof setTimeout>[]>([]);
  useEffect(() => () => healTimers.current.forEach(clearTimeout), []);

  const applyFix = useCallback(async (incidentId: string, fixId: string) => {
    const res = await api.applyFix(incidentId, fixId);
    if (res.applied && sbRef.current.details[incidentId]) setMeta((m) => ({ ...m, healing: true }));
    return res;
  }, []);

  // sandbox runs have no SSE: refetch GET /api/runs/{id} until the engine reports RESOLVED,
  // then turn the graph green hop by hop (root first). Only the backend's lifecycle counts.
  useEffect(() => {
    if (!meta.healing || !runId) return;
    const started = Date.now();
    let stopped = false;
    const iv = setInterval(() => {
      if (Date.now() - started > HEAL_GIVE_UP_MS) {
        clearInterval(iv);
        setMeta((m) => ({ ...m, healing: false }));
        return;
      }
      api
        .getRun(runId)
        .then((run) => {
          if (stopped) return;
          for (const i of run.incidents) sbDispatch({ type: "incident", incident: i, at: 0 });
          const done = run.incidents.length > 0 && run.incidents.every((i) => i.lifecycle === "RESOLVED");
          if (!done) return;
          stopped = true;
          clearInterval(iv);
          setMeta((m) => ({ ...m, healing: false }));
          const topo = sbRef.current.topo;
          if (!topo) return;
          const order = Array.from(new Set(run.incidents.flatMap((i) => i.timeline.map((t) => t.service))));
          const healed = new Set<string>();
          order.forEach((svc, k) => {
            healTimers.current.push(
              setTimeout(() => {
                healed.add(svc);
                const last = k === order.length - 1;
                sbDispatch({
                  type: "topo",
                  topo: {
                    ts: new Date().toISOString(),
                    services: topo.services.map((s) =>
                      healed.has(s.service) || last ? { ...s, health: "healthy", is_root_cause: false } : s,
                    ),
                    hot_edges: last ? [] : topo.hot_edges.filter(([a, b]) => !healed.has(a) && !healed.has(b)),
                  },
                });
              }, k * HEAL_STEP_MS),
            );
          });
        })
        .catch(() => {});
    }, HEAL_POLL_MS);
    return () => {
      stopped = true;
      clearInterval(iv);
    };
  }, [meta.healing, runId]);

  const select = useCallback((id: string | null) => dispatch({ type: "select", id }), []);
  const dismissNotice = useCallback(() => dispatch({ type: "notice", notice: null }), []);

  const value = useMemo<LiveData>(
    () => ({
      ...state,
      fixtureMode: USE_FIXTURES,
      selected: state.selectedId ? (state.details[state.selectedId] ?? null) : null,
      start,
      fault,
      reset,
      upload,
      select,
      dismissNotice,
      sandbox: {
        ...meta,
        active: meta.status !== "idle",
        view: {
          incidents: sb.incidents,
          details: sb.details,
          events: sb.events,
          storm: sb.storm,
          topo: sb.topo,
          selectedId: sb.selectedId,
          selected: sb.selectedId ? (sb.details[sb.selectedId] ?? null) : null,
          select: sbSelect,
        },
      },
      runBlind,
      runDesigned,
      revealRun,
      exitSandbox,
      applyFix,
    }),
    [state, start, fault, reset, upload, select, dismissNotice, sb, meta, sbSelect, runBlind, runDesigned, revealRun, exitSandbox, applyFix],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLiveData(): LiveData {
  const v = useContext(Ctx);
  if (!v) throw new Error("useLiveData must be used inside <LiveDataProvider>");
  return v;
}
