import { cn } from "@/lib/cn";

type SandboxBadgeProps = {
  /** true → SANDBOX adapter (no real keys); false/undefined → LIVE-TEST-MODE. */
  sandbox: boolean | undefined;
  className?: string;
};

/**
 * Payment-rail provenance chip. LIVE-TEST-MODE = real Razorpay test-mode keys
 * (real Orders API, no real money). SANDBOX = Dante's fake adapter when keys
 * are absent. Always rendered — judges must be able to tell which rail ran.
 */
export default function SandboxBadge({ sandbox, className }: SandboxBadgeProps) {
  const live = !sandbox;
  return (
    <span
      title={
        live
          ? "Executed against Razorpay Test Mode with real API keys — no real money moves."
          : "Executed against the local sandbox adapter — Razorpay keys were not configured."
      }
      className={cn(
        "inline-flex items-center gap-1.5 rounded-sm border px-2 py-[3px]",
        "font-mono text-[0.625rem] uppercase tracking-[0.14em] leading-none",
        live
          ? "border-success/40 bg-success/[0.07] text-success"
          : "border-rule bg-paper-bright text-ink-soft",
        className
      )}
    >
      <span
        aria-hidden={true}
        className={cn("inline-block h-1.5 w-1.5 rounded-full", live ? "bg-success" : "bg-ink-soft")}
      />
      {live ? "Live · Test Mode" : "Sandbox"}
    </span>
  );
}
