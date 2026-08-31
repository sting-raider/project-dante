import { cn } from "@/lib/cn";

type PanelProps = {
  children: React.ReactNode;
  /** Folio-style label strip rendered along the top border. */
  label?: string;
  /** Right-aligned folio slot (e.g. hash, dateline, badge). */
  aside?: React.ReactNode;
  tone?: "paper" | "bright";
  className?: string;
};

/**
 * Bordered paper panel — the house "card". Square-ish corners, 1px rule,
 * no shadow. `tone="bright"` lifts it off the page with paper-bright.
 */
export default function Panel({ children, label, aside, tone = "paper", className }: PanelProps) {
  return (
    <section
      className={cn(
        "rounded-lg border border-rule shadow-[0_1px_2px_rgba(16,24,40,0.03)]",
        tone === "bright" ? "bg-paper-bright" : "bg-paper",
        className
      )}
    >
      {label || aside ? (
        <header className="flex items-baseline justify-between gap-3 border-b border-rule px-4 py-2.5 md:px-5">
          {label ? <span className="folio-label">{label}</span> : <span />}
          {aside ? <span className="flex items-center gap-2">{aside}</span> : null}
        </header>
      ) : null}
      <div className={cn(label || aside ? "px-4 py-4 md:px-5 md:py-5" : "")}>{children}</div>
    </section>
  );
}
