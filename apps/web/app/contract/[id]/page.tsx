"use client";

/**
 * /contract/[id] — the visual centerpiece (§28).
 *
 * Sections: intent recap · selected offer · why selected · material promises
 * · authorization envelope · contract hashes · Razorpay state · rights
 * overview. Sticky bottom bars carry each buyer decision:
 *
 * STAGE 1 — §52 "YOU ARE ABOUT TO AUTHORIZE" card while AWAITING_BUYER_AUTH:
 *   POST /authorize → POST /payment-order → checkout_config stored (and
 *   session-persisted). This stage creates NO payment window itself.
 *
 * STAGE 2 — a distinct explicit "Pay ₹X securely via Razorpay" button whose
 * onClick calls rzp.open() synchronously (zero awaits between click and
 * open) — checkout.js needs a live user gesture to escape popup blocking.
 * In sandbox mode Stage 2 remains the clearly-badged simulate button instead.
 *
 * Payment paths (per docs/API_CONTRACT.md):
 * - sandbox:      payment-order returns mode "sandbox" → clearly-badged button
 *                 fires POST /api/demo/razorpay/simulate-event, then poll to PAID.
 * - live-test:    load checkout.js, open Standard Checkout on Pay, handler records
 *                 the provider callback as advisory and polls to PAID.
 * Client success is never final truth — the signature-verified webhook is.
 */

import Link from "next/link";
import Script from "next/script";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import type {
  PaymentOrderResponse,
  RazorpayCheckoutOptions,
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
  const [rzpScriptFailed, setRzpScriptFailed] = useState(false);
  const [dismissedNote, setDismissedNote] = useState<string | null>(null);
  const paymentFailureRef = useRef(false);

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

  /**
   * Build Standard Checkout options for an order and call .open() on it.
   * Every option audited against Razorpay's documented checkout.js surface:
   * `key` (the public key id VALUE — never `key_id`), `order_id` (server-
   * minted, reconciled against amount/currency), integer-paise `amount`,
   * `currency`, `name`/`description`, buyer `prefill`, `notes`, theme accent,
   * success `handler` (razorpay_order_id / razorpay_payment_id /
   * razorpay_signature) and `modal.ondismiss`.
   */
  const buildRazorpayOptions = useCallback(
    (order: PaymentOrderResponse): RazorpayCheckoutOptions | null => {
      if (!contractId) return null;
      const cfg = order.checkout_config;
      return {
        key: cfg.key_id, // checkout.js reads `key`; key_id is not an option
        amount: cfg.amount_paise, // integer paise — matches the server order
        currency: cfg.currency || "INR",
        name: "ASTER ELECTRONICS",
        description:
          offerMemo?.items?.map((item) => `${item.quantity}× ${item.offer.title}`).join(", ") ??
          offerMemo?.offer.title ??
          "Dante contract purchase",
        order_id: cfg.order_id,
        prefill: { name: "Demo Buyer" },
        notes: {
          dante_contract_id: contractId,
          dante_order_id: String(cfg.order_id),
        },
        theme: { color: "#2F6FED" },
        handler: () => {
          // The callback is advisory only. The signed Razorpay webhook is the
          // sole authority that can move the contract to PAID.
          setDismissedNote(
            "Checkout returned; waiting for Razorpay webhook confirmation.",
          );
          setCheckoutOpen(false);
          flow.pollUntilResolved(contractId);
        },
        modal: {
          ondismiss: () => {
            setCheckoutOpen(false);
            if (!paymentFailureRef.current) {
              setDismissedNote(
                "Checkout closed before completing. If the payment actually went through, server reconciliation will confirm it — watch the status below.",
              );
            }
            // window-closed fallback (§33.5): resume polling regardless
            flow.pollUntilResolved(contractId);
          },
        },
      };
    },
    [contractId, offerMemo, flow],
  );

  /**
   * STAGE 1 of the §52 gate: authorize + create the payment order. No window
   * opens here — Stage 2's explicit Pay click owns rzp.open().
   */
  async function handleAuthorize() {
    if (!contractId) return;
    // Idempotency guard: authorizeContract refuses re-entrant calls, and this
    // early-return keeps the button from even starting one.
    if (authorizing || flow.phase === "opening_checkout") return;
    setAuthorizing(true);
    try {
      await flow.authorizeContract(contractId);
    } finally {
      setAuthorizing(false);
    }
  }

  /**
   * STAGE 2: the explicit Pay button. Runs synchronously off onClick —
   * build options → new window.Razorpay(...) → .open() with no await in
   * between — so the checkout window opens inside the browser's user-gesture
   * window and cannot be popup-blocked.
   */
  function handlePayNow() {
    if (!contractId || !flow.orderInfo) return;
    setDismissedNote(null);
    const options = buildRazorpayOptions(flow.orderInfo);
    if (!options) return;
    if (!window.Razorpay) {
      // Script still loading (or blocked) — surface it instead of failing
      // silently; a retry click once checkout.js is ready succeeds.
      setDismissedNote(
        rzpScriptFailed
          ? "The Razorpay checkout script failed to load — check the connection and try again."
          : "The Razorpay checkout script is still loading — try again in a moment.",
      );
      return;
    }
    paymentFailureRef.current = false;
    const opened = flow.openCheckout();
    if (!opened) {
      setDismissedNote("Checkout could not be started — please try again.");
      return;
    }
    const rzp = new window.Razorpay(options);
    rzp.on?.("payment.failed", (resp: { error?: { description?: string } }) => {
      paymentFailureRef.current = true;
      setDismissedNote(
        resp.error?.description
          ? `Payment attempt failed: ${resp.error.description}`
          : "Payment attempt failed at the gateway.",
      );
    });
    rzp.open();
    setCheckoutOpen(true);
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
      <main className="dante-container min-h-screen pb-24 pt-8 md:pt-10">
        <Folio>Issue 01 / Buy</Folio>
        <Rule className="mt-4" />
        <Panel tone="signal" className="mt-8">
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
  // The §52 gate is open for a freshly-frozen contract too: the server
  // accepts CONTRACT_FROZEN → AWAITING_BUYER_AUTH, so Stage 1's card must
  // render there or the buyer can never authorize (chicken-and-egg).
  const awaitingAuth =
    contract.status === "AWAITING_BUYER_AUTH" ||
    contract.status === "CONTRACT_FROZEN";
  const authorized = contract.buyer_authority != null;

  return (
      <main className="dante-container min-h-screen pb-40 pt-8 md:pt-10">
      {/* Razorpay checkout.js — lazyOnload; opened on demand from the
          Stage 2 Pay click (never auto-opened without a user gesture) */}
      {!contract.sandbox_mode && !rzpScriptReady && (
        <Script
          src="https://checkout.razorpay.com/v1/checkout.js"
          strategy="lazyOnload"
          onLoad={() => setRzpScriptReady(true)}
          onError={() => setRzpScriptFailed(true)}
        />
      )}

      {/* masthead */}
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div><p className="folio-label text-action-deep">Purchase dossier</p><h1 className="mt-2 text-3xl font-semibold tracking-[-0.045em] text-ink md:text-4xl">{contract.display_code ?? contract.id}</h1></div>
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
        <div className="mt-6 rounded-lg border border-success/30 bg-paper-bright px-5 py-4 shadow-[0_1px_2px_rgba(16,24,40,0.03)]">
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

      {/* Breach banner — the breach spread was an orphan route (final-assault
          finding [17]); this surfaces it exactly when the lifecycle enters
          breach/remedy territory. */}
      {(contract.status === "BREACH_DETECTED" ||
        contract.status === "REMEDIATED") && (
        <div
          className={`mt-6 rounded-lg border px-5 py-4 ${
            contract.status === "BREACH_DETECTED"
              ? "border-signal/60 bg-paper-bright"
              : "border-success/50 bg-paper-bright"
          }`}
        >
          <span
            className={`font-mono text-[12px] uppercase tracking-[0.22em] ${
              contract.status === "BREACH_DETECTED" ? "text-signal" : "text-success"
            }`}
          >
            {contract.status === "BREACH_DETECTED"
              ? "Material breach detected — observed reality contradicts a frozen promise"
              : "Remediated — buyer made whole through a policy-gated remedy"}
          </span>{" "}
          <Link
            href={`/contract/${contract.id}/breach`}
            className="ml-2 font-body text-[13px] underline underline-offset-4 hover:text-ink"
          >
            Open the PROMISED vs OBSERVED breach dossier →
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
          <blockquote className="mt-4 rounded-r-lg border-l-4 border-signal bg-signal/[0.04] px-5 py-4 text-[clamp(1.1rem,2vw,1.7rem)] font-medium leading-snug tracking-[-0.025em] text-ink">
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
            items={offerMemo?.items}
            lineItems={contract.line_items}
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
                flow.canOpenCheckout &&
                (contract.status === "PAYMENT_ORDER_CREATED" ||
                  contract.status === "PAYMENT_PENDING")
              }
              onSimulateCapture={handleSimulate}
              onReopenCheckout={
                contract.sandbox_mode ? undefined : handlePayNow
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

      {/* STAGE 1 — sticky §52 authorization bar, shown only while the gate is
          genuinely open (AWAITING_BUYER_AUTH, order not yet created). Once
          authorize resolves into opening_checkout/sandbox_ready/checkout_ready/
          payment_pending the card clears (#1): re-authorizing is a server-side
          409 and a second payment-order would mint a duplicate payable order.
          This stage creates NO payment window — Stage 2 owns rzp.open(). */}
      {awaitingAuth &&
        !checkoutOpen &&
        !POST_AUTHORIZE_PHASES.includes(flow.phase) && (
        <div className="fixed inset-x-0 bottom-0 z-40 border-t-2 border-signal bg-paper/95 backdrop-blur">
          <div className="mx-auto max-w-6xl px-5 py-4 md:px-10">
            <AuthorizationCard
              contract={contract}
              promises={flow.promises}
              offerTitle={
                offerMemo?.items
                  ? `${offerMemo.items.length} frozen line items`
                  : offerMemo?.offer.title ?? null
              }
              onAuthorize={handleAuthorize}
              authorizing={authorizing || flow.phase === "opening_checkout"}
            />
          </div>
        </div>
      )}

      {/* STAGE 2 — distinct explicit Pay button once an order exists (live
          test mode). handlePayNow runs synchronously off this onClick —
          options → new window.Razorpay(...) → .open() with zero awaits — so
          the checkout window opens inside the browser's user-gesture window
          instead of being popup-blocked. Sandbox never lands here: it keeps
          the clearly-badged simulate affordance in §7. */}
      {!awaitingAuth &&
        !paid &&
        !contract.sandbox_mode &&
        flow.canOpenCheckout &&
        !checkoutOpen &&
        (contract.status === "PAYMENT_ORDER_CREATED" ||
          contract.status === "PAYMENT_PENDING") && (
        <div className="fixed inset-x-0 bottom-0 z-40 border-t-2 border-signal bg-paper-bright">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-5 py-4 md:px-10">
            <p className="font-body text-[13px] leading-snug text-ink-soft">
              Order {shortHash(flow.orderInfo?.checkout_config.order_id)} created
              for {rupees(flow.orderInfo?.checkout_config.amount_paise)}. Nothing
              is charged until you complete Razorpay&rsquo;s secure checkout.
            </p>
            <Button onClick={handlePayNow}>
              Pay{" "}
              {rupees(
                flow.orderInfo?.checkout_config.amount_paise ??
                  contract.amount_paise,
              )}{" "}
              securely via Razorpay
            </Button>
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

      {(flow.phase === "error_authorize" || flow.phase === "error_order") && (
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
