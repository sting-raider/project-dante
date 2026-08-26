"use client";

/**
 * RAZORPAY panel (§28 §7) — mode chip, order id, payment id once known,
 * live status via polling. In sandbox mode renders the clearly-badged
 * simulate button that fires the documented simulate-event endpoint; in
 * live-test-mode the Standard Checkout window is driven from page.tsx.
 */

import type {
  ContractStatus,
  PaymentOrderResponse,
} from "@/lib/useContractFlow";
import { rupees } from "@/lib/useContractFlow";
import { Badge, Button, MonoValue, Panel, Rule, SandboxBadge, SectionLabel } from "./atoms";

function StatusChip({ status }: { status: ContractStatus }) {
  const tone =
    status === "PAID" || status === "SATISFIED"
      ? "success"
      : status.startsWith("BREACH") || status === "FAILED"
        ? "danger"
        : status === "PAYMENT_PENDING" || status === "PAYMENT_ORDER_CREATED"
          ? "warning"
          : "neutral";
  return <Badge tone={tone}>{status.replaceAll("_", " ")}</Badge>;
}

export function RazorpayPanel({
  status,
  orderInfo,
  orderId,
  paymentId,
  pollingActive,
  sandboxMode,
  recheckoutAvailable,
  onSimulateCapture,
  onReopenCheckout,
  simulating,
  onRecheck,
}: {
  status: ContractStatus;
  orderInfo: PaymentOrderResponse | null;
  orderId?: string | null;
  paymentId?: string | null;
  pollingActive: boolean;
  /** Authoritative sandbox flag from the contract record (survives refresh). */
  sandboxMode?: boolean;
  /** True when an order exists and checkout can be (re)opened. */
  recheckoutAvailable?: boolean;
  onSimulateCapture: () => void;
  /** Re-open the Razorpay Standard Checkout window (live test-mode path). */
  onReopenCheckout?: () => void;
  simulating: boolean;
  onRecheck: () => void;
}) {
  // Sandbox detection must NOT hinge on in-memory orderInfo alone (#3): a
  // refresh used to make the simulate button vanish. The contract's own
  // sandbox_mode flag is authoritative; the cached order response corroborates.
  const isSandbox = sandboxMode ?? orderInfo?.mode === "sandbox";
  const awaitingPayment =
    status === "PAYMENT_PENDING" || status === "PAYMENT_ORDER_CREATED";

  return (
    <Panel>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SectionLabel index="§7">Razorpay</SectionLabel>
        <div className="flex items-center gap-2">
          {isSandbox && <SandboxBadge />}
          {!isSandbox && (orderInfo || status === "PAID") && (
            <Badge tone="neutral">live test mode</Badge>
          )}
          <StatusChip status={status} />
        </div>
      </div>

      <dl className="mt-4 space-y-2 font-mono text-[11px]">
        <div className="flex justify-between gap-3">
          <dt className="uppercase tracking-[0.18em] text-ink-soft">Order id</dt>
          <dd className="text-right">
            <MonoValue value={orderId ?? orderInfo?.checkout_config.order_id ?? "—"} />
          </dd>
        </div>
        <div className="flex justify-between gap-3">
          <dt className="uppercase tracking-[0.18em] text-ink-soft">Payment id</dt>
          <dd className="text-right">
            <MonoValue value={paymentId ?? "not yet known"} />
          </dd>
        </div>
        {orderInfo && (
          <div className="flex justify-between gap-3">
            <dt className="uppercase tracking-[0.18em] text-ink-soft">Order amount</dt>
            <dd className="tabular-nums text-ink">
              {rupees(orderInfo.checkout_config.amount_paise)}
            </dd>
          </div>
        )}
      </dl>

      {/* sandbox path — shown whenever the rail is sandbox and payment is
          still open, however this page was loaded (#3) */}
      {isSandbox && awaitingPayment && (
        <>
          <Rule className="my-4" />
          <p className="font-mono text-[11px] uppercase tracking-[0.16em] text-warning">
            Sandbox mode — no Razorpay keys configured
          </p>
          <p className="mt-2 font-body text-[12px] leading-relaxed text-ink-soft">
            The button below asks the demo endpoint to deliver a{" "}
            <em>real signed webhook</em>{" "}
            (<code className="font-mono text-[11px]">payment.captured</code>)
            through the normal server verification pipeline — no client-side
            state fabrication.
          </p>
          <div className="mt-4">
            <Button variant="secondary" onClick={onSimulateCapture} disabled={simulating}>
              {simulating ? "Delivering…" : "Simulate test payment (SANDBOX)"}
            </Button>
          </div>
        </>
      )}

      {/* live test-mode path: reopen Standard Checkout after a dismissal or a
          refresh — previously impossible at PAYMENT_ORDER_CREATED (#3). The
          primary Pay affordance lives in the sticky bottom bar (Stage 2);
          this secondary control covers the window-closed/refresh case. */}
      {!isSandbox && awaitingPayment && recheckoutAvailable && onReopenCheckout && (
        <div className="mt-4 border-t border-rule pt-4">
          <p className="font-body text-[12px] leading-relaxed text-ink-soft">
            Checkout window closed or lost? The order is still live — reopen it.
            Closing the window again simply resumes server polling.
          </p>
          <div className="mt-3">
            <Button variant="secondary" onClick={onReopenCheckout}>
              Re-open Razorpay checkout
            </Button>
          </div>
        </div>
      )}

      {/* pending / reconciliation affordances */}
      {awaitingPayment && (
        <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-rule pt-4">
          <span className="inline-flex items-center gap-2 font-mono text-[11px] text-warning">
            <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-warning" aria-hidden="true" />
            {pollingActive ? "polling server every 2s" : "awaiting server confirmation"}
          </span>
          <Button variant="ghost" onClick={onRecheck}>
            Re-check now
          </Button>
        </div>
      )}

      {status === "PAID" && (
        <div className="mt-4 border-t border-rule pt-4">
          <span className="font-mono text-[12px] uppercase tracking-[0.2em] text-success">
            Paid — verified by webhook truth
          </span>
        </div>
      )}
    </Panel>
  );
}
