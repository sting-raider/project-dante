"use client";

/**
 * SELECTED OFFER panel (§28 §2-3) — the frozen offer identity plus the
 * "why selected" explanation list derived from the evaluator's soft scores.
 */

import type { MerchantOffer, SoftScore } from "@/lib/useContractFlow";
import { rupees } from "@/lib/useContractFlow";
import { Badge, Panel, Rule, SectionLabel } from "./atoms";

function deliveryText(offer: MerchantOffer): string {
  const dp = offer.delivery_promise;
  if (dp.promised_by_date) {
    const d = new Date(dp.promised_by_date);
    return Number.isNaN(d.getTime())
      ? dp.promised_by_date
      : d.toLocaleDateString("en-IN", {
          weekday: "short",
          day: "numeric",
          month: "short",
        });
  }
  if (dp.max_days != null) return `${dp.min_days ?? "?"}–${dp.max_days} days`;
  return "—";
}

export function SelectedOfferPanel({
  offer,
  explanation,
  softScores,
}: {
  offer: MerchantOffer | null;
  explanation?: string | null;
  softScores?: SoftScore[];
}) {
  return (
    <Panel tone="bright">
      <SectionLabel index="§2">Selected offer</SectionLabel>

      {!offer ? (
        <p className="mt-4 font-body text-sm italic text-ink-soft">
          Offer details unavailable (offer snapshot not returned by API).
        </p>
      ) : (
        <>
          <h3 className="mt-3 font-display text-3xl leading-tight text-ink">
            {offer.title}
          </h3>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
            <Badge tone="neutral">{offer.brand ?? "aster"}</Badge>
            <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink-soft">
              sku {offer.sku}
              {offer.category ? ` · ${offer.category}` : ""}
            </span>
          </div>

          <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
            <div>
              <dt className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-soft">
                Price
              </dt>
              <dd className="mt-0.5 font-body text-sm font-semibold tabular-nums text-ink">
                {rupees(offer.unit_amount_paise)}
              </dd>
            </div>
            <div>
              <dt className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-soft">
                Delivery
              </dt>
              <dd className="mt-0.5 font-body text-sm text-ink">
                {deliveryText(offer)}
              </dd>
            </div>
            <div>
              <dt className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-soft">
                Warranty
              </dt>
              <dd className="mt-0.5 font-body text-sm capitalize text-ink">
                {offer.terms.warranty_type}
                {offer.terms.warranty_duration_months
                  ? ` · ${offer.terms.warranty_duration_months}mo`
                  : ""}
                {offer.terms.warranty_region ? ` · ${offer.terms.warranty_region}` : ""}
              </dd>
            </div>
            <div>
              <dt className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-soft">
                Returns
              </dt>
              <dd className="mt-0.5 font-body text-sm text-ink">
                {offer.terms.return_window_days != null
                  ? `${offer.terms.return_window_days} days`
                  : "—"}
              </dd>
            </div>
          </dl>

          {/* WHY SELECTED */}
          <Rule className="my-4" />
          <SectionLabel index="§3">Why selected</SectionLabel>
          <ul className="mt-3 space-y-1.5">
            {explanation && (
              <li className="font-body text-[13px] leading-relaxed text-ink">
                {explanation}
              </li>
            )}
            {(softScores ?? [])
              .slice()
              .sort((a, b) => b.weight * b.score - a.weight * a.score)
              .map((s) => (
                <li key={s.key} className="flex items-baseline gap-2">
                  <span className="font-mono text-[11px] text-success" aria-hidden="true">
                    +
                  </span>
                  <span className="font-body text-[13px] leading-snug text-ink-soft">
                    <span className="text-ink">{s.note || s.key}</span>{" "}
                    <span className="font-mono text-[10px] tabular-nums">
                      (w{s.weight.toFixed(2)} × {s.score.toFixed(2)})
                    </span>
                  </span>
                </li>
              ))}
          </ul>

          {offer.variant && Object.keys(offer.variant).length > 0 && (
            <>
              <Rule className="my-4" />
              <div className="flex flex-wrap gap-x-5 gap-y-1">
                {Object.entries(offer.variant).map(([k, v]) => (
                  <span key={k} className="font-mono text-[11px] text-ink-soft">
                    {k}: <span className="text-ink">{v}</span>
                  </span>
                ))}
              </div>
            </>
          )}
        </>
      )}
    </Panel>
  );
}
