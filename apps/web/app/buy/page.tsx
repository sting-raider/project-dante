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

export default function BuyPage() {
  const router = useRouter();
  const flow = useContractFlow();
  const [brief, setBrief] = useState(HERO_BRIEF);
  const [pendingSelect, setPendingSelect] = useState(false);
  const busy = flow.isBusy || pendingSelect;
  const isBundle = flow.searchItems.length > 0;

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
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="folio-label text-action-deep">Buyer desk</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-[-0.045em] text-ink md:text-4xl">Create a purchase brief</h1>
          <p className="mt-2 text-sm text-ink-soft">Describe one item or an entire basket. Dante will keep every line accountable.</p>
        </div>
        <Dateline>Aster Electronics · buyer workspace</Dateline>
      </header>
      <Rule className="mt-6" />

      <div className="mt-8 grid gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(21rem,0.7fr)]">
        <section className="rounded-xl border border-rule bg-paper-bright p-6 shadow-[0_8px_28px_rgba(16,24,40,0.04)] md:p-8">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <SectionLabel index="01">Your buying brief</SectionLabel>
            <Badge tone="signal">Intent-bound</Badge>
          </div>
          <label htmlFor="brief" className="sr-only">Your buying brief, in your own words</label>
          <textarea
            id="brief"
            value={brief}
            onChange={(event) => setBrief(event.target.value)}
            rows={5}
            disabled={busy}
            className="mt-6 min-h-48 w-full resize-y rounded-lg border border-rule bg-paper px-4 py-4 text-lg leading-relaxed text-ink caret-action outline-none transition-shadow placeholder:text-ink-soft/60 focus:border-action focus:ring-4 focus:ring-action/10 md:text-xl"
            placeholder="Tell Dante what you need…"
          />
          <div className="mt-5 flex flex-wrap items-center gap-3">
            <Button onClick={() => flow.compileAndSearch(brief)} disabled={busy || !brief.trim()}>
              {flow.phase === "compiling" ? "Compiling…" : flow.phase === "searching" ? "Searching…" : "Compile intent"}
            </Button>
            <span className="text-sm text-ink-soft">Typed constraints appear before any product is shown.</span>
          </div>
        </section>

        <aside className="rounded-xl border border-rule bg-[#101828] p-6 text-white md:p-7">
          <div className="flex items-center justify-between gap-3"><span className="font-mono text-[10px] uppercase tracking-[0.18em] text-white/55">Dante workflow</span><span className="h-2 w-2 rounded-full bg-[#32d583]" aria-label="Ready" /></div>
          <h2 className="mt-8 text-2xl font-semibold tracking-[-0.035em]">A checked basket, not a blind search.</h2>
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
          <p className="mt-8 border-t border-white/15 pt-4 font-mono text-[10px] uppercase tracking-[0.13em] text-white/45">Hard constraints never get relaxed by a recommendation.</p>
        </aside>
      </div>

      <div className="mt-6"><BuyingBrief intent={flow.intent} /></div>

      {flow.phase === "idle" && <div className="mt-6 hidden md:block"><ActivityTicker phase={flow.phase} /></div>}
      {flow.phase !== "idle" && <div className="mt-6"><ActivityTicker phase={flow.phase} error={flow.error} onRetry={["error_compile", "error_search", "error_select"].includes(flow.phase) ? () => flow.compileAndSearch(brief) : undefined} /></div>}

      {flow.phase === "error_select" && (
        <div className="mt-6 rounded-lg border border-danger/30 bg-danger/[0.04] p-4"><p className="text-sm text-danger">{flow.error}</p><div className="mt-3"><Button variant="secondary" onClick={flow.resetError}>Back to offers</Button></div></div>
      )}

      {(flow.results.length > 0 || isBundle) && (
        <section className="mt-10" aria-label="Offer comparison">
          <Rule />
          <div className="mt-7"><SectionLabel index="02">Offer comparison</SectionLabel><div className="mt-5"><OfferSpread
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
          /></div></div>
        </section>
      )}

      {flow.phase === "shortlist" && flow.results.length === 0 && !isBundle && (
        <Panel tone="bright" className="mt-10 text-center"><p className="text-sm italic text-ink-soft">The merchant returned nothing for this brief. Loosen a constraint and compile again.</p></Panel>
      )}
    </main>
  );
}
