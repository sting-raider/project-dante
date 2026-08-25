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
  const [display, setDisplay] = useState(reduced ? value : 0);

  useEffect(() => {
    if (!inView || reduced) {
      setDisplay(value);
      return;
    }
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
      setDisplay(value);
    }
  }, [value]);

  return (
    <div className={cn("min-w-0", className)}>
      <span
        ref={ref}
        aria-label={label ?? `${prefix ?? ""}${format(value)}${suffix ?? ""}`}
        className="tabular block font-display text-6xl leading-none tracking-[-0.02em] text-ink md:text-7xl"
      >
        <span aria-hidden={true}>
          {prefix}
          {format(Math.round(display * 100) / 100)}
          {suffix}
        </span>
      </span>
      {(caption || label) && (
        <span className="folio-label mt-2 block">{caption ?? label}</span>
      )}
    </div>
  );
}
