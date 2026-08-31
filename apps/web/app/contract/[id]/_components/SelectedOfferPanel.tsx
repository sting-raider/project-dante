"use client";

/** Frozen offer/bundle identity shown on the purchase dossier. */

import type {
  DanteContract,
  MerchantOffer,
  OfferMemoItem,
  SoftScore,
} from "@/lib/useContractFlow";
import { rupees } from "@/lib/useContractFlow";
import { Badge, Panel, Rule, SectionLabel } from "./atoms";

function deliveryText(offer: MerchantOffer): string {
  const dp = offer.delivery_promise;
  if (dp.promised_by_date) {
    const d = new Date(dp.promised_by_date);
    return Number.isNaN(d.getTime()) ? dp.promised_by_date : d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
  }
  if (dp.max_days != null) return `${dp.min_days ?? "?"}–${dp.max_days} days`;
  return "—";
}

function WhySelected({ explanation, softScores }: { explanation?: string | null; softScores?: SoftScore[] }) {
  return (
    <>
      <Rule className="my-4" />
      <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-soft">Why this line</div>
      <ul className="mt-3 space-y-1.5">
        {explanation && <li className="text-[13px] leading-relaxed text-ink">{explanation}</li>}
        {(softScores ?? []).slice().sort((a, b) => b.weight * b.score - a.weight * a.score).map((score) => (
          <li key={score.key} className="flex items-baseline gap-2 text-[13px] text-ink-soft"><span className="font-mono text-success" aria-hidden="true">+</span><span>{score.note || score.key} <span className="font-mono text-[10px] tabular-nums">(w{score.weight.toFixed(2)} × {score.score.toFixed(2)})</span></span></li>
        ))}
      </ul>
    </>
  );
}

function OfferDetails({ offer, quantity = 1, explanation, softScores }: { offer: MerchantOffer; quantity?: number; explanation?: string | null; softScores?: SoftScore[] }) {
  const amount = offer.unit_amount_paise * Math.max(1, quantity);
  return (
    <article className="rounded-lg border border-rule bg-paper p-4 md:p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2"><Badge tone="success">Promise frozen</Badge>{quantity > 1 && <Badge>{`qty ${quantity}`}</Badge>}</div>
          <h3 className="mt-3 text-xl font-semibold leading-tight tracking-[-0.025em] text-ink">{offer.title}</h3>
          <div className="mt-1 font-mono text-[10px] uppercase tracking-[0.13em] text-ink-soft">{offer.brand ?? "Aster"} · SKU {offer.sku}{offer.category ? ` · ${offer.category}` : ""}</div>
        </div>
        <div className="text-right"><div className="text-xl font-semibold tabular-nums text-ink">{rupees(amount)}</div>{quantity > 1 && <div className="mt-1 font-mono text-[10px] text-ink-soft">{rupees(offer.unit_amount_paise)} / unit</div>}</div>
      </div>
      <dl className="mt-5 grid grid-cols-2 gap-x-5 gap-y-4 sm:grid-cols-4">
        <div><dt className="folio-label">Delivery</dt><dd className="mt-1 text-sm text-ink">{deliveryText(offer)}</dd></div>
        <div><dt className="folio-label">Warranty</dt><dd className="mt-1 text-sm capitalize text-ink">{offer.terms.warranty_type}{offer.terms.warranty_duration_months ? ` · ${offer.terms.warranty_duration_months}mo` : ""}{offer.terms.warranty_region ? ` · ${offer.terms.warranty_region}` : ""}</dd></div>
        <div><dt className="folio-label">Returns</dt><dd className="mt-1 text-sm text-ink">{offer.terms.return_window_days != null ? `${offer.terms.return_window_days} days` : "—"}</dd></div>
        <div><dt className="folio-label">Line total</dt><dd className="mt-1 text-sm font-semibold tabular-nums text-ink">{rupees(amount)}</dd></div>
      </dl>
      <WhySelected explanation={explanation} softScores={softScores} />
      {offer.variant && Object.keys(offer.variant).length > 0 && <><Rule className="my-4" /><div className="flex flex-wrap gap-x-5 gap-y-1 font-mono text-[11px] text-ink-soft">{Object.entries(offer.variant).map(([key, value]) => <span key={key}>{key}: <span className="text-ink">{value}</span></span>)}</div></>}
    </article>
  );
}

function FallbackLine({ line }: { line: NonNullable<DanteContract["line_items"]>[number] }) {
  return <article className="rounded-lg border border-rule bg-paper p-4 md:p-5"><div className="flex flex-wrap items-start justify-between gap-4"><div><Badge tone="success">Promise frozen</Badge><h3 className="mt-3 text-xl font-semibold leading-tight tracking-[-0.025em] text-ink">{line.title}</h3><p className="mt-1 font-mono text-[10px] uppercase tracking-[0.13em] text-ink-soft">SKU {line.sku} · line {line.id}</p></div><div className="text-xl font-semibold tabular-nums text-ink">{rupees(line.amount_paise)}</div></div><div className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-3"><div><div className="folio-label">Quantity</div><div className="mt-1 text-sm text-ink">{line.quantity}</div></div><div><div className="folio-label">Unit price</div><div className="mt-1 text-sm tabular-nums text-ink">{rupees(line.unit_amount_paise)}</div></div><div><div className="folio-label">Offer id</div><div className="mt-1 break-all font-mono text-[11px] text-ink-soft">{line.offer_id}</div></div></div></article>;
}

export function SelectedOfferPanel({
  offer,
  items = [],
  lineItems = [],
  explanation,
  softScores,
}: {
  offer: MerchantOffer | null;
  items?: OfferMemoItem[];
  lineItems?: NonNullable<DanteContract["line_items"]>;
  explanation?: string | null;
  softScores?: SoftScore[];
}) {
  const isBundle = items.length > 0 || lineItems.length > 1;
  return (
    <Panel tone="bright">
      <div className="flex flex-wrap items-center justify-between gap-3"><SectionLabel index="§2">{isBundle ? "Selected bundle" : "Selected offer"}</SectionLabel>{isBundle && <Badge tone="signal">{`${items.length || lineItems.length} lines`}</Badge>}</div>
      {items.length > 0 ? <div className="mt-5 space-y-3">{items.map((item) => <OfferDetails key={item.item_id} offer={item.offer} quantity={item.quantity} explanation={item.explanation} softScores={item.softScores} />)}</div> : lineItems.length > 0 ? <div className="mt-5 space-y-3">{lineItems.map((line) => <FallbackLine key={line.id} line={line} />)}</div> : offer ? <div className="mt-5"><OfferDetails offer={offer} explanation={explanation} softScores={softScores} /></div> : <p className="mt-4 text-sm italic text-ink-soft">Offer details unavailable (offer snapshot not returned by API).</p>}
    </Panel>
  );
}
