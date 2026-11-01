/**
 * Types for the live demo: task graph, Risk Gate actions, disruptions,
 * per-replan metrics and WebSocket messages. They mirror the backend schemas.
 */
import type { NodeStatus, TaskSummary } from "./trip";

export interface GraphEdge {
  source_id: string;
  destination_id: string;
  contract: string;
  description: string;
  holds: boolean | null;
  explanation: string;
}

export interface TripGraph {
  trip_id: string;
  graph_id: string;
  nodes: TaskSummary[];
  edges: GraphEdge[];
}

export type Tier = "T0" | "T1" | "T2";
export type ActionStatus =
  | "applied"
  | "undone"
  | "pending"
  | "approving"
  | "executed"
  | "rejected"
  | "superseded";

/** One Risk Gate action (T0 log entry, T1 notification or T2 approval card). */
export interface GateAction {
  action_id: string;
  trip_id: string;
  node_id: string;
  node_type: string;
  kind: string;
  tier: Tier;
  score: number;
  reasons: string[];
  status: ActionStatus;
  title: string;
  why: string;
  event_id: string;
  option: Record<string, unknown>;
  changes: Array<{ field: string; old: unknown; new: unknown }>;
  currency: string;
  amount: number;
  old_amount: number | null;
  price_difference: number | null;
  cost_delta: number;
  refundable: boolean;
  offer_id: string | null;
  decision: string | null;
  reminders: number;
  last_error: string;
  undoable: boolean;
  result: Record<string, unknown> | null;
}

export interface DisruptionPreset {
  name: string;
  type: string;
  target_node: string;
  new_facts: Record<string, unknown>;
  description: string;
}

export type Strategy = "contract" | "naive" | "full";

export interface StrategyMetrics {
  strategy: string;
  regenerated: string[];
  regenerated_count: number;
  precision: number;
  recall: number;
  f1: number;
  stability: number;
  contracts_holding: number;
  contracts_total: number;
  latency_ms: number;
  llm_calls: number;
  api_calls: number;
}

export interface ReplanMetrics {
  oracle: { affected: string[]; method: string };
  replan: StrategyMetrics;
  b1: StrategyMetrics;
}

export interface DisruptionResult {
  trip_id: string;
  status: "replanned" | "no_effect" | "duplicate" | "invalid";
  message: string;
  event: { event_id: string; type: string; target_node: string; new_facts: Record<string, unknown> };
  broken_edges: string[][];
  stale: string[];
  regenerated: string[];
  changed: string[];
  latency_ms: number;
  contracts_total: number;
  contracts_holding: number;
  actions: GateAction[];
  metrics: ReplanMetrics | null;
}

export interface ActionOutcome {
  action: GateAction;
  node: TaskSummary;
  contracts_total: number;
  contracts_holding: number;
  broken_edges: string[][];
}

export interface LiveMessage {
  type: string;
  trip_id: string;
  at?: string;
  data: Record<string, unknown>;
}

/** How a node is drawn in the graph after the last disruption. */
export type NodeHighlight = "disrupted" | "stale" | "regenerated" | null;

export interface NodeState {
  id: string;
  status: NodeStatus;
}
