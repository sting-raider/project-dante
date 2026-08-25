/**
 * Project Dante design tokens — TypeScript mirror of the @theme block in
 * app/globals.css (plan §27). Use these when a value is needed in TS
 * (SVG strokes, inline styles, chart colors). In JSX classNames, prefer the
 * Tailwind utilities (text-ink-soft, border-rule, ...) which read the same
 * CSS variables.
 */

export const palette = {
  paper: "#F2F0EA",
  paperBright: "#FAF9F5",
  ink: "#0C0C0C",
  inkSoft: "#3E3D39",
  rule: "#C9C5BC",
  signal: "#F04A2D",
  signalDeep: "#C93817",
  success: "#235D3A",
  warning: "#A05A00",
  danger: "#B42318",
} as const;

/** Contract lifecycle states that mean "the promise held". */
export const SATISFIED_STATES = [
  "SATISFIED",
  "REMEDIATED",
] as const;

/**
 * Semantic color for a contract/lifecycle status. Always pair with the text
 * label itself — color is never the sole indicator (plan §50).
 */
export function statusTone(
  status: string
): "neutral" | "success" | "warning" | "danger" | "signal" {
  if (SATISFIED_STATES.includes(status as (typeof SATISFIED_STATES)[number])) {
    return "success";
  }
  switch (status) {
    case "BREACH_DETECTED":
    case "FAILED":
      return "danger";
    case "CANCELLED":
      return "neutral";
    case "VERIFYING":
    case "AWAITING_REMEDY_APPROVAL":
      return "warning";
    case "REMEDY_PLANNING":
    case "REMEDY_EXECUTING":
      return "signal";
    default:
      return "neutral";
  }
}

export const MOTION = {
  /** Standard restrained slide distance in px (plan §27.6: 8–16px). */
  rise: { y: 12 },
  fade: { opacity: [0, 1] },
  durationMs: 420,
  easeOut: [0.22, 0.61, 0.36, 1] as const,
} as const;
