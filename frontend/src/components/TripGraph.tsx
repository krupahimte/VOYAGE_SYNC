import { memo, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  Handle,
  MarkerType,
  Position,
  getBezierPath,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
  type ReactFlowInstance,
} from "reactflow";
import "reactflow/dist/style.css";
import { Bus, Calendar, CloudSun, Hotel, Plane, Wallet } from "lucide-react";
import type { TaskSummary } from "../types/trip";
import type { GraphEdge, NodeHighlight, TripGraph as TripGraphData } from "../types/live";

/**
 * Live Task Dependency Graph (React Flow).
 *
 * Columns follow dependency depth (left → right). Colours, as in the plan's
 * demo: grey = done, red = disrupted, amber = stale, green = regenerated;
 * a dashed blue outline means the node waits for a T2 approval.
 * Hover an edge to see its contract and whether it currently holds.
 */

const NODE_W = 178;
const NODE_H = 62;
const COL_GAP = 70;
const ROW_GAP = 16;

const PALETTE = {
  done: { border: "var(--color-border)", bg: "var(--color-surface)", accent: "var(--color-text-muted)", label: "Done" },
  disrupted: { border: "#c46060", bg: "#fbefef", accent: "#b04040", label: "Disrupted" },
  stale: { border: "#e0b65c", bg: "#fdf6ec", accent: "#b87c20", label: "Stale" },
  regenerated: { border: "#5a7f5b", bg: "#ecf2ec", accent: "#3a5a3b", label: "Regenerated" },
  failed: { border: "#c46060", bg: "#fbefef", accent: "#b04040", label: "Failed" },
} as const;

const AWAITING = "#4f6a9a";

type Palette = keyof typeof PALETTE;

interface TaskNodeData {
  node: TaskSummary;
  highlight: NodeHighlight;
  visible: boolean;
  selected: boolean;
}

const hhmm = (iso: unknown) => (typeof iso === "string" && iso.length >= 16 ? iso.slice(11, 16) : "");

function describe(node: TaskSummary): { title: string; sub: string } {
  const o = (node.output ?? {}) as Record<string, any>;
  switch (node.type) {
    case "FLIGHT": {
      const delay = Number(o.delay_minutes ?? 0);
      return {
        title: `${o.flight_number ?? "Flight"} ${o.origin_iata ?? ""}→${o.destination_iata ?? ""}`,
        sub: o.status === "cancelled"
          ? "cancelled"
          : `${hhmm(o.departure_time)}–${hhmm(o.arrival_time)}${delay ? ` · +${Math.round(delay / 6) / 10}h` : ""}`,
      };
    }
    case "STAY":
      return { title: String(o.hotel_name ?? "Hotel"), sub: `${o.nights ?? "?"} nights · ${o.room_type ?? ""}` };
    case "TRANSFER":
      return {
        title: o.direction === "departure" ? "Transfer to airport" : "Airport transfer",
        sub: `pickup ${hhmm(o.pickup_time)}`,
      };
    case "DAY_PLAN": {
      const acts = Array.isArray(o.activities) ? o.activities.length : 0;
      return { title: `Day ${o.day ?? node.id.match(/\d+/)?.[0] ?? ""} plan`, sub: `${acts} activities` };
    }
    case "WEATHER":
      return {
        title: `Weather day ${node.id.match(/\d+/)?.[0] ?? ""}`,
        sub: String(o.condition ?? "").replace("_", " ") + (o.is_storm ? " ⚠" : ""),
      };
    case "BUDGET":
      return {
        title: "Budget check",
        sub: typeof o.total_cost === "number" ? `${Math.round(o.total_cost)} / ${Math.round(o.budget_limit)}` : "",
      };
    default:
      return { title: node.id, sub: "" };
  }
}

const ICONS: Record<string, React.ReactNode> = {
  FLIGHT: <Plane size={13} strokeWidth={1.75} />,
  STAY: <Hotel size={13} strokeWidth={1.75} />,
  TRANSFER: <Bus size={13} strokeWidth={1.75} />,
  DAY_PLAN: <Calendar size={13} strokeWidth={1.75} />,
  WEATHER: <CloudSun size={13} strokeWidth={1.75} />,
  BUDGET: <Wallet size={13} strokeWidth={1.75} />,
};

const TaskNodeView = memo(function TaskNodeView({ data }: NodeProps<TaskNodeData>) {
  const { node, highlight, visible, selected } = data;
  const key: Palette = node.status === "FAILED" ? "failed" : highlight ?? "done";
  const p = PALETTE[key];
  const awaiting = node.status === "AWAITING_APPROVAL";
  const { title, sub } = describe(node);
  const badge = awaiting && key === "done" ? "Awaiting approval" : node.status === "STALE" ? "Stale" : p.label;
  return (
    <div
      style={{
        width: NODE_W,
        height: NODE_H,
        borderRadius: 8,
        padding: "7px 9px",
        background: p.bg,
        border: awaiting ? `1.5px dashed ${AWAITING}` : `1.5px solid ${p.border}`,
        boxShadow: selected ? "0 0 0 3px rgba(58,90,59,0.25)" : "var(--shadow-card)",
        opacity: visible ? 1 : 0,
        transform: visible ? "none" : "translateY(6px)",
        transition: "opacity 0.35s ease, transform 0.35s ease, background-color 0.4s, border-color 0.4s",
        fontSize: 11,
        lineHeight: 1.3,
        cursor: "pointer",
      }}
      title={`${node.id} · ${node.status} · v${node.version}`}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div className="flex items-center justify-between gap-1">
        <span className="flex items-center gap-1 min-w-0" style={{ color: p.accent === PALETTE.done.accent ? "var(--color-accent)" : p.accent }}>
          {ICONS[node.type]}
          <span className="font-semibold truncate" style={{ color: "var(--color-text-primary)" }}>{title}</span>
        </span>
      </div>
      <div className="truncate" style={{ color: "var(--color-text-muted)", marginTop: 2 }}>{sub}</div>
      <div className="flex items-center justify-between" style={{ marginTop: 3 }}>
        <span
          className="font-medium"
          style={{
            fontSize: 10,
            padding: "0 6px",
            borderRadius: 10,
            color: awaiting && key === "done" ? AWAITING : p.accent,
            background: awaiting && key === "done" ? "#eef2f9" : key === "done" ? "var(--color-bg-alt)" : "rgba(255,255,255,0.6)",
          }}
        >
          {badge}
        </span>
        <span style={{ fontSize: 10, color: "var(--color-text-muted)" }}>v{node.version}</span>
      </div>
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
});

interface ContractEdgeData {
  edge: GraphEdge;
  hovered: boolean;
  hot: boolean;
}

function ContractEdgeView(props: EdgeProps<ContractEdgeData>) {
  const { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, data, style } = props;
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  const edge = data?.edge;
  return (
    <>
      <BaseEdge path={path} markerEnd={markerEnd} style={style} interactionWidth={18} />
      {data?.hovered && edge && (
        <EdgeLabelRenderer>
          <div
            className="rounded-lg px-3 py-2 text-xs"
            style={{
              position: "absolute",
              transform: `translate(-50%, -110%) translate(${labelX}px, ${labelY}px)`,
              background: "var(--color-surface)",
              border: `1px solid ${edge.holds === false ? "#c46060" : "var(--color-border)"}`,
              boxShadow: "var(--shadow-raised)",
              maxWidth: 280,
              pointerEvents: "none",
              zIndex: 1000,
            }}
          >
            <p className="font-semibold" style={{ color: "var(--color-text-primary)" }}>{edge.contract}</p>
            <p style={{ color: "var(--color-text-secondary)" }}>{edge.description}</p>
            <p style={{ color: edge.holds === false ? "#b04040" : "var(--color-accent)", marginTop: 2 }}>
              {edge.holds === false ? "✗ broken" : "✓ holds"}
              {edge.explanation ? ` — ${edge.explanation}` : ""}
            </p>
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const nodeTypes = { task: TaskNodeView };
const edgeTypes = { contract: ContractEdgeView };

/** Column = longest-path depth; rows ordered by the parents' average row (fewer crossings). */
function layout(graph: TripGraphData): { pos: Record<string, { x: number; y: number }>; depth: Record<string, number> } {
  const parents: Record<string, string[]> = {};
  for (const n of graph.nodes) parents[n.id] = [];
  for (const e of graph.edges) parents[e.destination_id]?.push(e.source_id);
  const depth: Record<string, number> = {};
  for (const n of graph.nodes) {
    // graph.nodes arrive in topological order from the API
    depth[n.id] = Math.max(-1, ...parents[n.id].map((p) => depth[p] ?? 0)) + 1;
  }
  const rootRank = (n: TaskSummary) =>
    n.id === "flight_outbound" ? -1 : n.id === "flight_return" ? 99 : Number(n.id.match(/\d+/)?.[0] ?? 50);
  const columns: string[][] = [];
  for (const n of graph.nodes) (columns[depth[n.id]] ??= []).push(n.id);
  const row: Record<string, number> = {};
  const byId = Object.fromEntries(graph.nodes.map((n) => [n.id, n]));
  columns.forEach((col, d) => {
    const key = (id: string) =>
      d === 0
        ? rootRank(byId[id])
        : parents[id].reduce((s, p) => s + (row[p] ?? 0), 0) / Math.max(1, parents[id].length);
    col.sort((a, b) => key(a) - key(b) || a.localeCompare(b));
    col.forEach((id, i) => (row[id] = i));
  });
  const tallest = Math.max(...columns.map((c) => c.length));
  const pos: Record<string, { x: number; y: number }> = {};
  columns.forEach((col, d) => {
    const offset = ((tallest - col.length) * (NODE_H + ROW_GAP)) / 2;
    col.forEach((id, i) => {
      pos[id] = { x: d * (NODE_W + COL_GAP), y: offset + i * (NODE_H + ROW_GAP) };
    });
  });
  return { pos, depth };
}

export default function TripGraph({
  graph,
  highlight,
  selectedId,
  onSelect,
}: {
  graph: TripGraphData;
  highlight: Record<string, NodeHighlight>;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}) {
  const { pos, depth } = useMemo(() => layout(graph), [graph]);
  const maxDepth = Math.max(0, ...Object.values(depth));
  const [shownDepth, setShownDepth] = useState(-1);
  const [hoveredEdge, setHoveredEdge] = useState<string | null>(null);
  const flow = useRef<ReactFlowInstance | null>(null);

  useEffect(() => {
    const refit = () => flow.current?.fitView({ padding: 0.08 });
    window.addEventListener("resize", refit);
    return () => window.removeEventListener("resize", refit);
  }, []);

  // "The graph builds live": reveal the columns in execution (topological) order.
  // The parent remounts this component (key) for a new trip, which restarts the reveal.
  useEffect(() => {
    let d = -1;
    const timer = setInterval(() => {
      d += 1;
      setShownDepth(d);
      if (d >= maxDepth) {
        clearInterval(timer);
        flow.current?.fitView({ padding: 0.08, duration: 300 });
      }
    }, 260);
    return () => clearInterval(timer);
  }, [maxDepth]);

  const nodes: Node<TaskNodeData>[] = useMemo(
    () =>
      graph.nodes.map((n) => ({
        id: n.id,
        type: "task",
        position: pos[n.id],
        data: {
          node: n,
          highlight: highlight[n.id] ?? null,
          visible: depth[n.id] <= shownDepth,
          selected: n.id === selectedId,
        },
        draggable: false,
        ariaLabel: `${describe(n).title}, ${n.status.toLowerCase().replace("_", " ")}`,
      })),
    [graph, pos, depth, highlight, shownDepth, selectedId],
  );

  const edges: Edge<ContractEdgeData>[] = useMemo(
    () =>
      graph.edges.map((e) => {
        const id = `${e.source_id}->${e.destination_id}`;
        const visible = depth[e.source_id] <= shownDepth && depth[e.destination_id] <= shownDepth;
        const hot = Boolean(highlight[e.source_id] && highlight[e.destination_id]);
        const colour = e.holds === false ? "#c46060" : hot ? "#c99a3e" : "#c9c3ba";
        return {
          id,
          source: e.source_id,
          target: e.destination_id,
          type: "contract",
          data: { edge: e, hovered: hoveredEdge === id, hot },
          hidden: !visible,
          animated: hot,
          style: {
            stroke: colour,
            strokeWidth: hoveredEdge === id || hot || e.holds === false ? 2 : 1.2,
            strokeDasharray: e.holds === false ? "5 4" : undefined,
          },
          markerEnd: { type: MarkerType.ArrowClosed, color: colour, width: 14, height: 14 },
        };
      }),
    [graph, depth, shownDepth, highlight, hoveredEdge],
  );

  return (
    <div style={{ height: 560 }} className="rounded-lg" aria-label="Task dependency graph">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.08 }}
        onInit={(instance) => (flow.current = instance)}
        minZoom={0.3}
        maxZoom={1.6}
        zoomOnScroll={false}
        preventScrolling={false}
        nodesConnectable={false}
        onEdgeMouseEnter={(_, e) => setHoveredEdge(e.id)}
        onEdgeMouseLeave={() => setHoveredEdge(null)}
        onNodeClick={(_, n) => onSelect(n.id === selectedId ? null : n.id)}
        onPaneClick={() => onSelect(null)}
      >
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
    </div>
  );
}

export function GraphLegend() {
  const item = (label: string, border: string, bg: string, dashed = false) => (
    <span className="flex items-center gap-1.5">
      <span
        className="inline-block rounded"
        style={{ width: 14, height: 10, background: bg, border: `1.5px ${dashed ? "dashed" : "solid"} ${border}` }}
        aria-hidden="true"
      />
      {label}
    </span>
  );
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs" style={{ color: "var(--color-text-secondary)" }}>
      {item("Done", PALETTE.done.border, PALETTE.done.bg)}
      {item("Disrupted", PALETTE.disrupted.border, PALETTE.disrupted.bg)}
      {item("Stale", PALETTE.stale.border, PALETTE.stale.bg)}
      {item("Regenerated", PALETTE.regenerated.border, PALETTE.regenerated.bg)}
      {item("Awaiting approval", AWAITING, "var(--color-surface)", true)}
      <span style={{ color: "var(--color-text-muted)" }}>Hover an arrow to see its contract.</span>
    </div>
  );
}
