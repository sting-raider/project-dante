"use client";

/**
 * Buyer desk — intent first, then an editable single offer or bundle. The
 * server remains the authority for hard constraints, frozen promises and the
 * aggregate amount; this page only holds temporary selection state.
 */

import { useRouter } from "next/navigation";
import { useState } from "react";
import { rememberBuyerBrief, rememberOfferSelection, useContractFlow } from "@/lib/useContractFlow";
import { ActivityTicker } from "./_components/ActivityTicker";
import { BuyingBrief } from "./_components/BuyingBrief";
import { OfferSpread } from "./_components/OfferSpread";
import { Badge, Button, Dateline, Panel, Rule, SectionLabel } from "./_components/atoms";

const HERO_BRIEF =
  "Buy me over-ear ANC headphones under ₹12,000. I need an Indian manufacturer warranty, as they must arrive within 3 days, and do not show me anything over ₹12,000.";

function DeskMetric({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="buy-metric-card">
      <span className="buy-metric-label">{label}</span>
      <strong className="buy-metric-value">{value}</strong>
      <span className="buy-metric-detail">{detail}</span>
    </div>
  );
}

export default function BuyPage() {
  const router = useRouter();
  const flow = useContractFlow();
  const [brief, setBrief] = useState(HERO_BRIEF);
  const [pendingSelect, setPendingSelect] = useState(false);
  const busy = flow.isBusy || pendingSelect;
  const isBundle = flow.searchItems.length > 0;
  const parsedItemCount = flow.intent
    ? Math.max(flow.intent.items.length, 1)
    : 0;
  const parsedConstraintCount = flow.intent
    ? flow.intent.hard_constraints.filter((constraint) => constraint.critical).length +
      flow.intent.items.reduce(
        (total, item) =>
          total + item.hard_constraints.filter((constraint) => constraint.critical).length,
        0,
      )
    : 0;
  const briefStatus =
    flow.phase === "idle"
      ? "Awaiting brief"
      : flow.phase === "compiling" || flow.phase === "searching"
        ? "Compiling"
        : flow.phase.startsWith("error")
          ? "Needs attention"
          : flow.results.length > 0 || isBundle
            ? "Offers ready"
            : "Constraints ready";

  function handleChoose(offerId: string) {
    flow.chooseOffer(offerId);
  }

  async function handleConfirm(offerId: string) {
    setPendingSelect(true);
    const result = flow.results.find((candidate) => candidate.offer.id === offerId);
    rememberBuyerBrief(brief);
    const contractId = await flow.selectOffer(offerId);
    if (contractId) {
      if (result) {
        rememberOfferSelection(contractId, {
          offer: result.offer,
          explanation: result.evaluation.explanation,
          softScores: result.evaluation.soft_scores,
        });
      }
      router.push(`/contract/${contractId}`);
    } else {
      setPendingSelect(false);
    }
  }

  async function handleConfirmBundle() {
    setPendingSelect(true);
    rememberBuyerBrief(brief);
    const contractId = await flow.selectOffers();
    if (contractId) {
      const items = flow.searchItems.flatMap((group) => {
        const selected = group.results.find(
          (candidate) => candidate.offer.id === flow.selectedOfferIds[group.item_id],
        );
        return selected
          ? [{
              item_id: group.item_id,
              label: group.label,
              quantity: group.quantity,
              offer: selected.offer,
              explanation: selected.evaluation.explanation,
              softScores: selected.evaluation.soft_scores,
            }]
          : [];
      });
      if (items[0]) {
        rememberOfferSelection(contractId, { offer: items[0].offer, items });
      }
      router.push(`/contract/${contractId}`);
    } else {
      setPendingSelect(false);
    }
  }

  return (
    <main className="dante-container pb-24 pt-8 md:pt-10">
      <header className="buy-page-header">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <p className="folio-label text-action-deep">Buyer desk / issue 01</p>
            <Badge tone="success">Buyer-owned runtime</Badge>
          </div>
          <h1 className="mt-3 text-4xl font-semibold tracking-[-0.055em] text-ink md:text-6xl">Create a purchase brief</h1>
          <p className="mt-4 max-w-2xl text-base leading-relaxed text-ink-soft md:text-lg">Describe one item or an entire basket. Dante turns the brief into visible constraints, checks each offer, and keeps every line accountable.</p>
        </div>
        <div className="buy-header-note">
          <Dateline>Aster Electronics · 112 structured SKUs</Dateline>
          <p className="mt-3 text-sm leading-relaxed text-ink-soft">Authorization stays yours. No order or payment is created while you are still comparing.</p>
        </div>
      </header>
      <Rule className="mt-6" />

      <div className="buy-metric-grid" aria-label="Buyer desk status">
        <DeskMetric
          label="Brief status"
          value={briefStatus}
          detail={flow.error ? "Review the message below" : "Typed input is the source of truth"}
        />
        <DeskMetric
          label="Requested shape"
          value={parsedItemCount ? `${parsedItemCount} ${parsedItemCount === 1 ? "line" : "lines"}` : "Not compiled"}
          detail={parsedConstraintCount ? `${parsedConstraintCount} hard checks parsed` : "Compile to reveal constraints"}
        />
        <DeskMetric
          label="Total guardrail"
          value={flow.intent?.max_total_amount_paise != null ? `₹${(flow.intent.max_total_amount_paise / 100).toLocaleString("en-IN")}` : "Open budget"}
          detail={flow.intent?.max_total_amount_paise != null ? "Applied across the basket" : "No shared cap stated"}
        />
        <DeskMetric
          label="Payment rail"
          value="Protected"
          detail="Authorization follows the frozen contract"
        />
      </div>

      <div className="mt-8 grid gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(21rem,0.7fr)]">
        <section className="buy-compose-card">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <SectionLabel index="01">Your buying brief</SectionLabel>
            <Badge tone="signal">Intent-bound</Badge>
          </div>
          <label htmlFor="brief" className="sr-only">Your buying brief, in your own words</label>
          <div className="buy-brief-input-wrap">
            <span className="buy-brief-index" aria-hidden="true">01</span>
            <textarea
              id="brief"
              value={brief}
              onChange={(event) => setBrief(event.target.value)}
              rows={5}
              disabled={busy}
              aria-describedby="brief-guidance brief-count"
              className="buy-brief-input"
              placeholder="Tell Dante what you need…"
            />
          </div>
          <div className="buy-compose-footer">
            <div>
              <p id="brief-guidance" className="text-sm leading-relaxed text-ink-soft">Change a quantity or constraint here, then compile again. The server re-evaluates the whole brief before any offer can be selected.</p>
              <p id="brief-count" className="mt-2 font-mono text-[10px] uppercase tracking-[0.15em] text-ink-soft">{brief.length.toLocaleString("en-IN")} characters · no payment action</p>
            </div>
            <Button onClick={() => flow.compileAndSearch(brief)} disabled={busy || !brief.trim()}>
              {flow.phase === "compiling" ? "Compiling…" : flow.phase === "searching" ? "Searching…" : "Compile intent"}
            </Button>
          </div>
        </section>

        <aside className="buy-workflow-card">
          <div className="buy-workflow-glow" aria-hidden="true" />
          <div className="relative flex items-center justify-between gap-3"><span className="font-mono text-[10px] uppercase tracking-[0.18em] text-white/55">Dante workflow</span><span className="buy-workflow-dot" aria-label="Runtime ready" /></div>
          <h2 className="relative mt-8 text-2xl font-semibold tracking-[-0.035em]">A checked basket, not a blind search.</h2>
          <ol className="mt-6 space-y-4">
            {[
              ["Compile", "Turn your words into visible hard constraints."],
              ["Compare", "See eligible and rejected offers with reasons."],
              ["Freeze", "Lock the exact promises before authorization."],
            ].map(([title, body], index) => (
              <li key={title} className="flex gap-3 border-t border-white/15 pt-3">
                <span className="font-mono text-[10px] text-[#98b8ff]">0{index + 1}</span>
                <div><div className="text-sm font-semibold">{title}</div><p className="mt-1 text-sm leading-relaxed text-white/60">{body}</p></div>
              </li>
            ))}
          </ol>
          <p className="relative mt-8 border-t border-white/15 pt-4 font-mono text-[10px] uppercase tracking-[0.13em] text-white/45">Hard constraints never get relaxed by a recommendation.</p>
        </aside>
      </div>

      <div className="buy-brief-result mt-6"><BuyingBrief intent={flow.intent} /></div>

      {flow.phase === "idle" && <div className="mt-6"><ActivityTicker phase={flow.phase} /></div>}
      {flow.phase !== "idle" && <div className="mt-6"><ActivityTicker phase={flow.phase} error={flow.error} onRetry={["error_compile", "error_search", "error_select"].includes(flow.phase) ? () => flow.compileAndSearch(brief) : undefined} /></div>}

      {flow.phase === "error_select" && (
        <div className="mt-6 rounded-lg border border-danger/30 bg-danger/[0.04] p-4"><p className="text-sm text-danger">{flow.error}</p><div className="mt-3"><Button variant="secondary" onClick={flow.resetError}>Back to offers</Button></div></div>
      )}

      {(flow.results.length > 0 || isBundle) && (
        <section className="buy-results-section mt-10" aria-label="Offer comparison">
          <Rule />
          <div className="mt-7 flex flex-wrap items-end justify-between gap-4">
            <div>
              <SectionLabel index="02">Offer comparison</SectionLabel>
              <p className="mt-3 max-w-2xl text-sm leading-relaxed text-ink-soft">Only server-confirmed hard-feasible candidates can be selected. Swap offers here; edit the brief above if the requirements themselves need to change.</p>
            </div>
            <div className="buy-results-meta" aria-label="Comparison summary">
              <span>{isBundle ? `${flow.searchItems.length} requested lines` : `${flow.results.filter((result) => result.evaluation.feasible).length} eligible offers`}</span>
              <span aria-hidden="true">·</span>
              <span>{flow.intent?.max_total_amount_paise != null ? `cap ₹${(flow.intent.max_total_amount_paise / 100).toLocaleString("en-IN")}` : "no shared cap"}</span>
            </div>
          </div>
          <div className="mt-6"><OfferSpread
            results={flow.results}
            itemGroups={flow.searchItems}
            bundleRecommendation={flow.bundleRecommendation}
            selectedOfferId={flow.selectedOfferId}
            selectedOfferIds={flow.selectedOfferIds}
            totalCapPaise={flow.intent?.max_total_amount_paise}
            selectionTotalPaise={flow.selectionTotalPaise}
            selectionComplete={flow.selectionComplete}
            selectionWithinBudget={flow.selectionWithinBudget}
            onSelect={handleChoose}
            onSelectItem={flow.chooseItemOffer}
            onUseRecommendation={flow.chooseRecommendedBundle}
            onConfirm={handleConfirm}
            onConfirmItems={handleConfirmBundle}
            submitting={pendingSelect}
          /></div>
        </section>
      )}

      {flow.phase === "shortlist" && flow.results.length === 0 && !isBundle && (
        <Panel tone="bright" className="buy-empty-state mt-10 text-center"><p className="text-sm italic text-ink-soft">The merchant returned nothing for this brief. Loosen a constraint and compile again.</p></Panel>
      )}
    </main>
  );
}
