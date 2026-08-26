"use client";

/**
 * /contract/[id] — the visual centerpiece (§28).
 *
 * Sections: intent recap · selected offer · why selected · material promises
 * · authorization envelope · contract hashes · Razorpay state · rights
 * overview. A sticky bottom bar carries the §52 "YOU ARE ABOUT TO AUTHORIZE"
 * card whenever the contract sits at AWAITING_BUYER_AUTH.
 *
 * Payment paths (per docs/API_CONTRACT.md):
 * - sandbox:      payment-order returns mode "sandbox" → clearly-badged button
 *                 fires POST /api/demo/razorpay/simulate-event, then poll to PAID.
 * - live-test:    load checkout.js, open Standard Checkout, handler posts
 *                 /api/payments/verify-client, then poll to PAID.
 * Client success is never final truth — the webhook is.
 */

import Link from "next/link";
import Script from "next/script";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import type {
  PaymentOrderResponse,
  RazorpayHandlerResponse,
} from "@/lib/useContractFlow";
import {
  BRIEF_SESSION_KEY,
  readOfferSelection,
  rupees,
  shortHash,
  useContractFlow,
  type OfferMemo,
} from "@/lib/useContractFlow";

import type { FlowPhase } from "@/lib/useContractFlow";

import { AuthorizationCard } from "./_components/AuthorizationCard";
import { ContractHashes } from "./_components/ContractHashes";
import { MaterialPromises } from "./_components/MaterialPromises";
import { RazorpayPanel } from "./_components/RazorpayPanel";
import { SelectedOfferPanel } from "./_components/SelectedOfferPanel";
import {
  Badge,
  Button,
  Dateline,
  Folio,
  MarginNote,
  Panel,
  Rule,
  SandboxBadge,
  SectionLabel,
} from "./_components/atoms";

/**
 * Phases meaning "authorization has resolved and the payment pipeline is in
 * motion" — the sticky §52 card must not re-render once any of these hold,
 * even if the 2s poll hasn't yet flipped the contract status (#1).
 */
const POST_AUTHORIZE_PHASES: FlowPhase[] = [
  "opening_checkout",
  "checkout_ready",
  "sandbox_ready",
  "payment_pending",
  "paid",
];

export default function ContractPage() {
  const params = useParams<{ id: string }>();
  const contractId = params?.id ?? null;

  const flow = useContractFlow();
  const [offerMemo, setOfferMemo] = useState<OfferMemo | null>(null);
  const [brief, setBrief] = useState<string | null>(null);
  const [authorizing, setAuthorizing] = useState(false);
  const [simulating, setSimulating] = useState(false);
  const [checkoutOpen, setCheckoutOpen] = useState(false);
  const [rzpScriptReady, setRzpScriptReady] = useState(false);
  const [dismissedNote, setDismissedNote] = useState<string | null>(null);

  // initial load (+ resume polling if payment pending)
  useEffect(() => {
    if (!contractId) return;
    void flow.loadContract(contractId);
    setOfferMemo(readOfferSelection(contractId));
    try {
      setBrief(window.sessionStorage.getItem(BRIEF_SESSION_KEY));
    } catch {
      setBrief(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contractId]);

  const contract = flow.contract;

  const openRazorpayCheckout = useCallback(
    (order: PaymentOrderResponse) => {
      setDismissedNote(null);
      if (!window.Razorpay) {
        setDismissedNote(
          "Razorpay checkout script is still loading — try again in a moment.",
        );
        return;
      }
      const cfg = order.checkout_config;
      const rzp = new window.Razorpay({
        key_id: cfg.key_id,
        amount: cfg.amount_paise,
        currency: cfg.currency ?? "INR",
        name: "ASTER ELECTRONICS",
        description: offerMemo?.offer.title ?? "Dante contract purchase",
        order_id: cfg.order_id,
        prefill: { name: "Demo Buyer" },
        theme: { color: "#F04A2D" },
        handler: (response: RazorpayHandlerResponse) => {
          // client success ≠ truth; verify then keep polling for webhook
          void flow.verifyClient(contractId!, response);
        },
        modal: {
          ondismiss: () => {
            setDismissedNote(
              "Checkout closed before completing. If the payment actually went through, server reconciliation will confirm it — watch the status below.",
            );
            // window-closed fallback (§33.5): resume polling regardless
            flow.pollUntilResolved(contractId!);
          },
        },
      });
      rzp.open();
      setCheckoutOpen(true);
    },
    [contractId, offerMemo, flow],
  );

  async function handleAuthorize() {
    if (!contractId) return;
    // Idempotency guard: authorizeAndOpenCheckout refuses re-entrant calls,
    // and this early-return keeps the button from even starting one.
    if (authorizing || flow.phase === "opening_checkout") return;
    setAuthorizing(true);
    await flow.authorizeAndOpenCheckout(contractId, openRazorpayCheckout);
    setAuthorizing(false);
  }

  function handleSimulate() {
    setSimulating(true);
    void flow.simulateSandboxPayment().finally(() => setSimulating(false));
  }

  // ---------------------------------------------------------- render states

  // Fatal screen only when the contract genuinely can't be loaded (404) —
  // transient poll failures degrade to the inline retrying notice (#2).
  if (flow.phase === "error_contract_load" || flow.phase === "error_poll") {
    return (
      <main className="mx-auto min-h-screen max-w-6xl px-5 pb-24 pt-10 md:px-10">
        <Folio>Issue 01 / Buy</Folio>
        <Rule className="mt-4" />
        <Panel tone="signal" className="mt-10">
          <h1 className="font-display text-3xl text-danger">Contract unavailable</h1>
          <p className="mt-3 font-body text-sm text-ink-soft">{flow.error}</p>
          <div className="mt-5 flex gap-3">
            <Button
              onClick={() => contractId && void flow.loadContract(contractId)}
            >
              Retry
            </Button>
            <Link href="/buy">
              <Button variant="secondary">Back to buyer desk</Button>
            </Link>
          </div>
        </Panel>
      </main>
    );
  }

  if (!contract) {
    return (
      <main className="mx-auto min-h-screen max-w-6xl px-5 pb-24 pt-10 md:px-10">
        <Folio>Issue 01 / Buy</Folio>
        <Rule className="mt-4" />
        <p className="mt-16 animate-pulse font-mono text-[12px] uppercase tracking-[0.25em] text-ink-soft">
          Opening dossier…
        </p>
      </main>
    );
  }

  const paid = contract.status === "PAID";
  const awaitingAuth = contract.status === "AWAITING_BUYER_AUTH";
  const authorized = contract.buyer_authority != null;

  return (
    <main className="mx-auto min-h-screen max-w-6xl px-5 pb-40 pt-10 md:px-10">
      {/* Razorpay checkout.js — lazyOnload; opened on demand post-authorize */}
      {!contract.sandbox_mode && !rzpScriptReady && (
        <Script
          src="https://checkout.razorpay.com/v1/checkout.js"
          strategy="lazyOnload"
          onLoad={() => setRzpScriptReady(true)}
        />
      )}

      {/* masthead */}
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="font-mono text-[10px] uppercase tracking-[0.3em] text-ink-soft">
          Dossier / {contract.display_code ?? contract.id}
        </h1>
        <Dateline>
          Frozen {contract.frozen_at ? new Date(contract.frozen_at).toLocaleString("en-IN") : "—"}
          {contract.sandbox_mode && (
            <span className="ml-3">
              <SandboxBadge />
            </span>
          )}
        </Dateline>
      </header>

      {/* PAID banner */}
      {paid && (
        <div className="mt-6 rounded-[2px] border border-success/50 bg-paper-bright px-5 py-4">
          <span className="font-mono text-[12px] uppercase tracking-[0.22em] text-success">
            Paid — verified by webhook truth
          </span>{" "}
          <Link
            href={`/contract/${contract.id}/timeline`}
            className="ml-2 font-body text-[13px] underline underline-offset-4 hover:text-ink"
          >
            Open the full event timeline →
          </Link>
        </div>
      )}

      {dismissedNote && !paid && (
        <div className="mt-6 rounded-[2px] border border-warning/50 bg-paper-bright px-5 py-3 font-body text-[13px] leading-snug text-warning">
          {dismissedNote}
        </div>
      )}
      {flow.phase === "payment_pending" && !paid && (
        <div className="mt-6 flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.18em] text-warning">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-warning" aria-hidden="true" />
          {flow.verifyNote ?? "confirming against server truth…"}
        </div>
      )}

      {/* Transient poll degradation (#2): keep last-known data on screen, show
          a small retrying notice instead of the fatal full-page error. */}
      {flow.pollRetrying && (
        <div
          role="status"
          className="mt-4 inline-flex max-w-full items-center gap-2 rounded-[2px] border border-rule bg-paper-bright px-4 py-2.5 font-mono text-[11px] uppercase tracking-[0.16em] text-ink-soft"
        >
          <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-warning" aria-hidden="true" />
          Connection hiccup while polling server truth — showing last-known
          state, retrying…
          {flow.pollError && (
            <span className="normal-case tracking-normal text-ink-soft/80">
              ({flow.pollError})
            </span>
          )}
        </div>
      )}

      {/* Outcome of a manual “Re-check now” (#12) — never a silent no-op. */}
      {flow.recheckNote && !paid && (
        <div
          role="status"
          className="mt-4 inline-flex max-w-full items-center gap-2 rounded-[2px] border border-rule bg-paper-bright px-4 py-2.5 font-mono text-[11px] uppercase tracking-[0.16em] text-ink-soft"
        >
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-signal" aria-hidden="true" />
          {flow.recheckNote}
        </div>
      )}

      {/* §1 intent recap */}
      <section className="mt-10">
        <SectionLabel index="§1">The original intent</SectionLabel>
        <blockquote className="mt-4 border-l-2 border-signal pl-5 font-display text-[clamp(1.3rem,2.4vw,1.9rem)] leading-snug text-ink">
          {brief ?? (
            <>
              Buyer brief archived under intent{" "}
              <span className="font-mono text-base">{contract.intent_id}</span>
            </>
          )}
        </blockquote>
      </section>

      {/* §2+§3 selected offer */}
      <section className="mt-12 grid gap-6 md:grid-cols-12">
        <div className="md:col-span-7">
          <SelectedOfferPanel
            offer={offerMemo?.offer ?? null}
            explanation={offerMemo?.explanation}
            softScores={offerMemo?.softScores}
          />
        </div>
        <div className="space-y-6 md:col-span-5">
          {/* §6 hashes */}
          <ContractHashes contract={contract} />
          {/* §5 authorization envelope (post-authorize) */}
          {authorized && (
            <Panel>
              <SectionLabel index="§5">Authorization envelope</SectionLabel>
              <dl className="mt-4 space-y-2.5 font-mono text-[11px]">
                <div className="flex justify-between gap-3">
                  <dt className="uppercase tracking-[0.18em] text-ink-soft">Max amount</dt>
                  <dd className="tabular-nums text-ink">
                    {rupees(contract.buyer_authority?.max_amount_paise)}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="uppercase tracking-[0.18em] text-ink-soft">Authorized at</dt>
                  <dd className="text-ink">
                    {contract.buyer_authority?.authorized_at
                      ? new Date(contract.buyer_authority.authorized_at).toLocaleTimeString("en-IN")
                      : "—"}
                  </dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="uppercase tracking-[0.18em] text-ink-soft">
                    Hash at authorization
                  </dt>
                  <dd className="text-ink" title={contract.buyer_authority?.contract_hash_at_authorization ?? undefined}>
                    {shortHash(contract.buyer_authority?.contract_hash_at_authorization)}
                  </dd>
                </div>
              </dl>
              {contract.buyer_authority?.contract_hash_at_authorization &&
                contract.contract_hash &&
                contract.buyer_authority.contract_hash_at_authorization !==
                  contract.contract_hash && (
                  <MarginNote>
                    Hash drift detected since authorization — approval is stale.
                  </MarginNote>
                )}
            </Panel>
          )}
        </div>
      </section>

      {/* §4 material promises */}
      <section className="mt-14">
        <Rule />
        <div className="mt-8">
          <MaterialPromises promises={flow.promises} />
        </div>
      </section>

      {/* §7 razorpay */}
      <section className="mt-14" id="razorpay">
        <Rule />
        <div className="mt-8 grid gap-6 md:grid-cols-12">
          <div className="md:col-span-7">
            <RazorpayPanel
              status={contract.status}
              orderInfo={flow.orderInfo}
              orderId={contract.razorpay_order_id}
              paymentId={contract.razorpay_payment_id}
              pollingActive={flow.pollingActive}
              sandboxMode={contract.sandbox_mode || (flow.orderInfo?.mode === "sandbox" ? true : undefined)}
              recheckoutAvailable={
                !contract.sandbox_mode &&
                !!flow.orderInfo?.checkout_config?.key_id &&
                contract.status === "PAYMENT_ORDER_CREATED"
              }
              onSimulateCapture={handleSimulate}
              onReopenCheckout={
                flow.orderInfo && !contract.sandbox_mode
                  ? () => openRazorpayCheckout(flow.orderInfo!)
                  : undefined
              }
              simulating={simulating}
              onRecheck={() => contractId && void flow.recheckStatus(contractId)}
            />
          </div>
          {/* §8 rights overview */}
          <div className="md:col-span-5">
            <Panel tone="bright" className="h-full">
              <SectionLabel index="§8">Rights overview</SectionLabel>
              <div className="mt-4 flex items-baseline gap-3">
                <span className="font-display text-5xl leading-none text-ink tabular-nums">
                  {flow.entitlements.length}
                </span>
                <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink-soft">
                  entitlements
                  <br />
                  created at freeze
                </span>
              </div>
              <ul className="mt-4 space-y-1">
                {flow.entitlements.slice(0, 5).map((e) => (
                  <li key={e.id} className="flex items-center justify-between gap-2">
                    <span className="font-body text-[13px] text-ink">
                      {e.type.replaceAll("_", " ")}{" "}
                      <span className="font-mono text-[10px] text-ink-soft">
                        / {e.issuer_name}
                      </span>
                    </span>
                    <Badge
                      tone={
                        e.status === "eligible" || e.status === "active"
                          ? "success"
                          : e.status === "blocked" || e.status === "invalid"
                            ? "danger"
                            : "neutral"
                      }
                    >
                      {e.status}
                    </Badge>
                  </li>
                ))}
              </ul>
              <Link href={`/contract/${contract.id}/rights`}>
                <Button variant="secondary" className="mt-5 w-full">
                  Open purchase rights graph →
                </Button>
              </Link>
            </Panel>
          </div>
        </div>
      </section>

      {/* footer nav */}
      <footer className="mt-16 flex flex-wrap items-center justify-between gap-3 border-t border-rule pt-5">
        <Dateline>
          Aster Electronics · Dante contract runtime · every money action bounded & gated
        </Dateline>
        <Link href="/buy" className="font-body text-[13px] underline underline-offset-4 hover:text-ink">
          New brief
        </Link>
      </footer>

      {/* sticky §52 authorization bar — only while the gate is genuinely
          open. Once authorize resolves into creating_order/sandbox_ready/
          checkout_ready/payment_pending the card clears (#1): re-authorizing
          is a server-side 409 and a second payment-order would mint a
          duplicate payable order. */}
      {awaitingAuth &&
        !checkoutOpen &&
        !POST_AUTHORIZE_PHASES.includes(flow.phase) && (
        <div className="fixed inset-x-0 bottom-0 z-40 border-t-2 border-signal bg-paper/95 backdrop-blur">
          <div className="mx-auto max-w-6xl px-5 py-4 md:px-10">
            <AuthorizationCard
              contract={contract}
              promises={flow.promises}
              offerTitle={offerMemo?.offer.title ?? null}
              onAuthorize={handleAuthorize}
              authorizing={authorizing || flow.phase === "opening_checkout"}
            />
          </div>
        </div>
      )}

      {/* sandbox hand-off note — replaces the cleared authorization bar so the
          buyer knows what to do next (simulate button lives in §7 panel). */}
      {!awaitingAuth &&
        !paid &&
        contract.sandbox_mode &&
        (flow.phase === "sandbox_ready" || flow.phase === "opening_checkout") && (
          <div className="fixed inset-x-0 bottom-0 z-40 border-t-2 border-warning bg-paper-bright">
            <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-5 py-4 md:px-10">
              <p className="font-body text-[13px] leading-snug text-warning">
                Order created in sandbox mode — no Razorpay keys configured.
                Use “Simulate test payment” in the Razorpay section above; the
                signed webhook confirms it server-side.
              </p>
              <a
                href="#razorpay"
                className="font-mono text-[11px] uppercase tracking-[0.18em] text-ink underline underline-offset-4 hover:text-signal"
              >
                Jump to Razorpay ↓
              </a>
            </div>
          </div>
        )}

      {flow.phase === "error_authorize" && (
        <div className="fixed inset-x-0 bottom-0 z-40 border-t-2 border-danger bg-paper-bright">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-5 py-4 md:px-10">
            <p className="font-body text-[13px] text-danger">{flow.error}</p>
            <Button variant="secondary" onClick={() => flow.resetError()}>
              Dismiss
            </Button>
          </div>
        </div>
      )}
    </main>
  );
}
