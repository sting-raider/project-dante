import { formatDate, formatDateTime, formatTime } from "@/lib/format";
import { cn } from "@/lib/cn";

type DatelineProps = {
  /** ISO-8601 timestamp from the API. */
  iso: string | null | undefined;
  /** "date" → "25 Aug 2026"; "datetime" adds the time; "time" clock only. */
  precision?: "date" | "datetime" | "time";
  prefix?: string;
  className?: string;
};

/**
 * Dateline — mono timestamp in folio style. Renders an em-dash placeholder
 * when the API value is missing so layouts never collapse.
 */
export default function Dateline({
  iso,
  precision = "datetime",
  prefix,
  className,
}: DatelineProps) {
  const text =
    precision === "date"
      ? formatDate(iso)
      : precision === "time"
        ? formatTime(iso)
        : formatDateTime(iso);
  return (
    <span
      className={cn("folio-label tabular inline-flex items-center gap-2", className)}
    >
      {prefix ? <span>{prefix}</span> : null}
      <time dateTime={iso ?? undefined}>{text}</time>
    </span>
  );
}
