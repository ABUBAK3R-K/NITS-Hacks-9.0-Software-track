"use client";
import { useMemo, useState } from "react";
import {
  Background,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge as FlowEdge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import type { Health, Incident, Tier, Topology, TopologyState } from "@/lib/contracts/types";
import { HEALTH_COLOR } from "./ui";

// Hand-tuned layout: callers on the left, data and external dependencies on the right.
const POS: Record<string, { x: number; y: number }> = {
  "api-gateway": { x: 0, y: 235 },
  "auth-svc": { x: 270, y: 95 },
  "payments-svc": { x: 270, y: 375 },
  "ledger-svc": { x: 540, y: 330 },
  "redis-session": { x: 810, y: 0 },
  postgres: { x: 810, y: 200 },
  "psp-adapter": { x: 810, y: 470 },
};

const TIER_LABEL: Record<Tier, string> = { edge: "EDGE", app: "APP", data: "DATA", external: "EXTERNAL" };

type ServiceData = {
  label: string;
  id: string;
  tier: Tier;
  health: Health;
  root: boolean;
  candidate: boolean;
  score: number | null;
  alerts: number;
};

function ServiceNode({ data }: NodeProps<Node<ServiceData>>) {
  const color = HEALTH_COLOR[data.health];
  const hot = data.health !== "healthy";
  // U7: a ripple each time health changes, so failure visibly travels hop by hop
  const [prev, setPrev] = useState(data.health);
  const [ripple, setRipple] = useState(0);
  if (prev !== data.health) {
    setPrev(data.health);
    setRipple((r) => r + 1);
  }
  return (
    <div
      className={`relative w-[200px] rounded-lg border-2 bg-sheet px-3.5 py-2.5 transition-colors duration-500 ${
        data.root ? "root-pulse" : data.candidate ? "cand-pulse" : ""
      }`}
      style={{
        borderColor: data.candidate && !data.root ? "#f5a623" : hot ? color : "#2e2e2e",
        borderWidth: hot || data.candidate ? 2 : 1,
        background: hot ? `color-mix(in srgb, ${color} 8%, #111111)` : "#111111",
      }}
    >
      {ripple > 0 && (
        <span
          key={ripple}
          className="node-ripple pointer-events-none absolute inset-0 rounded-lg"
          style={{ "--ripple": color } as React.CSSProperties}
        />
      )}
      <Handle type="target" position={Position.Left} className="!h-2 !w-2 !border-0 !bg-subtle" />
      <Handle type="source" position={Position.Right} className="!h-2 !w-2 !border-0 !bg-subtle" />
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-bold tracking-widest text-subtle">{TIER_LABEL[data.tier]}</span>
        <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase" style={{ color }}>
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: color }} />
          {data.health}
        </span>
      </div>
      <div className="mt-0.5 text-[17px] font-bold leading-tight text-ink">{data.label}</div>
      <div className="font-mono text-xs text-muted">{data.id}</div>
      {data.score !== null && (
        <div className="mt-1 font-mono text-xs text-ink-2">
          score <span className="font-bold text-ink">{data.score.toFixed(2)}</span>
        </div>
      )}
      {data.root && (
        <span className="absolute -top-3 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-md bg-danger px-2 py-0.5 text-[11px] font-black tracking-widest text-paper">
          ROOT CAUSE
        </span>
      )}
      {data.candidate && !data.root && (
        <span className="absolute -top-3 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-md bg-warn px-2 py-0.5 text-[11px] font-black tracking-widest text-paper">
          POSSIBLE CAUSE
        </span>
      )}
      {data.alerts > 0 && (
        <span className="absolute -right-3 -top-3 flex h-7 min-w-7 items-center justify-center rounded-full bg-danger px-1.5 text-sm font-black text-paper ring-2 ring-paper">
          {data.alerts}
        </span>
      )}
    </div>
  );
}

const nodeTypes = { service: ServiceNode };

export function ServiceGraph({
  topology,
  topo,
  incident,
  alertCounts,
}: {
  topology: Topology | null;
  topo: TopologyState | null;
  incident: Incident | null;
  alertCounts: Record<string, number>;
}) {
  const { nodes, edges } = useMemo(() => {
    if (!topology) return { nodes: [] as Node<ServiceData>[], edges: [] as FlowEdge[] };
    const health = new Map(topo?.services.map((s) => [s.service, s]) ?? []);
    const abstained = incident?.status === "ABSTAINED";
    const top2 = abstained ? new Set(incident!.candidates.slice(0, 2).map((c) => c.service)) : new Set<string>();
    const scores = new Map(incident?.candidates.map((c) => [c.service, c.score]) ?? []);
    const rootFromIncident = incident?.status === "OPEN" ? incident.root_cause?.service : undefined;
    const hot = new Set((topo?.hot_edges ?? []).map(([a, b]) => `${a}->${b}`));

    const nodes: Node<ServiceData>[] = topology.services.map((s, i) => {
      const h = health.get(s.id);
      return {
        id: s.id,
        type: "service",
        position: POS[s.id] ?? { x: 270 * (i % 4), y: 560 + 110 * Math.floor(i / 4) },
        draggable: false,
        data: {
          label: s.label,
          id: s.id,
          tier: s.tier,
          health: h?.health ?? "healthy",
          root: Boolean(h?.is_root_cause) || s.id === rootFromIncident,
          candidate: top2.has(s.id),
          score: scores.get(s.id) ?? null,
          alerts: alertCounts[s.id] ?? 0,
        },
      };
    });
    const edges: FlowEdge[] = topology.edges.map((e) => {
      const isHot = hot.has(`${e.from}->${e.to}`);
      return {
        id: `${e.from}->${e.to}`,
        source: e.from,
        target: e.to,
        animated: isHot,
        className: isHot ? "hot" : "",
        style: { stroke: isHot ? "#ff6166" : "#454545", strokeWidth: isHot ? 3 : 1.6 },
        markerEnd: { type: MarkerType.ArrowClosed, color: isHot ? "#ff6166" : "#707070", width: 18, height: 18 },
      };
    });
    return { nodes, edges };
  }, [topology, topo, incident, alertCounts]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      fitView
      fitViewOptions={{ padding: 0.14 }}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      panOnDrag={false}
      zoomOnScroll={false}
      zoomOnPinch={false}
      zoomOnDoubleClick={false}
      preventScrolling={false}
      colorMode="dark"
      style={{ background: "transparent" }}
    >
      <Background color="#1f1f1f" gap={22} size={1.2} />
    </ReactFlow>
  );
}
