import { cn } from "@/lib/cn";

type ConstraintMarkProps = {
  /** Human label of the constraint, e.g. "India manufacturer warranty". */
  label: string;
  status: "pass" | "fail" | "unknown";
  /** Optional detail line under the label (expected vs actual, note). */
  detail?: string;
  className?: string;
};

const mark: Record<ConstraintMarkProps["status"], { glyph: string; cls: string; word: string }> = {
  pass: { glyph: "✓", cls: "text-success", word: "PASS" },
  fail: { glyph: "✕", cls: "text-danger", word: "FAIL" },
  unknown: { glyph: "?", cls: "text-warning", word: "UNVERIFIED" },
};

/**
 * Pass/fail constraint row — the ✓/✕ marks on offer candidates and the
 * authorization envelope (plan §28 /buy). Glyph + colored mark + literal
 * PASS/FAIL word so color is never the sole signal.
 */
export default function ConstraintMark({ label, status, detail, className }: ConstraintMarkProps) {
  const m = mark[status];
  return (
    <div className={cn("flex items-start gap-3 py-2", className)}>
      <span
        role="img"
        aria-label={`${m.word}: ${label}`}
        className={cn(
          "mt-[2px] inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-sm border font-mono text-xs leading-none",
          m.cls,
          status === "fail" ? "border-danger/40 bg-danger/[0.07]" : status === "pass" ? "border-success/30 bg-success/[0.07]" : "border-warning/40 bg-warning/[0.08]"
        )}
        aria-hidden={false}
      >
        <span aria-hidden={true}>{m.glyph}</span>
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-medium leading-snug text-ink">
          {label}
          <span className={cn("ml-2 font-mono text-[0.625rem] uppercase tracking-[0.12em]", m.cls)}>
            {m.word}
          </span>
        </span>
        {detail ? (
          <span className="mt-0.5 block font-mono text-xs leading-snug text-ink-soft">
            {detail}
          </span>
        ) : null}
      </span>
    </div>
  );
}

/** Compact variant for dense comparison spreads — glyph + short label only. */
export function ConstraintMarkInline({
  label,
  status,
  className,
}: {
  label: string;
  status: ConstraintMarkProps["status"];
  className?: string;
}) {
  const m = mark[status];
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-sm", className)}>
      <span aria-hidden={true} className={cn("font-mono text-xs", m.cls)}>
        {m.glyph}
      </span>
      <span className="text-ink">{label}</span>
      <span className={cn("font-mono text-[0.5625rem] uppercase tracking-widest", m.cls)}>
        {m.word}
      </span>
    </span>
  );
}
