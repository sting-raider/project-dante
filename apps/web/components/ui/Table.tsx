import { cn } from "@/lib/cn";

type TableProps = {
  children: React.ReactNode;
  /** Caption rendered as a folio label above the table. */
  caption?: string;
  className?: string;
};

/**
 * Editorial table — hairline row rules, no zebra, mono numerals via `tabular`.
 * Horizontal overflow is contained INSIDE the wrapper so mobile never scrolls
 * the page body (plan §50).
 */
export default function Table({ children, caption, className }: TableProps) {
  return (
    <div className={cn("w-full", className)}>
      {caption ? <p className="folio-label mb-2">{caption}</p> : null}
      <div className="overflow-x-auto border border-rule rounded-md bg-paper-bright">
        <table className="w-full min-w-[36rem] border-collapse text-left text-sm">
          {children}
        </table>
      </div>
    </div>
  );
}

export function THead({ children }: { children: React.ReactNode }) {
  return (
    <thead>
      <tr className="border-b border-ink">{children}</tr>
    </thead>
  );
}

export function TH({
  children,
  numeric,
  className,
}: {
  children?: React.ReactNode;
  /** Right-align for money/number columns. */
  numeric?: boolean;
  className?: string;
}) {
  return (
    <th
      scope="col"
      className={cn(
        "px-3 py-2 font-mono text-[0.625rem] font-medium uppercase tracking-[0.14em] text-ink-soft",
        numeric && "text-right",
        className
      )}
    >
      {children}
    </th>
  );
}

export function TBody({ children }: { children: React.ReactNode }) {
  return <tbody>{children}</tbody>;
}

export function TR({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <tr className={cn("border-b border-rule last:border-b-0", className)}>{children}</tr>
  );
}

export function TD({
  children,
  numeric,
  mono,
  className,
}: {
  children?: React.ReactNode;
  numeric?: boolean;
  mono?: boolean;
  className?: string;
}) {
  return (
    <td
      className={cn(
        "px-3 py-2.5 align-top leading-snug",
        mono && "font-mono text-xs",
        numeric && "tabular text-right",
        className
      )}
    >
      {children}
    </td>
  );
}
