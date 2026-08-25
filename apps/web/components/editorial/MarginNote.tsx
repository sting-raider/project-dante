import { cn } from "@/lib/cn";

type MarginNoteProps = {
  /** The note marker, e.g. "†" or "01". */
  marker?: string;
  children: React.ReactNode;
  className?: string;
};

/**
 * Margin note — small annotation set beside the main column (plan §27.5).
 * Use inside grid layouts: place in a side column next to the content it
 * annotates. Reads as regular text for screen readers.
 */
export default function MarginNote({ marker = "†", children, className }: MarginNoteProps) {
  return (
    <aside
      className={cn(
        "border-l border-rule pl-3 text-[0.8125rem] leading-relaxed text-ink-soft",
        className
      )}
    >
      <span aria-hidden={true} className="mr-1.5 font-mono text-signal">
        {marker}
      </span>
      {children}
    </aside>
  );
}
