import Link from "next/link";
import { cn } from "@/lib/cn";

type FolioProps = {
  /** Left slot: issue-style label, e.g. "ISSUE 01 / BUY". */
  issue: string;
  /** Right slot: running folio, e.g. "DOSSIER / COV-1842" or a page path. */
  running?: string;
  /** Optional right-hand link target for `running`. */
  href?: string;
  className?: string;
};

/**
 * Page folio header — the thin editorial masthead strip that sits at the top
 * of every page/section (plan §27.5 motifs). Not the site nav; a print-folio.
 */
export default function Folio({ issue, running, href, className }: FolioProps) {
  return (
    <div
      className={cn(
        "flex items-baseline justify-between gap-4 border-b border-rule pb-3",
        className
      )}
    >
      <span className="folio-label text-ink">{issue}</span>
      {running ? (
        href ? (
          <Link
            href={href}
            className="folio-label underline-offset-4 hover:text-signal hover:underline"
          >
            {running}
          </Link>
        ) : (
          <span className="folio-label">{running}</span>
        )
      ) : null}
    </div>
  );
}
