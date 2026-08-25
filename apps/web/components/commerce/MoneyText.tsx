import { formatINR, formatINRExact } from "@/lib/format";
import { cn } from "@/lib/cn";

type MoneyTextProps = {
  /** Amount in integer paise (API contract). */
  paise: number | null | undefined;
  /** Render exact paisa precision (audit surfaces). Default: rounded rupees. */
  precise?: boolean;
  size?: "sm" | "md" | "lg" | "xl";
  className?: string;
};

const sizes = {
  sm: "text-sm",
  md: "text-lg",
  lg: "text-2xl md:text-3xl",
  xl: "font-display text-5xl md:text-6xl leading-none tracking-[-0.02em]",
} as const;

/**
 * Money in editorial type — tabular figures, en-IN grouping. Missing values
 * render an em-dash so stat strips never show ₹0 for "unknown".
 */
export default function MoneyText({ paise, precise = false, size = "md", className }: MoneyTextProps) {
  return (
    <span
      className={cn(
        "tabular font-medium text-ink",
        sizes[size],
        size === "xl" && "font-display font-normal",
        className
      )}
    >
      {precise ? formatINRExact(paise) : formatINR(paise)}
    </span>
  );
}
