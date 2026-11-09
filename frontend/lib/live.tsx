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
  type ReactNode,
} from "react";
import type {
  Event,
  FaultType,
  Incident,
  IncidentSummary,
  IngestResult,
  SimStatus,
  SseEventName,
  SsePayloads,
  Topology,
  TopologyState,
} from "@/lib/contracts/types";
import { api, FX, toSummary, USE_FIXTURES } from "@/lib/api";
import { FakeStream } from "@/lib/fixture/fakeStream";

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
  | { type: "event"; event: Event; at: number }
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
      if (a.event.kind === "alert" && storm.phase !== "collapsed") {
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
}

const Ctx = createContext<LiveData | null>(null);

export function LiveDataProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initial);
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
  }, [state.storm.phase]);

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
    }),
    [state, start, fault, reset, upload, select, dismissNotice],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLiveData(): LiveData {
  const v = useContext(Ctx);
  if (!v) throw new Error("useLiveData must be used inside <LiveDataProvider>");
  return v;
}
