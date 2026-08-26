"use client";

import { useEffect, useRef, useState } from "react";
import { animate, useInView, useReducedMotion } from "motion/react";
import { cn } from "@/lib/cn";

type StatNumeralProps = {
  /** Final value to roll to. */
  value: number;
  /** Render the numeric value; keep formatting outside (e.g. formatINR). */
  format?: (v: number) => string;
  label?: string;
  /** Folio-style kicker above/below the numeral, e.g. "LIVE CONTRACTS". */
  caption?: string;
  prefix?: string;
  suffix?: string;
  className?: string;
};

/**
 * Oversized editorial numeral with a restrained roll-up on first view
 * (plan §27.6 "number rolling for metrics"). Honors prefers-reduced-motion
 * by snapping straight to the final value.
 *
 * Accessibility (#15): the animated/visual numeral is aria-hidden; screen
 * readers get a plain-text equivalent instead of an invalid aria-label on a
 * non-widget span. Until first paint of real data the layout is reserved
 * with a dash — never a misleading 0.
 */
export default function StatNumeral({
  value,
  format = (v) => new Intl.NumberFormat("en-IN").format(v),
  label,
  caption,
  prefix,
  suffix,
  className,
}: StatNumeralProps) {
  const reduced = useReducedMotion();
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: "-40px" });
  // null = not yet rolled: reserve space with a dash rather than painting 0.
  const [display, setDisplay] = useState<number | null>(reduced ? value : null);
  const rolledRef = useRef(reduced);

  useEffect(() => {
    if (!inView || rolledRef.current) return;
    if (reduced) {
      rolledRef.current = true;
      setDisplay(value);
      return;
    }
    rolledRef.current = true;
    const controls = animate(0, value, {
      duration: Math.min(1.1, 0.5 + Math.abs(value) / 100000),
      ease: [0.22, 0.61, 0.36, 1],
      onUpdate: (v) => setDisplay(v),
    });
    return () => controls.stop();
  }, [inView, reduced, value]);

  // Live updates after mount: if `value` changes (polling), jump without drama.
  const lastTarget = useRef(value);
  useEffect(() => {
    if (lastTarget.current !== value) {
      lastTarget.current = value;
      if (rolledRef.current || reduced) setDisplay(value);
    }
  }, [value, reduced]);

  const accessibleText =
    label ??
    `${prefix ?? ""}${format(Math.round((display ?? 0) * 100) / 100)}${suffix ?? ""}`;

  return (
    <div className={cn("min-w-0", className)}>
      <span ref={ref} className="block">
        {/* Screen-reader text (real content, not an aria-label on a span). */}
        <span className="sr-only">{accessibleText}</span>
        <span
          aria-hidden={true}
          className="tabular block font-display text-6xl leading-none tracking-[-0.02em] text-ink md:text-7xl"
        >
          {display == null
            ? "—"
            : `${prefix ?? ""}${format(Math.round(display * 100) / 100)}${suffix ?? ""}`}
        </span>
      </span>
      {(caption || label) && (
        <span className="folio-label mt-2 block">{caption ?? label}</span>
      )}
    </div>
  );
}
