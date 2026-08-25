/**
 * Formatting helpers — money is integer paise everywhere in the API
 * (docs/API_CONTRACT.md); timestamps are ISO-8601 strings.
 */

const inr = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

const inrPrecise = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** ₹11,499 — paise to display rupees, no decimals (house convention). */
export function formatINR(paise: number | null | undefined): string {
  if (paise == null || Number.isNaN(paise)) return "—";
  return inr.format(paise / 100);
}

/**
 * Exact paise rendering for audit surfaces where the paisa matters,
 * e.g. refunds computed from odd splits.
 */
export function formatINRExact(paise: number | null | undefined): string {
  if (paise == null || Number.isNaN(paise)) return "—";
  return inrPrecise.format(paise / 100);
}

/** "25 Aug 2026, 4:32 pm" — short en-IN. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  }).format(d);
}

/** "25 Aug" — datelines and timeline rows. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(d);
}

/** "14:32:05 IST-style" clock time from an ISO string. */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(d);
}

/** First 10 characters — hash folios like `7b9c…` on contract covers. */
export function shortHash(h: string | null | undefined): string {
  if (!h) return "—";
  return h.slice(0, 10);
}

/** Percent with no decimals; tolerates 0..1 or already-scaled values. */
export function formatPct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  const pct = Math.abs(value) <= 1 ? value * 100 : value;
  return `${Math.round(pct)}%`;
}

/** Indented JSON for audit/event payload viewers; "—" for empty input. */
export function prettyJson(value: unknown): string {
  if (value == null) return "—";
  try {
    const s = JSON.stringify(value, null, 2);
    return s === "{}" || s === "[]" ? "—" : s;
  } catch {
    return String(value);
  }
}

/** One-line summary of an event payload: first few key=value pairs. */
export function payloadSummary(
  payload: Record<string, unknown> | null | undefined,
  maxPairs = 3
): string {
  if (!payload || typeof payload !== "object") return "—";
  const pairs = Object.entries(payload)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .slice(0, maxPairs)
    .map(([k, v]) => `${k}=${typeof v === "object" ? "{…}" : String(v)}`);
  if (pairs.length === 0) return "—";
  const more = Object.keys(payload).length > maxPairs ? `, +${Object.keys(payload).length - maxPairs}` : "";
  return pairs.join(", ") + more;
}
