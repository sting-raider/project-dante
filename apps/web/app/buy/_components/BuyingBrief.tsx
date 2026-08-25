"use client";

/**
 * BUYING BRIEF column — the parsed intent, rendered as typed constraints.
 * Hard constraints (critical) and soft preferences are separated; values
 * formatted for humans while keys stay machine-mono. Appears after compile.
 */

import type { BuyerIntent } from "@/lib/useContractFlow";
import { rupees } from "@/lib/useContractFlow";
import {
  Badge,
  ConstraintMark,
  MarginNote,
  Panel,
  Rule,
  SectionLabel,
} from "./atoms";

function formatValue(key: string, value: unknown): string {
  if (value == null) return "—";
  if (key.includes("paise") && typeof value === "number") return rupees(value);
  if (typeof value === "string") {
    // ISO dates → readable dates; everything else verbatim
    const d = new Date(value);
    if (!Number.isNaN(d.getTime()) && /^\d{4}-\d{2}-\d{2}/.test(value)) {
      return d.toLocaleDateString("en-IN", {
        weekday: "long",
        day: "numeric",
        month: "short",
      });
    }
    return value;
  }
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function humanKey(key: string): string {
  return key.replace(/[._]/g, " ").replace(/paise$/, "");
}

export function BuyingBrief({
  intent,
  engine,
}: {
  intent: BuyerIntent | null;
  engine: string | null;
}) {
  if (!intent) {
    return (
      <Panel tone="bright">
        <SectionLabel index="§">Buying brief</SectionLabel>
        <div className="mt-4 space-y-3">
          <MarginNote>
            Your words are compiled into typed constraints — category, price cap,
            warranty, delivery deadline — each one checkable against every offer
            the merchant returns.
          </MarginNote>
          <p className="font-body text-[13px] leading-relaxed text-ink-soft">
            Press{" "}
            <span className="font-mono text-[11px] uppercase tracking-[0.15em] text-ink">
              Compile intent
            </span>{" "}
            and the parse appears here before any product is shown.
          </p>
        </div>
      </Panel>
    );
  }

  const hard = intent.hard_constraints.filter((c) => c.critical);
  const soft = intent.soft_preferences;

  return (
    <Panel tone="bright">
      <div className="flex items-center justify-between gap-3">
        <SectionLabel index="§">Buying brief</SectionLabel>
        {engine && <Badge>compiler · {engine}</Badge>}
      </div>

      {/* hard constraints */}
      <ul className="mt-5 space-y-2.5">
        {hard.length === 0 && (
          <li className="font-body text-[13px] italic text-ink-soft">
            No hard constraints parsed.
          </li>
        )}
        {hard.map((c, i) => (
          <li key={`${c.key}-${i}`} className="flex items-start justify-between gap-3">
            <ConstraintMark pass detail={`op ${c.op}`}>
              {humanKey(c.key)}
            </ConstraintMark>
            <span className="text-right font-mono text-[11px] text-ink tabular-nums">
              {formatValue(c.key, c.value)}
            </span>
          </li>
        ))}
      </ul>

      {/* soft preferences */}
      {soft.length > 0 && (
        <>
          <Rule className="my-4" />
          <div className="font-mono text-[10px] uppercase tracking-[0.25em] text-ink-soft">
            Soft preferences
          </div>
          <ul className="mt-2.5 space-y-2">
            {soft.map((c, i) => (
              <li key={`${c.key}-${i}`} className="flex items-start justify-between gap-3">
                <span className="font-mono text-[11px] text-ink-soft">
                  {humanKey(c.key)}
                </span>
                <span className="text-right font-mono text-[11px] text-ink-soft tabular-nums">
                  {formatValue(c.key, c.value)}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      {/* spend authority */}
      {(intent.max_total_amount_paise != null ||
        intent.autonomous_spend_limit_paise != null) && (
        <>
          <Rule className="my-4" />
          <div className="grid grid-cols-2 gap-3 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-soft">
            <div>
              <div>Max total</div>
              <div className="mt-1 font-body text-sm font-semibold normal-case tracking-normal text-ink">
                {rupees(intent.max_total_amount_paise)}
              </div>
            </div>
            <div>
              <div>Autonomous limit</div>
              <div className="mt-1 font-body text-sm font-semibold normal-case tracking-normal text-ink">
                {intent.autonomous_spend_limit_paise != null
                  ? rupees(intent.autonomous_spend_limit_paise)
                  : "—"}
              </div>
            </div>
          </div>
        </>
      )}

      {intent.substitutions_allowed && (
        <>
          <Rule className="my-4" />
          <Badge tone="neutral">substitutions allowed</Badge>
        </>
      )}
    </Panel>
  );
}
