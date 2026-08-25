"use client";

/**
 * Offer comparison spread (§28) — every candidate offer as a column-row with
 * pass/fail ConstraintMarks derived from evaluation.hard_failures. Feasible
 * rows are selectable; infeasible rows stay visible, grayed, with the exact
 * failure reasons shown. Visible failures are a feature, not noise.
 */

import type { MerchantOffer, SearchResult } from "@/lib/useContractFlow";
import { rupees } from "@/lib/useContractFlow";
import { Badge, Button, ConstraintMark, Rule } from "./atoms";

function failureFor(
  failures: { key: string }[],
  key: string,
): { key: string } | undefined {
  return failures.find((f) => f.key === key);
}

function deliveryLabel(o: MerchantOffer): string {
  const dp = o.delivery_promise;
  if (dp.promised_by_date) {
    const d = new Date(dp.promised_by_date);
    const day = Number.isNaN(d.getTime())
      ? dp.promised_by_date
      : d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
    return day;
  }
  if (dp.max_days != null) return `${dp.min_days ?? "?"}–${dp.max_days} days`;
  return "—";
}

function warrantyLabel(o: MerchantOffer): string {
  const t = o.terms;
  if (t.warranty_type === "none") return "none";
  if (t.warranty_type === "unknown") return "unknown";
  const parts = [t.warranty_type];
  if (t.warranty_duration_months) parts.push(`${t.warranty_duration_months}mo`);
  if (t.warranty_region) parts.push(t.warranty_region);
  return parts.join(" · ");
}

export function OfferSpread({
  results,
  selectedOfferId,
  onSelect,
  onConfirm,
  submitting,
}: {
  results: SearchResult[];
  selectedOfferId: string | null;
  /** Choose an offer (radio) — no side effects yet. */
  onSelect: (offerId: string) => void;
  /** Freeze the chosen offer into a contract. */
  onConfirm: (offerId: string) => void;
  submitting: boolean;
}) {
  const feasible = results.filter((r) => r.evaluation.feasible);
  const infeasible = results.filter((r) => !r.evaluation.feasible);

  // Feasible first, best soft-score first; failures keep original order.
  const sorted = [
    ...feasible.sort(
      (a, b) =>
        b.evaluation.soft_scores.reduce((s, x) => s + x.weight * x.score, 0) -
        a.evaluation.soft_scores.reduce((s, x) => s + x.weight * x.score, 0),
    ),
    ...infeasible,
  ];

  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-display text-2xl text-ink">The merchant's shelf</h3>
        <span className="font-mono text-[10px] uppercase tracking-[0.25em] text-ink-soft">
          {feasible.length} feasible · {infeasible.length} rejected
        </span>
      </div>

      <div className="mt-5 overflow-x-auto">
        <table className="w-full min-w-[820px] border-collapse text-left">
          <thead>
            <tr className="border-b border-rule">
              {["Offer", "Price", "Delivery", "Warranty", "Constraint check", ""].map(
                (h, i) => (
                  <th
                    key={h + i}
                    className={`pb-2 pr-4 font-mono text-[10px] font-normal uppercase tracking-[0.22em] text-ink-soft ${
                      i === 0 ? "w-[26%]" : ""
                    }`}
                  >
                    {h}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {sorted.map(({ offer, evaluation }) => {
              const ok = evaluation.feasible;
              const isSelected = selectedOfferId === offer.id;
              return (
                <tr
                  key={offer.id}
                  className={`border-b border-rule align-top transition-colors ${
                    ok ? "" : "opacity-55"
                  } ${isSelected ? "bg-paper-bright" : ""}`}
                >
                  {/* title / sku / brand */}
                  <td className="py-4 pr-4">
                    <div className="font-body text-[15px] font-semibold leading-snug text-ink">
                      {offer.title}
                    </div>
                    <div className="mt-1 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-soft">
                      {offer.brand ?? "—"} · sku {offer.sku}
                      {!ok && " · rejected"}
                    </div>
                  </td>

                  {/* price */}
                  <td className="py-4 pr-4">
                    <span
                      className={`font-mono text-[13px] tabular-nums ${
                        ok ? "text-ink" : "text-ink-soft"
                      }`}
                    >
                      {rupees(offer.unit_amount_paise)}
                    </span>
                  </td>

                  {/* delivery */}
                  <td className="py-4 pr-4 font-mono text-[12px] text-ink-soft">
                    {deliveryLabel(offer)}
                  </td>

                  {/* warranty chip */}
                  <td className="py-4 pr-4">
                    {failureFor(evaluation.hard_failures, "warranty.type") ? (
                      <Badge tone="danger">{warrantyLabel(offer)}</Badge>
                    ) : (
                      <span className="font-mono text-[11px] text-ink-soft">
                        {warrantyLabel(offer)}
                      </span>
                    )}
                  </td>

                  {/* constraint marks */}
                  <td className="py-4 pr-4">
                    {ok ? (
                      <>
                        <ConstraintMark pass detail={evaluation.explanation}>
                          all hard constraints
                        </ConstraintMark>
                        {evaluation.soft_scores.length > 0 && (
                          <div className="mt-1 font-mono text-[10px] text-ink-soft">
                            score{" "}
                            {evaluation.soft_scores
                              .reduce((s, x) => s + x.weight * x.score, 0)
                              .toFixed(2)}
                          </div>
                        )}
                      </>
                    ) : (
                      <ul className="space-y-1 border-l-2 border-danger/30 pl-3">
                        {evaluation.hard_failures.map((f, i) => (
                          <li key={i} className="font-mono text-[10px] leading-relaxed text-danger">
                            ✗ {f.key}: expected{" "}
                            <span className="text-ink">{String(f.expected)}</span>, got{" "}
                            <span className="text-ink">{String(f.actual)}</span>
                          </li>
                        ))}
                      </ul>
                    )}
                  </td>

                  {/* select */}
                  <td className="py-4 pr-2">
                    {ok ? (
                      <label className="flex cursor-pointer items-center gap-2">
                        <input
                          type="radio"
                          name="offer-select"
                          value={offer.id}
                          checked={isSelected}
                          onChange={() => onSelect(offer.id)}
                          disabled={submitting}
                          className="accent-black focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal"
                          aria-label={`Select ${offer.title}`}
                        />
                        <span className="sr-only">Select</span>
                      </label>
                    ) : (
                      <span
                        title={evaluation.explanation}
                        className="font-mono text-[10px] uppercase tracking-[0.18em] text-rule"
                      >
                        n/a
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* evaluator explanations */}
      <Rule className="my-6" />
      <div className="grid gap-4 md:grid-cols-2">
        {sorted.slice(0, 4).map(({ offer, evaluation }) => (
          <div key={`exp-${offer.id}`}>
            <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink-soft">
              {evaluation.feasible ? "Selected rationale" : "Rejection note"} · {offer.sku}
            </div>
            <p className="mt-1 font-body text-[13px] leading-relaxed text-ink-soft">
              {evaluation.explanation || "—"}
            </p>
          </div>
        ))}
      </div>

      {selectedOfferId && (
        <div className="mt-6 flex items-center gap-4">
          <Button onClick={() => onSelect(selectedOfferId)} disabled={submitting}>
            {submitting ? "Freezing promises…" : "Freeze & open contract"}
          </Button>
          <span className="font-body text-[13px] text-ink-soft">
            Freezes this offer's exact promises before any money moves.
          </span>
        </div>
      )}

      {results.length === 0 && (
        <p className="py-8 text-center font-body text-sm italic text-ink-soft">
          No offers returned by the merchant.
        </p>
      )}
    </div>
  );
}
