import { api } from "../config/api";
import type {
  TripCreateResponse,
  TripFormValues,
  TripGoalPayload,
  TripItinerary,
} from "../types/trip";
import type {
  ActionOutcome,
  DisruptionPreset,
  DisruptionResult,
  GateAction,
  Strategy,
  TripGraph,
} from "../types/live";

/** Map the form's values onto the backend TripGoal schema. */
export function toGoalPayload(values: TripFormValues): TripGoalPayload {
  const notes = [values.specialRequests.trim(), values.accessibilityNeeds.trim()
    ? `Accessibility: ${values.accessibilityNeeds.trim()}`
    : ""]
    .filter(Boolean)
    .join("\n");
  return {
    destination: values.destination.trim(),
    departure_city: values.departureCity.trim(),
    departure_date: values.departureDate,
    return_date: values.returnDate,
    travelers: values.travelers,
    budget: Number(values.budget),
    currency: values.currency,
    travel_style: values.travelStyle,
    interests: values.interests,
    preferences: { accommodation: values.accommodation, pace: values.dailyPace },
    notes,
    flexible_dates: values.flexibleDates,
  };
}

async function readError(resp: Response): Promise<string> {
  try {
    const body = await resp.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join("; ");
    }
  } catch {
    /* fall through */
  }
  return `Request failed (${resp.status})`;
}

export async function createTrip(values: TripFormValues): Promise<TripCreateResponse> {
  const resp = await fetch(api.trips, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(toGoalPayload(values)),
  });
  if (!resp.ok) throw new Error(await readError(resp));
  return resp.json();
}

export async function getItinerary(tripId: string): Promise<TripItinerary> {
  const resp = await fetch(api.tripItinerary(tripId));
  if (!resp.ok) throw new Error(await readError(resp));
  return resp.json();
}

async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init);
  if (!resp.ok) throw new Error(await readError(resp));
  return resp.json();
}

const postJson = <T>(url: string, body?: unknown) =>
  getJson<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const getGraph = (tripId: string) => getJson<TripGraph>(api.tripGraph(tripId));

export const getPresets = () => getJson<DisruptionPreset[]>(api.presets);

export const getApprovals = (tripId: string) => getJson<GateAction[]>(api.approvals(tripId));

export const getActions = (tripId: string) => getJson<GateAction[]>(api.tripActions(tripId));

export const injectDisruption = (tripId: string, scenario: string, strategy: Strategy, compareBaseline: boolean) =>
  postJson<DisruptionResult>(api.tripDisruptions(tripId), {
    scenario,
    strategy,
    compare_baseline: compareBaseline,
  });

export const decideApproval = (actionId: string, decision: "approve" | "reject") =>
  postJson<ActionOutcome>(api.approval(actionId), { decision });

export const undoAction = (actionId: string) => postJson<ActionOutcome>(api.undo(actionId));

export interface HealthInfo {
  status: string;
  version: string;
  mode?: string;
}

export async function getHealth(): Promise<HealthInfo> {
  const resp = await fetch(api.health);
  if (!resp.ok) throw new Error(await readError(resp));
  return resp.json();
}
