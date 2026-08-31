/**
 * Local editorial atoms for Agent H pages (app/buy, app/contract/[id]).
 *
 * Self-contained so the buyer/contract flow compiles before Agent G's
 * components/editorial + components/commerce land. Styled strictly to the
 * frozen token spec (§27): paper/ink palette, hairline rules, square-ish
 * corners, mono machine-data. At integration, swap usages for
 * components/editorial/* and components/commerce/* equivalents.
 */

import type { ReactNode } from "react";
import { rupees } from "@/lib/useContractFlow";

// ---------------------------------------------------------------- Rule

export function Rule({ className = "" }: { className?: string }) {
  return <hr className={`border-0 border-t border-rule ${className}`} />;
}

// ---------------------------------------------------------------- SectionLabel

export function SectionLabel({
  children,
  index,
  className = "",
}: {
  children: ReactNode;
  index?: string;
  className?: string;
}) {
  return (
    <div className={`flex items-baseline gap-3 ${className}`}>
      {index && (
        <span className="font-mono text-[11px] tracking-[0.2em] text-signal">{index}</span>
      )}
      <h2 className="font-mono text-[11px] uppercase tracking-[0.25em] text-ink-soft">
        {children}
      </h2>
    </div>
  );
}

// ---------------------------------------------------------------- Folio

export function Folio({ children }: { children: ReactNode }) {
  return (
    <div className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink-soft">
      {children}
    </div>
  );
}

// ---------------------------------------------------------------- Dateline

export function Dateline({ children }: { children: ReactNode }) {
  return (
    <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink-soft">
      {children}
    </div>
  );
}

// ---------------------------------------------------------------- MarginNote

export function MarginNote({ children }: { children: ReactNode }) {
  return (
    <p className="border-l-2 border-rule pl-3 font-body text-[13px] leading-relaxed text-ink-soft">
      {children}
    </p>
  );
}

// ---------------------------------------------------------------- Panel

export function Panel({
  children,
  tone = "default",
  className = "",
}: {
  children: ReactNode;
  tone?: "default" | "bright" | "signal";
  className?: string;
}) {
  const tones = {
    default: "bg-paper-bright border-rule shadow-[0_1px_2px_rgba(16,24,40,0.03)]",
    bright: "bg-paper-bright border-rule shadow-[0_1px_2px_rgba(16,24,40,0.03)]",
    signal: "bg-paper-bright border-signal shadow-[0_6px_20px_rgba(255,86,48,0.10)]",
  } as const;
  return (
    <section className={`rounded-lg border ${tones[tone]} p-5 md:p-6 ${className}`}>
      {children}
    </section>
  );
}

// ---------------------------------------------------------------- Button

export function Button({
  children,
  variant = "primary",
  disabled = false,
  onClick,
  type = "button",
  className = "",
  title,
}: {
  children: ReactNode;
  variant?: "primary" | "secondary" | "ghost";
  disabled?: boolean;
  onClick?: () => void;
  type?: "button" | "submit";
  className?: string;
  title?: string;
}) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-md px-5 py-3 font-mono text-[12px] uppercase tracking-[0.18em] transition-colors duration-150 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal disabled:cursor-not-allowed disabled:opacity-40";
  const variants = {
    primary: "bg-action text-white hover:bg-action-deep",
    secondary:
      "border border-rule bg-transparent text-ink hover:border-ink hover:bg-paper-bright",
    ghost: "bg-transparent text-ink-soft underline-offset-4 hover:text-ink hover:underline",
  } as const;
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={`${base} ${variants[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------- Badge / chips

export function Badge({
  children,
  tone = "neutral",
  className = "",
}: {
  children: ReactNode;
  tone?: "neutral" | "success" | "danger" | "warning" | "signal";
  className?: string;
}) {
  const tones = {
    neutral: "border-rule text-ink-soft",
    success: "border-success/40 text-success",
    danger: "border-danger/40 text-danger",
    warning: "border-warning/40 text-warning",
    signal: "border-signal/50 text-signal",
  } as const;
  return (
    <span
      className={`inline-block rounded-sm border px-2 py-[3px] font-mono text-[10px] uppercase tracking-[0.14em] ${tones[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

/** ✓ / ✗ constraint mark with accessible label (color never sole signal). */
export function ConstraintMark({
  pass,
  children,
  detail,
}: {
  pass: boolean;
  children: ReactNode;
  detail?: string;
}) {
  return (
    <span
      title={detail}
      className={`inline-flex items-center gap-1.5 font-mono text-[11px] ${
        pass ? "text-success" : "text-danger"
      }`}
    >
      <span aria-hidden="true">{pass ? "✓" : "✗"}</span>
      <span>{children}</span>
    </span>
  );
}

export function SandboxBadge() {
  return (
    <span className="inline-block rounded-sm border border-dashed border-warning/60 px-2 py-[3px] font-mono text-[10px] uppercase tracking-[0.18em] text-warning">
      Sandbox
    </span>
  );
}

export function SyntheticBadge() {
  return (
    <span className="inline-block rounded-sm border border-dashed border-rule px-2 py-[3px] font-mono text-[10px] uppercase tracking-[0.18em] text-ink-soft">
      Synthetic
    </span>
  );
}

// ---------------------------------------------------------------- MoneyText

export function MoneyText({
  paise,
  size = "md",
  className = "",
}: {
  paise?: number | null;
  size?: "md" | "lg" | "xl";
  className?: string;
}) {
  const sizes = {
    md: "font-body text-base font-semibold text-ink tabular-nums",
    lg: "font-display text-2xl text-ink tabular-nums",
    xl: "font-display text-4xl leading-none text-ink tabular-nums",
  } as const;
  return <span className={`${sizes[size]} ${className}`}>{rupees(paise)}</span>;
}

// ---------------------------------------------------------------- MonoValue

export function MonoValue({ value }: { value: string }) {
  return (
    <code className="break-all font-mono text-[11px] text-ink-soft">{value}</code>
  );
}
