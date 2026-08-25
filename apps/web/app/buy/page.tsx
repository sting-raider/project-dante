"use client";

/**
 * /buy — the buyer's desk (§28).
 *
 * Left: the natural-language brief as a large editorial pull quote.
 * Right: the BUYING BRIEF column of parsed typed constraints (post-compile).
 * Below: agent activity ticker, then the offer comparison spread where
 * infeasible offers stay visible with their exact failure reasons.
 *
 * Selecting a feasible offer freezes its promises into a Dante Contract
 * and navigates to /contract/[id].
 */

import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  rememberBuyerBrief,
  rememberOfferSelection,
  useContractFlow,
} from "@/lib/useContractFlow";
import { ActivityTicker } from "./_components/ActivityTicker";
import { BuyingBrief } from "./_components/BuyingBrief";
import { OfferSpread } from "./_components/OfferSpread";
import {
  Dateline,
  Folio,
  Panel,
  Button,
  Rule,
  SectionLabel,
} from "./_components/atoms";

const HERO_BRIEF =
  "Buy me over-ear ANC headphones under ₹12,000. I need an Indian manufacturer warranty, as they must arrive by Thursday, and do not show me anything over ₹12,000.";

export default function BuyPage() {
  const router = useRouter();
  const flow = useContractFlow();
  const [brief, setBrief] = useState(HERO_BRIEF);
  const [pendingSelect, setPendingSelect] = useState(false);

  const busy = flow.isBusy || pendingSelect;

  async function handleSelect(offerId: string) {
    setPendingSelect(true);
    const result = flow.results.find((r) => r.offer.id === offerId);
    if (result) {
      rememberBuyerBrief(brief);
    }
    const contractId = await flow.selectOffer(offerId);
    if (contractId && result) {
      rememberOfferSelection(contractId, {
        offer: result.offer,
        explanation: result.evaluation.explanation,
        softScores: result.evaluation.soft_scores,
      });
      router.push(`/contract/${contractId}`);
    } else if (contractId) {
      router.push(`/contract/${contractId}`);
    } else {
      setPendingSelect(false);
    }
  }

  return (
    <main className="mx-auto min-h-screen max-w-6xl px-5 pb-24 pt-10 md:px-10">
      {/* masthead */}
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <Folio>Issue 01 / Buy</Folio>
        <Dateline>
          Aster Electronics · {new Date().toLocaleDateString("en-IN", { day: "numeric", month: "long", year: "numeric" })}{" "}
          · Buyer desk
        </Dateline>
      </header>
      <Rule className="mt-4" />

      {/* brief + parsed intent */}
      <div className="mt-10 grid gap-10 md:grid-cols-12">
        <div className="md:col-span-7">
          <SectionLabel index="§1">The buyer's brief</SectionLabel>
          <label htmlFor="brief" className="sr-only">
            Your buying brief, in your own words
          </label>
          <textarea
            id="brief"
            value={brief}
            onChange={(e) => setBrief(e.target.value)}
            rows={5}
            disabled={busy}
            className="mt-5 w-full resize-none border-0 bg-transparent font-display text-[clamp(1.6rem,3.2vw,2.6rem)] leading-[1.25] text-ink caret-signal outline-none placeholder:text-rule focus-visible:outline-none"
            placeholder="Tell Dante what you need…"
          />
          <div className="mt-6 flex flex-wrap items-center gap-4">
            <Button onClick={() => flow.compileAndSearch(brief)} disabled={busy || !brief.trim()}>
              {flow.phase === "compiling"
                ? "Compiling…"
                : flow.phase === "searching"
                  ? "Searching…"
                  : "Compile intent"}
            </Button>
            <span className="font-body text-[13px] text-ink-soft">
              Typed constraints appear before any product is shown.
            </span>
          </div>

          {flow.phase === "idle" && (
            <div className="mt-8 hidden md:block">
              <ActivityTicker phase={flow.phase} />
            </div>
          )}
        </div>

        <aside className="md:col-span-5">
          <BuyingBrief intent={flow.intent} engine={flow.engine} />
        </aside>
      </div>

      {/* single activity ticker for all non-idle phases */}
      {flow.phase !== "idle" && (
        <div className="mt-10">
          <ActivityTicker
            phase={flow.phase}
            error={flow.error}
            onRetry={
              ["error_compile", "error_search", "error_select"].includes(flow.phase)
                ? () => flow.compileAndSearch(brief)
                : undefined
            }
          />
        </div>
      )}

      {/* error banner for select failures */}
      {flow.phase === "error_select" && (
        <div className="mt-8 rounded-[2px] border border-danger/40 bg-paper-bright p-4">
          <p className="font-body text-[13px] text-danger">{flow.error}</p>
          <div className="mt-3">
            <Button variant="secondary" onClick={flow.resetError}>
              Back to offers
            </Button>
          </div>
        </div>
      )}

      {/* results */}
      {flow.results.length > 0 && (
        <section className="mt-14" aria-label="Offer comparison">
          <Rule />
          <div className="mt-8">
            <SectionLabel index="§2">Comparison spread</SectionLabel>
            <div className="mt-6">
              <OfferSpread
                results={flow.results}
                selectedOfferId={flow.selectedOfferId}
                onSelect={handleSelect}
                submitting={pendingSelect}
              />
            </div>
          </div>
        </section>
      )}

      {/* empty state after search with no rows at all */}
      {flow.phase === "shortlist" && flow.results.length === 0 && (
        <Panel tone="bright" className="mt-14 text-center">
          <p className="font-body text-sm italic text-ink-soft">
            The merchant returned nothing for this brief. Loosen a constraint and compile again.
          </p>
        </Panel>
      )}
    </main>
    // NOTE for integration: when Agent G's components/editorial + commerce land,
    // swap ./_components/atoms imports for components/editorial + components/commerce.
  );
}
