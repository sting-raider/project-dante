import { cn } from "@/lib/cn";
import { statusTone } from "@/lib/design";

type BadgeTone = "neutral" | "success" | "warning" | "danger" | "signal";

const toneClasses: Record<BadgeTone, string> = {
  neutral: "border-rule bg-paper-bright text-ink-soft",
  success: "border-success/40 bg-success/[0.07] text-success",
  warning: "border-warning/40 bg-warning/[0.08] text-warning",
  danger: "border-danger/40 bg-danger/[0.07] text-danger",
  signal: "border-signal/50 bg-signal/[0.08] text-signal-deep",
};

type BadgeProps = {
  /** Status text is ALWAYS rendered — color is never the sole indicator (plan §50). */
  children: string;
  tone?: BadgeTone;
  className?: string;
};

/**
 * Status badge — SATISFIED / BREACH / PAID etc. Small mono chip with a
 * semantic border/tint plus the literal text label.
 */
export default function Badge({ children, tone, className }: BadgeProps) {
  const resolved = tone ?? statusTone(children);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-sm border px-2 py-[3px]",
        "font-mono text-[0.6875rem] uppercase tracking-[0.12em] leading-none",
        toneClasses[resolved],
        className
      )}
    >
      {children}
    </span>
  );
}

export type { BadgeTone };
