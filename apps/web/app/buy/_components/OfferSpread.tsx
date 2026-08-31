"use client";

/**
 * Candidate shelf and bundle editor. A single-item brief keeps the compact
 * shelf view; a multi-item brief gets one editable line card per request and
 * one final aggregate freeze action. Rejected rows stay visible with exact
 * hard-constraint failures so the buyer can edit the brief deliberately.
 */

import type {
  BundleRecommendation,
  MerchantOffer,
  SearchItemGroup,
  SearchResult,
} from "@/lib/useContractFlow";
import { rupees } from "@/lib/useContractFlow";
import { Badge, Button, ConstraintMark, Rule } from "./atoms";

function deliveryLabel(o: MerchantOffer): string {
  const dp = o.delivery_promise;
  if (dp.promised_by_date) {
    const d = new Date(dp.promised_by_date);
    return Number.isNaN(d.getTime())
      ? dp.promised_by_date
      : d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
  }
  if (dp.max_days != null) return `${dp.min_days ?? "?"}–${dp.max_days} days`;
  return "—";
}

function warrantyLabel(o: MerchantOffer): string {
  const t = o.terms;
  if (t.warranty_type === "none") return "none";
  if (t.warranty_type === "unknown") return "unknown";
  const parts: string[] = [t.warranty_type];
  if (t.warranty_duration_months) parts.push(`${t.warranty_duration_months}mo`);
  if (t.warranty_region) parts.push(t.warranty_region);
  return parts.join(" · ");
}

function scoreOf(result: SearchResult): number {
  return result.evaluation.soft_scores.reduce((total, score) => total + score.weight * score.score, 0);
}

function sortedResults(results: SearchResult[]): SearchResult[] {
  return [
    ...results.filter((result) => result.evaluation.feasible).sort((a, b) => scoreOf(b) - scoreOf(a)),
    ...results.filter((result) => !result.evaluation.feasible),
  ];
}

function FailureList({ result }: { result: SearchResult }) {
  return (
    <ul className="mt-3 space-y-1 border-l-2 border-danger/30 pl-3">
      {result.evaluation.hard_failures.map((failure, index) => (
        <li key={`${failure.key}-${index}`} className="font-mono text-[10px] leading-relaxed text-danger">
          ✕ {failure.key}: expected <span className="text-ink">{String(failure.expected)}</span>, got <span className="text-ink">{String(failure.actual)}</span>
        </li>
      ))}
    </ul>
  );
}

function OfferLine({
  result,
  itemId,
  selected,
  recommended,
  submitting,
  onSelect,
}: {
  result: SearchResult;
  itemId?: string;
  selected: boolean;
  recommended?: boolean;
  submitting: boolean;
  onSelect: (offerId: string) => void;
}) {
  const { offer, evaluation } = result;
  const feasible = evaluation.feasible;
  return (
    <div className={`bundle-offer-row ${selected ? "is-selected" : ""} ${!feasible ? "is-rejected" : ""}`}>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          {feasible ? (
            <label className="flex min-w-0 cursor-pointer items-start gap-3">
              <input
                type="radio"
                name={itemId ? `offer-select-${itemId}` : "offer-select"}
                value={offer.id}
                checked={selected}
                onChange={() => onSelect(offer.id)}
                disabled={submitting}
                className="mt-1 h-4 w-4 accent-[var(--color-action)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal"
                aria-label={`Select ${offer.title}`}
              />
              <span className="min-w-0">
                <span className="block text-sm font-semibold leading-snug text-ink">{offer.title}</span>
                <span className="mt-1 block font-mono text-[10px] uppercase tracking-[0.12em] text-ink-soft">
                  {offer.brand ?? "Aster"} · SKU {offer.sku}
                </span>
              </span>
            </label>
          ) : (
            <div className="min-w-0 pl-7">
              <span className="block text-sm font-semibold leading-snug text-ink">{offer.title}</span>
              <span className="mt-1 block font-mono text-[10px] uppercase tracking-[0.12em] text-ink-soft">{offer.brand ?? "Aster"} · SKU {offer.sku} · rejected</span>
            </div>
          )}
          {recommended && <Badge tone="signal">Dante pick</Badge>}
        </div>

        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 pl-7 font-mono text-[10px] uppercase tracking-[0.08em] text-ink-soft">
          <span>Delivery <strong className="font-medium normal-case tracking-normal text-ink">{deliveryLabel(offer)}</strong></span>
          <span>Warranty <strong className="font-medium normal-case tracking-normal text-ink">{warrantyLabel(offer)}</strong></span>
          <span>Inventory <strong className="font-medium normal-case tracking-normal text-ink">{offer.inventory}</strong></span>
        </div>
        {!feasible && <FailureList result={result} />}
        {feasible && evaluation.explanation && (
          <p className="mt-2 pl-7 text-xs leading-relaxed text-ink-soft">{evaluation.explanation}</p>
        )}
      </div>
      <div className="ml-4 shrink-0 text-right">
        <div className={`font-display text-xl font-semibold tabular-nums ${feasible ? "text-ink" : "text-ink-soft"}`}>{rupees(offer.unit_amount_paise)}</div>
        {feasible ? <ConstraintMark pass>hard constraints hold</ConstraintMark> : <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-danger">not eligible</span>}
      </div>
    </div>
  );
}

function ItemShelf({
  group,
  selectedOfferId,
  onSelect,
  submitting,
}: {
  group: SearchItemGroup;
  selectedOfferId?: string;
  onSelect: (offerId: string) => void;
  submitting: boolean;
}) {
  const results = sortedResults(group.results);
  return (
    <section className="bundle-line-card" aria-label={`${group.label} offers`}>
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-rule pb-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-lg font-semibold tracking-[-0.02em] text-ink">{group.label}</h3>
            <Badge tone={group.feasible_count > 0 ? "success" : "danger"}>{group.feasible_count > 0 ? `${group.feasible_count} eligible` : "no complete match"}</Badge>
          </div>
          <p className="mt-1 font-mono text-[10px] uppercase tracking-[0.13em] text-ink-soft">
            line {group.item_id} · quantity {group.quantity}
          </p>
        </div>
        {group.max_price_paise != null && <div className="text-right"><div className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-soft">Line cap</div><div className="mt-1 font-semibold tabular-nums text-ink">{rupees(group.max_price_paise)} <span className="text-xs font-normal text-ink-soft">/ unit</span></div></div>}
      </div>
      <div className="mt-3 divide-y divide-rule">
        {results.map((result) => <OfferLine key={result.offer.id} result={result} itemId={group.item_id} selected={selectedOfferId === result.offer.id} recommended={group.recommended_offer_id === result.offer.id} submitting={submitting} onSelect={onSelect} />)}
      </div>
      {group.feasible_count === 0 && <p className="mt-4 rounded-md bg-danger/[0.05] px-4 py-3 text-sm leading-relaxed text-danger">Dante will not freeze this line. Edit the brief above to loosen a hard constraint or choose a merchant with the missing capability.</p>}
    </section>
  );
}

export function OfferSpread({
  results,
  itemGroups = [],
  bundleRecommendation,
  selectedOfferId,
  selectedOfferIds = {},
  totalCapPaise,
  selectionTotalPaise = 0,
  selectionComplete = false,
  selectionWithinBudget = true,
  onSelect,
  onSelectItem,
  onUseRecommendation,
  onConfirm,
  onConfirmItems,
  submitting,
}: {
  results: SearchResult[];
  itemGroups?: SearchItemGroup[];
  bundleRecommendation?: BundleRecommendation | null;
  selectedOfferId: string | null;
  selectedOfferIds?: Record<string, string>;
  totalCapPaise?: number | null;
  selectionTotalPaise?: number;
  selectionComplete?: boolean;
  selectionWithinBudget?: boolean;
  onSelect: (offerId: string) => void;
  onSelectItem?: (itemId: string, offerId: string) => void;
  onUseRecommendation?: () => void;
  onConfirm: (offerId: string) => void;
  onConfirmItems?: () => void;
  submitting: boolean;
}) {
  const isBundle = itemGroups.length > 0;
  const feasible = results.filter((result) => result.evaluation.feasible);
  const infeasible = results.filter((result) => !result.evaluation.feasible);

  if (isBundle) {
    const canConfirm = selectionComplete && selectionWithinBudget && !!onConfirmItems;
    return (
      <div>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h3 className="text-2xl font-semibold tracking-[-0.035em] text-ink">Build your bundle</h3>
            <p className="mt-1 text-sm text-ink-soft">Choose one eligible offer for every line. The final action freezes one aggregate Promise Ledger.</p>
          </div>
          <span className="font-mono text-[10px] uppercase tracking-[0.17em] text-ink-soft">{itemGroups.length} lines · {itemGroups.reduce((sum, group) => sum + group.results.length, 0)} candidates</span>
        </div>

        {bundleRecommendation?.available ? (
          <div className="mt-5 flex flex-wrap items-center justify-between gap-4 rounded-lg border border-action/25 bg-action-soft p-4">
            <div>
              <div className="flex items-center gap-2"><Badge tone="signal">Dante recommendation</Badge><span className="font-mono text-[10px] uppercase tracking-[0.12em] text-action-deep">hard-feasible mix</span></div>
              <p className="mt-2 text-sm leading-relaxed text-ink">{bundleRecommendation.reason}</p>
            </div>
            <div className="flex items-center gap-4"><div className="text-right"><div className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-soft">Recommended total</div><div className="mt-1 text-xl font-semibold tabular-nums text-ink">{rupees(bundleRecommendation.total_amount_paise)}</div></div><Button onClick={onUseRecommendation} disabled={submitting || !onUseRecommendation}>Use recommended bundle</Button></div>
          </div>
        ) : (
          <div className="mt-5 rounded-lg border border-warning/30 bg-warning/[0.06] p-4">
            <div className="font-mono text-[10px] uppercase tracking-[0.16em] text-warning">No complete recommendation</div>
            <p className="mt-2 text-sm leading-relaxed text-ink-soft">{bundleRecommendation?.reason ?? "Dante found no complete bundle that satisfies every requested line."} Edit the brief to try again.</p>
          </div>
        )}

        <div className="mt-6 space-y-4">
          {itemGroups.map((group) => <ItemShelf key={group.item_id} group={group} selectedOfferId={selectedOfferIds[group.item_id]} onSelect={(offerId) => onSelectItem?.(group.item_id, offerId)} submitting={submitting} />)}
        </div>

        <div className="mt-6 rounded-lg border border-rule bg-paper-bright p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><div className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-soft">Bundle total</div><div className={`mt-1 text-2xl font-semibold tabular-nums ${selectionWithinBudget ? "text-ink" : "text-danger"}`}>{selectionTotalPaise > 0 ? rupees(selectionTotalPaise) : "Choose every line"}</div></div>
            {totalCapPaise != null && <div className="text-right font-mono text-[10px] uppercase tracking-[0.13em] text-ink-soft">Budget cap <span className="ml-2 text-sm font-semibold tracking-normal text-ink">{rupees(totalCapPaise)}</span></div>}
          </div>
          {!selectionWithinBudget && <p className="mt-2 text-sm text-danger">This combination exceeds the total budget. Choose a lower-priced eligible offer.</p>}
          <div className="mt-4 flex flex-wrap items-center gap-4">
            <Button onClick={onConfirmItems} disabled={!canConfirm || submitting}>{submitting ? "Freezing promises…" : "Freeze & open contract"}</Button>
            <span className="max-w-xl text-sm leading-relaxed text-ink-soft">{selectionComplete ? "Every line is selected. You can still swap any offer above before freezing." : "Select one eligible offer for each line to continue."}</span>
          </div>
        </div>
      </div>
    );
  }

  const sorted = sortedResults(results);
  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-2"><h3 className="text-2xl font-semibold tracking-[-0.035em] text-ink">The merchant shelf</h3><span className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink-soft">{feasible.length} eligible · {infeasible.length} rejected</span></div>
      <div className="mt-5 divide-y divide-rule overflow-hidden rounded-lg border border-rule bg-paper-bright">{sorted.map((result) => <OfferLine key={result.offer.id} result={result} selected={selectedOfferId === result.offer.id} submitting={submitting} onSelect={onSelect} />)}</div>
      <Rule className="my-6" />
      <div className="grid gap-4 md:grid-cols-2">{sorted.slice(0, 4).map(({ offer, evaluation }) => <div key={`exp-${offer.id}`}><div className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-soft">{evaluation.feasible ? "Selected rationale" : "Rejection note"} · {offer.sku}</div><p className="mt-1 text-[13px] leading-relaxed text-ink-soft">{evaluation.explanation || "—"}</p></div>)}</div>
      {selectedOfferId && <div className="mt-6 flex flex-wrap items-center gap-4"><Button onClick={() => onConfirm(selectedOfferId)} disabled={submitting}>{submitting ? "Freezing promises…" : "Freeze & open contract"}</Button><span className="max-w-md text-sm leading-snug text-ink-soft">Freezes this offer&apos;s exact promises before any money moves.</span></div>}
      {results.length === 0 && <p className="py-8 text-center text-sm italic text-ink-soft">No offers returned by the merchant.</p>}
    </div>
  );
}
