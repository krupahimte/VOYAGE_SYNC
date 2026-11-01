import { useState } from "react";
import type { FormEvent, ChangeEvent } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import type {
  TripFormValues,
  TravelStyle,
  Accommodation,
  DailyPace,
  Interest,
  Currency,
} from "../types/trip";

export type PlanStatus = "idle" | "loading" | "success" | "error";

interface TripGoalFormProps {
  onSubmit: (values: TripFormValues) => void;
  /** Planning request state, owned by the parent. */
  status?: PlanStatus;
  /** Message shown with the success or error notice. */
  statusMessage?: string;
}

const DEFAULT_VALUES: TripFormValues = {
  destination: "",
  departureCity: "",
  departureDate: "",
  returnDate: "",
  flexibleDates: false,
  travelers: 1,
  budget: "",
  currency: "USD",
  travelStyle: "balanced",
  accommodation: "hotel",
  interests: [],
  dailyPace: "balanced",
  accessibilityNeeds: "",
  specialRequests: "",
};

const CURRENCIES: Currency[] = ["USD", "EUR", "GBP", "JPY", "INR", "AUD", "CAD"];

const TRAVEL_STYLES: { value: TravelStyle; label: string }[] = [
  { value: "budget", label: "Budget" },
  { value: "balanced", label: "Balanced" },
  { value: "comfort", label: "Comfort" },
  { value: "luxury", label: "Luxury" },
];

const ACCOMMODATIONS: { value: Accommodation; label: string }[] = [
  { value: "hotel", label: "Hotel" },
  { value: "hostel", label: "Hostel" },
  { value: "apartment", label: "Apartment" },
  { value: "flexible", label: "Flexible" },
];

const INTERESTS: { value: Interest; label: string; emoji: string }[] = [
  { value: "culture", label: "Culture", emoji: "🏛️" },
  { value: "food", label: "Food", emoji: "🍜" },
  { value: "nature", label: "Nature", emoji: "🌿" },
  { value: "adventure", label: "Adventure", emoji: "🧗" },
  { value: "shopping", label: "Shopping", emoji: "🛍️" },
  { value: "nightlife", label: "Nightlife", emoji: "🎶" },
  { value: "relaxation", label: "Relaxation", emoji: "🌅" },
];

const DAILY_PACES: { value: DailyPace; label: string; desc: string }[] = [
  { value: "relaxed", label: "Relaxed", desc: "1–2 activities/day" },
  { value: "balanced", label: "Balanced", desc: "3–4 activities/day" },
  { value: "packed", label: "Packed", desc: "5+ activities/day" },
];

/* ── Tiny shared sub-components ─────────────────────────────────── */

function SectionHeading({ number, title }: { number: string; title: string }) {
  return (
    <div className="flex items-center gap-3 mb-5">
      <div
        className="flex items-center justify-center w-6 h-6 rounded-full text-xs font-semibold shrink-0"
        style={{
          backgroundColor: "var(--color-accent-light)",
          color: "var(--color-accent)",
          border: "1px solid var(--color-accent-light)",
        }}
      >
        {number}
      </div>
      <h3
        className="text-sm font-semibold tracking-wide"
        style={{ color: "var(--color-text-primary)" }}
      >
        {title}
      </h3>
    </div>
  );
}

function FormField({
  id,
  label,
  required,
  error,
  children,
}: {
  id: string;
  label: string;
  required?: boolean;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label htmlFor={id} className="form-label">
        {label}
        {required && (
          <span className="ml-0.5" style={{ color: "var(--color-danger)" }} aria-hidden="true">
            *
          </span>
        )}
      </label>
      {children}
      {error && (
        <p className="mt-1 text-xs" style={{ color: "var(--color-danger)" }} role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

/* ── Segmented control ───────────────────────────────────────────── */
function SegmentedControl<T extends string>({
  id,
  label,
  options,
  value,
  onChange,
}: {
  id: string;
  label: string;
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div>
      <p className="form-label" id={`${id}-label`}>
        {label}
      </p>
      <div
        className="flex rounded-lg p-1 gap-1"
        style={{ backgroundColor: "var(--color-bg-alt)", border: "1px solid var(--color-border)" }}
        role="group"
        aria-labelledby={`${id}-label`}
      >
        {options.map((opt) => (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={value === opt.value}
            onClick={() => onChange(opt.value)}
            className={`seg-option${value === opt.value ? " active" : ""}`}
          >
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/* ── Main form ──────────────────────────────────────────────────── */

interface ValidationErrors {
  destination?: string;
  departureCity?: string;
  departureDate?: string;
  returnDate?: string;
  travelers?: string;
  budget?: string;
}

export default function TripGoalForm({ onSubmit, status = "idle", statusMessage = "" }: TripGoalFormProps) {
  const [values, setValues] = useState<TripFormValues>(DEFAULT_VALUES);
  const [travelersText, setTravelersText] = useState(String(DEFAULT_VALUES.travelers));
  const [errors, setErrors] = useState<ValidationErrors>({});
  const [showMore, setShowMore] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  function set<K extends keyof TripFormValues>(key: K, val: TripFormValues[K]) {
    setValues((prev) => ({ ...prev, [key]: val }));
    // Clear error on change and reset success state so user can re-submit
    if (submitted) setSubmitted(false);
    if (key in errors) {
      setErrors((prev) => ({ ...prev, [key]: undefined }));
    }
  }

  function handleText(e: ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) {
    const { name, value } = e.target;
    set(name as keyof TripFormValues, value);
  }

  function toggleInterest(interest: Interest) {
    if (submitted) setSubmitted(false);
    setValues((prev) => ({
      ...prev,
      interests: prev.interests.includes(interest)
        ? prev.interests.filter((i) => i !== interest)
        : [...prev.interests, interest],
    }));
  }

  function validate(): ValidationErrors {
    const e: ValidationErrors = {};
    if (!values.destination.trim()) e.destination = "Destination is required.";
    if (!values.departureCity.trim()) e.departureCity = "Departure city is required.";
    if (!values.departureDate) e.departureDate = "Departure date is required.";
    if (!values.returnDate) {
      e.returnDate = "Return date is required.";
    } else if (values.departureDate && values.returnDate <= values.departureDate) {
      e.returnDate = "Return date must be after departure.";
    }
    if (values.travelers < 1) e.travelers = "At least 1 traveller required.";
    else if (values.travelers > 20) e.travelers = "At most 20 travellers.";
    if (!values.budget.trim()) {
      e.budget = "Budget is required.";
    } else if (isNaN(Number(values.budget))) {
      e.budget = "Budget must be a number.";
    } else if (values.budget.trim() && Number(values.budget) <= 0) {
      e.budget = "Budget must be greater than zero.";
    }
    return e;
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const errs = validate();
    if (Object.keys(errs).length > 0) {
      setErrors(errs);
      return;
    }
    setErrors({});
    setSubmitted(true);
    onSubmit(values);
  }

  return (
    <form
      onSubmit={handleSubmit}
      aria-label="Trip planning form"
      noValidate
    >
      {/* ── Section A: The Journey ─────────────────────────────── */}
      <div
        className="rounded-xl p-6 sm:p-7 mb-4"
        style={{ backgroundColor: "var(--color-surface)", border: "1px solid var(--color-border)", boxShadow: "var(--shadow-card)" }}
      >
        <SectionHeading number="A" title="The journey" />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
          {/* Destination — full width */}
          <div className="sm:col-span-2">
            <FormField id="destination" label="Destination" required error={errors.destination}>
              <input
                id="destination"
                name="destination"
                type="text"
                placeholder="e.g. Kyoto, Japan"
                value={values.destination}
                onChange={handleText}
                className="form-input"
                autoComplete="off"
                spellCheck={false}
                aria-required="true"
              />
            </FormField>
          </div>

          {/* Departure city */}
          <div className="sm:col-span-2">
            <FormField id="departureCity" label="Departure city or airport" required error={errors.departureCity}>
              <input
                id="departureCity"
                name="departureCity"
                type="text"
                placeholder="e.g. London Heathrow (LHR)"
                value={values.departureCity}
                onChange={handleText}
                className="form-input"
                autoComplete="off"
                spellCheck={false}
                aria-required="true"
              />
            </FormField>
          </div>

          {/* Departure date */}
          <FormField id="departureDate" label="Departure date" required error={errors.departureDate}>
            <input
              id="departureDate"
              name="departureDate"
              type="date"
              value={values.departureDate}
              onChange={handleText}
              className="form-input"
              aria-required="true"
            />
          </FormField>

          {/* Return date */}
          <FormField id="returnDate" label="Return date" required error={errors.returnDate}>
            <input
              id="returnDate"
              name="returnDate"
              type="date"
              value={values.returnDate}
              onChange={handleText}
              className="form-input"
              aria-required="true"
              min={values.departureDate || undefined}
            />
          </FormField>

          {/* Flexible dates toggle */}
          <div className="sm:col-span-2 flex items-center gap-3">
            <button
              type="button"
              role="switch"
              aria-checked={values.flexibleDates}
              id="flexibleDates"
              onClick={() => set("flexibleDates", !values.flexibleDates)}
              className="relative inline-flex items-center w-10 h-5 rounded-full transition-colors shrink-0"
              style={{
                backgroundColor: values.flexibleDates ? "var(--color-accent)" : "var(--color-border)",
                border: "none",
                cursor: "pointer",
                padding: 0,
              }}
            >
              <span
                className="absolute w-4 h-4 rounded-full bg-white transition-transform"
                style={{
                  transform: values.flexibleDates ? "translateX(22px)" : "translateX(2px)",
                  boxShadow: "0 1px 3px rgba(0,0,0,0.15)",
                }}
              />
            </button>
            <label htmlFor="flexibleDates" className="text-sm cursor-pointer" style={{ color: "var(--color-text-secondary)" }}>
              Flexible dates <span style={{ color: "var(--color-text-muted)" }}>(±3 days)</span>
            </label>
          </div>

          {/* Travelers */}
          <FormField id="travelers" label="Number of travellers" required error={errors.travelers}>
            <input
              id="travelers"
              name="travelers"
              type="number"
              min={1}
              max={20}
              value={travelersText}
              onChange={(e) => {
                // Let the box be empty or mid-edit while typing; store the number once it is valid.
                setTravelersText(e.target.value);
                const n = parseInt(e.target.value, 10);
                if (n >= 1 && n <= 20) set("travelers", n);
              }}
              onBlur={() => {
                const n = Math.min(20, Math.max(1, parseInt(travelersText, 10) || values.travelers));
                setTravelersText(String(n));
                set("travelers", n);
              }}
              className="form-input"
              aria-required="true"
            />
          </FormField>
        </div>
      </div>

      {/* ── Section B: Budget & Stay ──────────────────────────────── */}
      <div
        className="rounded-xl p-6 sm:p-7 mb-4"
        style={{ backgroundColor: "var(--color-surface)", border: "1px solid var(--color-border)", boxShadow: "var(--shadow-card)" }}
      >
        <SectionHeading number="B" title="Budget & stay" />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
          {/* Budget + currency */}
          <div className="sm:col-span-2">
            <FormField id="budget" label="Total budget" error={errors.budget}>
              <div className="flex gap-2">
                <select
                  id="currency"
                  name="currency"
                  value={values.currency}
                  onChange={handleText}
                  className="form-input"
                  style={{ width: "auto", minWidth: "5rem" }}
                  aria-label="Currency"
                >
                  {CURRENCIES.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
                <input
                  id="budget"
                  name="budget"
                  type="text"
                  inputMode="numeric"
                  placeholder="e.g. 3 000"
                  value={values.budget}
                  onChange={handleText}
                  className="form-input flex-1"
                />
              </div>
            </FormField>
          </div>

          {/* Travel style */}
          <div className="sm:col-span-2">
            <SegmentedControl
              id="travelStyle"
              label="Travel style"
              options={TRAVEL_STYLES}
              value={values.travelStyle}
              onChange={(v) => set("travelStyle", v)}
            />
          </div>

          {/* Accommodation */}
          <div className="sm:col-span-2">
            <SegmentedControl
              id="accommodation"
              label="Accommodation"
              options={ACCOMMODATIONS}
              value={values.accommodation}
              onChange={(v) => set("accommodation", v)}
            />
          </div>
        </div>
      </div>

      {/* ── Section C: Travel Preferences ────────────────────────── */}
      <div
        className="rounded-xl p-6 sm:p-7 mb-4"
        style={{ backgroundColor: "var(--color-surface)", border: "1px solid var(--color-border)", boxShadow: "var(--shadow-card)" }}
      >
        <SectionHeading number="C" title="Travel preferences" />

        {/* Interests */}
        <div className="mb-5">
          <p className="form-label mb-3">Interests</p>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Select your travel interests">
            {INTERESTS.map(({ value, label, emoji }) => (
              <button
                key={value}
                type="button"
                role="checkbox"
                aria-checked={values.interests.includes(value)}
                onClick={() => toggleInterest(value)}
                className={`chip${values.interests.includes(value) ? " active" : ""}`}
              >
                <span className="mr-1.5" aria-hidden="true">{emoji}</span>
                {label}
              </button>
            ))}
          </div>
        </div>

        {/* Daily pace */}
        <div>
          <p className="form-label mb-3" id="pace-label">Daily pace</p>
          <div className="grid grid-cols-3 gap-3" role="group" aria-labelledby="pace-label">
            {DAILY_PACES.map(({ value, label, desc }) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={values.dailyPace === value}
                onClick={() => set("dailyPace", value)}
                className="rounded-lg p-3 text-left transition-all"
                style={{
                  border: values.dailyPace === value
                    ? "1.5px solid var(--color-accent)"
                    : "1px solid var(--color-border)",
                  backgroundColor: values.dailyPace === value
                    ? "var(--color-accent-light)"
                    : "var(--color-surface)",
                  cursor: "pointer",
                }}
              >
                <p
                  className="text-sm font-medium"
                  style={{ color: values.dailyPace === value ? "var(--color-accent)" : "var(--color-text-primary)" }}
                >
                  {label}
                </p>
                <p className="text-xs mt-0.5" style={{ color: "var(--color-text-muted)" }}>{desc}</p>
              </button>
            ))}
          </div>
        </div>

        {/* More preferences (collapsible) */}
        <div className="mt-5">
          <button
            type="button"
            onClick={() => setShowMore((v) => !v)}
            className="flex items-center gap-2 text-sm font-medium transition-colors"
            style={{ color: "var(--color-accent)", background: "none", border: "none", cursor: "pointer", padding: 0 }}
            aria-expanded={showMore}
          >
            {showMore ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
            {showMore ? "Hide" : "More"} preferences
          </button>

          {showMore && (
            <div className="mt-4 grid grid-cols-1 gap-4">
              <FormField id="accessibilityNeeds" label="Accessibility requirements">
                <textarea
                  id="accessibilityNeeds"
                  name="accessibilityNeeds"
                  rows={2}
                  placeholder="e.g. Wheelchair accessible routes, hearing loop at venues…"
                  value={values.accessibilityNeeds}
                  onChange={handleText}
                  className="form-input"
                  style={{ resize: "vertical" }}
                />
              </FormField>
              <FormField id="specialRequests" label="Special requests or notes">
                <textarea
                  id="specialRequests"
                  name="specialRequests"
                  rows={2}
                  placeholder="e.g. Anniversary trip — surprise restaurant recommendations welcome"
                  value={values.specialRequests}
                  onChange={handleText}
                  className="form-input"
                  style={{ resize: "vertical" }}
                />
              </FormField>
            </div>
          )}
        </div>
      </div>

      {/* ── Action row ─────────────────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4 pt-1">
        <button
          id="create-trip-btn"
          type="submit"
          className="btn-primary"
          disabled={status === "loading"}
          aria-busy={status === "loading"}
        >
          {status === "loading" ? "Planning trip…" : "Create trip"}
        </button>

        {Object.keys(errors).length > 0 && (
          <p className="text-sm" style={{ color: "var(--color-danger)" }} role="alert">
            Please fix the errors above before continuing.
          </p>
        )}
      </div>

      {/* Result notice — shown after the planning request finishes */}
      {submitted && (status === "success" || status === "error") && (
        <div
          className="mt-4 rounded-lg px-4 py-3 flex gap-3 items-start"
          style={
            status === "error"
              ? { backgroundColor: "var(--color-warning-bg)", border: "1px solid var(--color-warning-border)" }
              : { backgroundColor: "var(--color-accent-light)", border: "1px solid var(--color-accent-light)" }
          }
          role={status === "error" ? "alert" : "status"}
          aria-live="polite"
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            style={{ color: status === "error" ? "var(--color-danger)" : "var(--color-accent)", marginTop: "2px", flexShrink: 0 }}
            aria-hidden="true"
          >
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
          <p className="text-sm" style={{ color: status === "error" ? "var(--color-danger)" : "var(--color-accent)" }}>
            {statusMessage}
          </p>
        </div>
      )}

      {/* Mode notice */}
      {status !== "success" && status !== "error" && (
        <p
          className="mt-3 text-xs"
          style={{ color: "var(--color-text-muted)" }}
          role="note"
        >
          Plans are built by the VoyageSync orchestrator. Agents currently run in mock mode — no bookings or payments are made.
        </p>
      )}
    </form>
  );
}
