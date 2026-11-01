/**
 * Centralised API configuration.
 * Set VITE_API_BASE_URL in your .env file to point at a different backend.
 */
export const API_BASE_URL: string =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

const trip = (tripId: string) => `${API_BASE_URL}/trips/${encodeURIComponent(tripId)}`;

export const api = {
  health: `${API_BASE_URL}/health`,
  trips: `${API_BASE_URL}/trips`,
  tripGraph: (tripId: string) => `${trip(tripId)}/graph`,
  tripItinerary: (tripId: string) => `${trip(tripId)}/itinerary`,
  tripDisruptions: (tripId: string) => `${trip(tripId)}/disruptions`,
  tripActions: (tripId: string) => `${trip(tripId)}/actions`,
  presets: `${API_BASE_URL}/disruptions/presets`,
  approvals: (tripId: string) => `${API_BASE_URL}/approvals?trip_id=${encodeURIComponent(tripId)}`,
  approval: (actionId: string) => `${API_BASE_URL}/approvals/${encodeURIComponent(actionId)}`,
  undo: (actionId: string) => `${API_BASE_URL}/actions/${encodeURIComponent(actionId)}/undo`,
  live: (tripId: string) =>
    `${API_BASE_URL.replace(/^http/, "ws")}/ws/trips/${encodeURIComponent(tripId)}`,
} as const;
