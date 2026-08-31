"use client";

/**
 * Typed buyer brief. Multi-item intents keep shared constraints at the top
 * and render each item's own cap/features in its own card so a monitor rule
 * cannot accidentally become a keyboard rule.
 */

import type { BuyerIntent, Constraint, IntentItem } from "@/lib/useContractFlow";
import { rupees } from "@/lib/useContractFlow";
import { Badge, ConstraintMark, MarginNote, Panel, Rule, SectionLabel } from "./atoms";

function formatValue(key: string, value: unknown): string {
  if (value == null) return "—";
  if (key.includes("paise") && typeof value === "number") return rupees(value);
  if (typeof value === "string") {
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
  if (Array.isArray(value)) return value.map(String).join(" · ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function humanKey(key: string): string {
  return key.replace(/[._]/g, " ").replace(/paise$/, "");
}

function ConstraintRows({ constraints }: { constraints: Constraint[] }) {
  const hard = constraints.filter((constraint) => constraint.critical);
  return (
    <ul className="space-y-2">
      {hard.length === 0 && (
        <li className="font-body text-[13px] italic text-ink-soft">No hard constraints parsed.</li>
      )}
      {hard.map((constraint, index) => (
        <li key={`${constraint.key}-${index}`} className="flex items-start justify-between gap-3">
          <ConstraintMark detail={`op ${constraint.op}`} pass>
            {humanKey(constraint.key)}
          </ConstraintMark>
          <span className="max-w-[56%] text-right font-mono text-[11px] text-ink tabular-nums">
            {formatValue(constraint.key, constraint.value)}
          </span>
        </li>
      ))}
    </ul>
  );
}

function ItemCard({ item }: { item: IntentItem }) {
  return (
    <div className="rounded-lg border border-rule bg-paper p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-ink">{item.label}</div>
          <div className="mt-1 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-soft">
            line {item.id} · qty {item.quantity}
          </div>
        </div>
        {item.max_price_paise != null && <Badge>{`up to ${rupees(item.max_price_paise)}`}</Badge>}
      </div>
      <div className="mt-4">
        <ConstraintRows constraints={item.hard_constraints} />
      </div>
      {item.soft_preferences.length > 0 && (
        <>
          <Rule className="my-3" />
          <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-soft">Preferences</div>
          <ul className="mt-2 space-y-1.5">
            {item.soft_preferences.map((preference, index) => (
              <li key={`${preference.key}-${index}`} className="flex items-baseline justify-between gap-3 font-mono text-[11px] text-ink-soft">
                <span>{humanKey(preference.key)}</span>
                <span className="text-right">{formatValue(preference.key, preference.value)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export function BuyingBrief({ intent }: { intent: BuyerIntent | null }) {
  if (!intent) {
    return (
      <Panel tone="bright">
        <SectionLabel index="§">Buying brief</SectionLabel>
        <div className="mt-4 space-y-3">
          <MarginNote>
            Your words become typed constraints before the merchant shelf is opened. Dante checks
            every line against structured catalog evidence.
          </MarginNote>
          <p className="font-body text-[13px] leading-relaxed text-ink-soft">
            Press <span className="font-mono text-[11px] uppercase tracking-[0.15em] text-ink">Compile intent</span> and the parse appears here.
          </p>
        </div>
      </Panel>
    );
  }

  const items = intent.items ?? [];
  const isBundle = items.length > 0;
  const hard = intent.hard_constraints.filter((constraint) => constraint.critical);
  const soft = intent.soft_preferences;
  const provenance = intent.compilation_provenance;
  const compilerLabel =
    provenance?.engine === "llm"
      ? "LLM compiled"
      : provenance
        ? "Deterministic fallback"
        : "Compiler evidence unavailable";
  const compilerTone = provenance?.engine === "llm" ? "success" : "neutral";

  return (
    <Panel tone="bright">
      <div className="flex items-center justify-between gap-3">
        <SectionLabel index="§">Buying brief</SectionLabel>
        <div className="flex items-center gap-2">
          {isBundle && <Badge tone="signal">{`${items.length} lines`}</Badge>}
          <Badge tone={compilerTone}>{compilerLabel}</Badge>
        </div>
      </div>

      {provenance && (
        <p className="mt-3 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-soft">
          {provenance.engine === "llm"
            ? `${provenance.provider ?? "provider"} · ${provenance.model ?? "model"} · ${provenance.item_count} ${provenance.item_count === 1 ? "line" : "lines"} verified`
            : `rules path · ${provenance.fallback_reason === "not_configured" ? "no provider configured" : "LLM output rejected safely"}`}
        </p>
      )}

      {isBundle ? (
        <div className="mt-5 space-y-3">
          {hard.length > 0 && (
            <div className="rounded-lg border border-action/20 bg-action-soft p-4">
              <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-action-deep">Shared rules</div>
              <div className="mt-3"><ConstraintRows constraints={intent.hard_constraints} /></div>
            </div>
          )}
          {items.map((item) => <ItemCard key={item.id} item={item} />)}
        </div>
      ) : (
        <div className="mt-5"><ConstraintRows constraints={intent.hard_constraints} /></div>
      )}

      {soft.length > 0 && !isBundle && (
        <>
          <Rule className="my-4" />
          <div className="font-mono text-[10px] uppercase tracking-[0.25em] text-ink-soft">Soft preferences</div>
          <ul className="mt-2.5 space-y-2">
            {soft.map((constraint, index) => (
              <li key={`${constraint.key}-${index}`} className="flex items-start justify-between gap-3">
                <span className="font-mono text-[11px] text-ink-soft">{humanKey(constraint.key)}</span>
                <span className="text-right font-mono text-[11px] text-ink-soft tabular-nums">{formatValue(constraint.key, constraint.value)}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {(intent.max_total_amount_paise != null || intent.autonomous_spend_limit_paise != null) && (
        <>
          <Rule className="my-4" />
          <div className="grid grid-cols-2 gap-3 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-soft">
            <div>
              <div>{isBundle ? "Bundle budget" : "Max total"}</div>
              <div className="mt-1 font-body text-sm font-semibold normal-case tracking-normal text-ink">{rupees(intent.max_total_amount_paise)}</div>
            </div>
            <div>
              <div>Autonomous limit</div>
              <div className="mt-1 font-body text-sm font-semibold normal-case tracking-normal text-ink">{intent.autonomous_spend_limit_paise != null ? rupees(intent.autonomous_spend_limit_paise) : "—"}</div>
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
