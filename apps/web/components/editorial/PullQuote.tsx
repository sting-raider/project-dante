import { cn } from "@/lib/cn";

type PullQuoteProps = {
  children: React.ReactNode;
  /** Attribution line, e.g. "— buyer's brief, 25 Aug". */
  attribution?: string;
  /** "signal" draws a red rule (buyer voice); default ink. */
  accent?: "ink" | "signal";
  size?: "md" | "lg";
  className?: string;
};

/**
 * Editorial pull quote — the buyer's natural-language brief renders here on
 * /buy; also used for thesis lines and remedy explanations.
 */
export default function PullQuote({
  children,
  attribution,
  accent = "ink",
  size = "lg",
  className,
}: PullQuoteProps) {
  return (
    <figure
      className={cn(
        "border-l-[3px] pl-5 md:pl-8",
        accent === "signal" ? "border-signal" : "border-ink",
        className
      )}
    >
      <blockquote
        className={cn(
          "font-display italic leading-[1.15] tracking-[-0.01em] text-ink",
          size === "lg" ? "text-3xl md:text-[2.75rem]" : "text-2xl md:text-3xl"
        )}
      >
        {children}
      </blockquote>
      {attribution ? (
        <figcaption className="folio-label mt-4">{attribution}</figcaption>
      ) : null}
    </figure>
  );
}
