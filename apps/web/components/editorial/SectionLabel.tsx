import { cn } from "@/lib/cn";

type SectionLabelProps = {
  children: React.ReactNode;
  /** "vertical" renders a rotated spine label (needs a tall parent); "inline" is the standard small-caps strip. */
  orientation?: "inline" | "vertical";
  className?: string;
};

/**
 * Editorial section label — folio typography (mono, uppercase, wide-tracked).
 * Vertical variant reads top-to-bottom like a magazine spine.
 */
export default function SectionLabel({
  children,
  orientation = "inline",
  className,
}: SectionLabelProps) {
  if (orientation === "vertical") {
    return (
      <span
        className={cn(
          "folio-label text-vertical inline-flex items-center gap-2",
          className
        )}
      >
        {children}
      </span>
    );
  }
  return (
    <span className={cn("folio-label inline-flex items-center gap-2", className)}>
      <span aria-hidden={true} className="h-px w-6 bg-ink-soft" />
      {children}
    </span>
  );
}
