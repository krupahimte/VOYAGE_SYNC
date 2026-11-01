/**
 * Extended form value types for the full trip-planning form.
 */

export type TravelStyle = "budget" | "balanced" | "comfort" | "luxury";
export type Accommodation = "hotel" | "hostel" | "apartment" | "flexible";
export type DailyPace = "relaxed" | "balanced" | "packed";
export type Interest =
  | "culture"
  | "food"
  | "nature"
  | "adventure"
  | "shopping"
  | "nightlife"
  | "relaxation";
export type Currency = "USD" | "EUR" | "GBP" | "JPY" | "INR" | "AUD" | "CAD";

export interface TripFormValues {
  // Section A – The journey
  destination: string;
  departureCity: string;
  departureDate: string;
  returnDate: string;
  flexibleDates: boolean;
  travelers: number;

  // Section B – Budget & stay
  budget: string;
  currency: Currency;
  travelStyle: TravelStyle;
  accommodation: Accommodation;

  // Section C – Travel preferences
  interests: Interest[];
  dailyPace: DailyPace;
  accessibilityNeeds: string;
  specialRequests: string;
}

/* ── Backend API types (/trips) ─────────────────────────────────── */

/** Body of POST /trips (mirrors backend TripCreateRequest). */
export interface TripGoalPayload {
  destination: string;
  departure_city: string;
  departure_date: string;
  return_date: string;
  travelers: number;
  budget: number;
  currency: Currency;
  travel_style: TravelStyle;
  interests: Interest[];
  preferences: Record<string, string>;
  notes: string;
  flexible_dates: boolean;
  user_id?: string;
}

export type NodeStatus =
  | "PENDING"
  | "READY"
  | "RUNNING"
  | "DONE"
  | "STALE"
  | "AWAITING_APPROVAL"
  | "FAILED";

export interface TaskSummary {
  id: string;
  type: string;
  agent: string;
  status: NodeStatus;
  version: number;
  output: Record<string, unknown> | null;
}

export interface TripCreateResponse {
  trip_id: string;
  graph_id: string;
  user_id: string;
  success: boolean;
  tasks_done: string[];
  tasks_failed: string[];
  tasks_skipped: string[];
  summary: string;
  decomposition: {
    source: "llm" | "fallback";
    attempts: number;
    errors: string[];
    llm_calls: number;
    provider: string;
  };
  contracts_total: number;
  contracts_holding: number;
  usage: {
    llm_calls?: number;
    api_calls?: Record<string, number>;
    api_calls_total?: number;
    provider_modes?: Record<string, string>;
  };
  nodes: TaskSummary[];
}

export interface Activity {
  slot: string;
  name: string;
  time: string;
  start: string;
  end: string;
  duration_minutes: number;
  outdoor: boolean;
  cost: number;
  description?: string;
}

export interface DayItinerary {
  day: number;
  date: string;
  theme: string;
  weather: { condition?: string; temperature_high_c?: number; source?: string } | null;
  activities: Activity[];
  cost: number;
}

/** Response of GET /trips/{id}/itinerary. Item objects carry node_id/status plus agent output. */
export interface TripItinerary {
  trip_id: string;
  destination: string;
  departure_city: string;
  departure_date: string;
  return_date: string;
  travelers: number;
  currency: string;
  summary: string;
  flights: Array<Record<string, any>>;
  stay: Record<string, any> | null;
  transfers: Array<Record<string, any>>;
  days: DayItinerary[];
  budget: Record<string, any> | null;
}
