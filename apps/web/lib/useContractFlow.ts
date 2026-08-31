"use client";

/**
 * useContractFlow — client state machine for the Dante buyer journey.
 *
 * Drives /buy (compile → search → select) and the contract lifecycle
 * (authorize → payment-order → checkout/sandbox-simulate → PAID), polling
 * GET /api/contracts/{id} every 2s while a payment is pending so the UI
 * reflects server-side webhook truth, never the browser's word.
 *
 * Endpoint shapes come from docs/API_CONTRACT.md; field names mirror
 * apps/api/project_dante/domain/types.py exactly.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, apiPost, ApiError } from "@/lib/api";

// ---------------------------------------------------------------- domain types
// Mirrors project_dante.domain.types — keep in sync; additive only.

export type Constraint = {
  key: string;
  op: "eq" | "lte" | "gte" | "lt" | "gt" | "in" | "contains";
  value: unknown;
  critical: boolean;
};

export type Preference = { key: string; weight: number; value: unknown };

export type IntentItem = {
  id: string;
  label: string;
  hard_constraints: Constraint[];
  soft_preferences: Preference[];
  max_price_paise: number | null;
  quantity: number;
};

export type BuyerIntent = {
  id: string;
  raw_text: string;
  hard_constraints: Constraint[];
  soft_preferences: Preference[];
  items: IntentItem[];
  max_total_amount_paise: number | null;
  autonomous_spend_limit_paise: number | null;
  substitutions_allowed: boolean;
  created_at: string | null;
  compiler_version: string;
};

export type MerchantOffer = {
  id: string;
  merchant_id: string;
  sku: string;
  title: string;
  variant: Record<string, string>;
  unit_amount_paise: number;
  currency: "INR";
  inventory: number;
  delivery_promise: {
    min_days?: number | null;
    max_days?: number | null;
    promised_by_date?: string | null;
    service?: string | null;
  };
  terms: {
    warranty_type: "manufacturer" | "seller" | "none" | "unknown";
    warranty_duration_months?: number | null;
    warranty_region?: string | null;
    return_window_days?: number | null;
    replacement_window_days?: number | null;
    condition: "new" | "refurbished" | "used" | "unknown";
    region?: string | null;
    notes?: string | null;
  };
  expires_at?: string | null;
  category?: string | null;
  brand?: string | null;
  attributes?: Record<string, unknown>;
};

export type HardFailure = { key: string; op: string; expected: unknown; actual: unknown };

export type SoftScore = { key: string; weight: number; score: number; note: string };

export type OfferEvaluation = {
  feasible: boolean;
  hard_failures: HardFailure[];
  soft_scores: SoftScore[];
  explanation: string;
};

export type SearchResult = {
  item_id?: string;
  offer: MerchantOffer;
  evaluation: OfferEvaluation;
};

export type SearchItemGroup = {
  item_id: string;
  label: string;
  max_price_paise: number | null;
  quantity: number;
  results: SearchResult[];
  feasible_count: number;
  recommended_offer_id?: string | null;
};

export type BundleRecommendation = {
  available: boolean;
  engine: string;
  offer_ids: Record<string, string>;
  total_amount_paise: number | null;
  score: number | null;
  reason: string;
};

export type Promise_ = {
  id: string;
  line_item_id?: string | null;
  key: string;
  value: unknown;
  normalized_value?: unknown;
  source_artifact_id?: string | null;
  extraction_method: "structured" | "agent_extracted" | "derived";
  verification_status: "verified" | "merchant_asserted" | "unverified";
  confidence?: number | null;
  material_to_intent: boolean;
  material_reason?: string | null;
};

export type EvidenceArtifact = {
  id: string;
  line_item_id?: string | null;
  source_type: string;
  raw_payload_ref: string;
  sha256: string;
  observed_at: string;
  trusted_level: string;
  synthetic: boolean;
  excerpt?: string | null;
};

export type AuthorityEnvelope = {
  max_amount_paise: number;
  currency: "INR";
  authorized_at?: string | null;
  authorized_by: string;
  scope?: "single_purchase";
  contract_hash_at_authorization?: string | null;
};

export type ContractStatus =
  | "DRAFT"
  | "INTENT_READY"
  | "OFFER_SELECTED"
  | "CONTRACT_FROZEN"
  | "AWAITING_BUYER_AUTH"
  | "PAYMENT_ORDER_CREATED"
  | "PAYMENT_PENDING"
  | "PAID"
  | "FULFILLING"
  | "DELIVERED"
  | "VERIFYING"
  | "SATISFIED"
  | "BREACH_DETECTED"
  | "REMEDY_PLANNING"
  | "AWAITING_REMEDY_APPROVAL"
  | "REMEDY_EXECUTING"
  | "REMEDIATED"
  | "CANCELLED"
  | "FAILED";

export type DanteContract = {
  id: string;
  display_code?: string | null;
  intent_id: string;
  offer_id: string;
  line_items?: {
    id: string;
    intent_item_id?: string | null;
    offer_id: string;
    sku: string;
    title: string;
    quantity: number;
    unit_amount_paise: number;
    amount_paise: number;
    offer_hash?: string | null;
    promise_ids?: string[];
  }[];
  buyer_authority?: AuthorityEnvelope | null;
  offer_hash?: string | null;
  promise_set_hash?: string | null;
  contract_hash?: string | null;
  razorpay_order_id?: string | null;
  razorpay_payment_id?: string | null;
  amount_paise?: number | null;
  status: ContractStatus;
  created_at?: string | null;
  frozen_at?: string | null;
  sandbox_mode: boolean;
};

export type ContractDetail = {
  contract: DanteContract;
  promises: Promise_[];
  entitlements: {
    id: string;
    issuer_type: string;
    issuer_name: string;
    type: string;
    expires_at?: string | null;
    remedy_value_paise?: number | null;
    execution_mode: string;
    status:
      | "dormant"
      | "eligible"
      | "active"
      | "consumed"
      | "expired"
      | "invalid"
      | "blocked";
  }[];
};

export type PaymentOrderResponse = {
  mode: "live-test-mode" | "sandbox";
  razorpay_order: Record<string, unknown>;
  checkout_config: {
    key_id: string;
    order_id: string;
    amount_paise: number;
    currency: string;
  };
  contract_status?: ContractStatus;
};

// Razorpay Standard Checkout handler payload (subset we consume).
export type RazorpayHandlerResponse = {
  razorpay_order_id: string;
  razorpay_payment_id: string;
  razorpay_signature: string;
};

/**
 * Razorpay Standard Checkout options (checkout.js v1: new Razorpay(options)).
 * Every field below is on the documented Standard Checkout surface:
 * - key             : the PUBLIC key id VALUE (rzp_test_…) — checkout.js reads
 *                     the option named `key`, never `key_id`
 * - order_id        : order created server-side via the Orders API; when
 *                     present Razorpay reconciles amount/currency against it
 * - amount/currency : integer paise + ISO currency, matching the order
 * - name/description: merchant and item lines shown on the payment sheet
 * - prefill         : optional buyer name/email/contact hints
 * - notes           : key/value metadata echoed onto the payment record
 * - theme.color     : checkout widget accent color
 * - handler         : success callback — response carries exactly
 *                     razorpay_order_id, razorpay_payment_id, razorpay_signature
 *                     (signature is verified SERVER-side, never trusted here)
 * - modal.ondismiss : buyer closed the window — resume webhook polling; it is
 *                     a normal outcome, not an error
 */
export type RazorpayCheckoutOptions = {
  key: string;
  order_id: string;
  amount: number;
  currency: string;
  name: string;
  description?: string;
  prefill?: { name?: string; email?: string; contact?: string };
  notes?: Record<string, string>;
  theme?: { color?: string };
  handler: (response: RazorpayHandlerResponse) => void;
  modal?: { ondismiss?: () => void };
};

declare global {
  interface Window {
    Razorpay?: new (options: RazorpayCheckoutOptions) => {
      /** Present the checkout sheet. Must run inside a user-gesture call stack. */
      open: () => void;
      /**
       * checkout.js event subscription; `payment.failed` carries
       * { error: { description } } from the gateway.
       */
      on?: (
        event: "payment.failed",
        handler: (resp: { error?: { description?: string } }) => void,
      ) => void;
    };
  }
}

// ---------------------------------------------------------------- flow states

export type FlowPhase =
  // /buy pipeline (§28)
  | "idle"
  | "compiling"
  | "searching"
  | "shortlist" // results rendered; buyer is choosing
  | "awaiting_selection" // radio chosen, freeze button armed
  | "freezing" // select-offer in flight → navigate to dossier
  // contract pipeline
  | "awaiting_authorization"
  | "opening_checkout" // authorize + payment-order in flight
  | "checkout_ready" // live-test-mode: order created; awaiting the explicit Pay click
  | "sandbox_ready" // sandbox: awaiting simulated capture
  | "payment_pending" // verify sent / capture delivered; waiting on webhook truth
  | "paid"
  // error states carry retry via `retry`
  | "error_compile"
  | "error_search"
  | "error_select"
  | "error_contract_load"
  | "error_authorize"
  | "error_order"
  | "error_verify"
  | "error_poll";

/** Human-readable ticker strings per phase (§28 agent activity ticker). */
export const PHASE_TICKER: Partial<Record<FlowPhase, string>> = {
  compiling: "Compiling intent…",
  searching: "Searching merchant…",
  shortlist: "Offers evaluated — choose each line to freeze the bundle.",
  awaiting_selection: "Selection ready — freeze the bundle into a contract.",
  freezing: "Freezing promises…",
  awaiting_authorization: "Contract frozen. Awaiting your authorization.",
  opening_checkout: "Opening checkout…",
  checkout_ready: "Order created — complete payment via the Pay button.",
  sandbox_ready: "SANDBOX MODE — no Razorpay keys configured. Simulate below.",
  payment_pending: "Confirming against server-side webhook truth…",
  paid: "PAID — verified by webhook truth.",
};

const POLL_INTERVAL_MS = 2000;

export type UseContractFlow = ReturnType<typeof useContractFlow>;

export function useContractFlow() {
  // ---- /buy state
  const [phase, setPhase] = useState<FlowPhase>("idle");
  const [intent, setIntent] = useState<BuyerIntent | null>(null);
  const [engine, setEngine] = useState<string | null>(null);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searchItems, setSearchItems] = useState<SearchItemGroup[]>([]);
  const [bundleRecommendation, setBundleRecommendation] =
    useState<BundleRecommendation | null>(null);
  const [selectedOfferId, setSelectedOfferId] = useState<string | null>(null);
  const [selectedOfferIds, setSelectedOfferIds] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  // ---- contract state
  const [contractId, setContractId] = useState<string | null>(null);
  const [contract, setContract] = useState<DanteContract | null>(null);
  const [promises, setPromises] = useState<Promise_[]>([]);
  const [entitlements, setEntitlements] = useState<ContractDetail["entitlements"]>([]);
  const [orderInfo, setOrderInfo] = useState<PaymentOrderResponse | null>(null);
  const [verifyNote, setVerifyNote] = useState<string | null>(null);
  // Transient poll degradation — last-known data stays on screen; only an
  // explicit 404 replaces the whole page with the fatal screen.
  const [pollRetrying, setPollRetrying] = useState(false);
  const [pollError, setPollError] = useState<string | null>(null);
  // Result of the last manual "Re-check now" press (surfaced inline).
  const [recheckNote, setRecheckNote] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [pollingActive, setPollingActive] = useState(false);
  const mountedRef = useRef(true);
  /** Guards authorize→order against double-firing while a click is in flight. */
  const authorizeInFlightRef = useRef(false);

  const fail = useCallback((p: FlowPhase, e: unknown) => {
    if (!mountedRef.current) return;
    setPhase(p);
    setError(
      e instanceof ApiError
        ? `${e.status ? `HTTP ${e.status}: ` : ""}${e.message}`
        : e instanceof Error
          ? e.message
          : String(e),
    );
  }, []);

  const resetError = useCallback(() => setError(null), []);

  // ---------------------------------------------------------- polling

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    setPollingActive(false);
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      stopPolling();
    };
  }, [stopPolling]);

  const refreshContract = useCallback(
    async (
      id: string,
      options: { initialLoad?: boolean } = {},
    ): Promise<DanteContract | null> => {
      try {
        const detail = await apiGet<ContractDetail>(`/api/contracts/${id}`);
        if (!mountedRef.current) return detail.contract;
        setContract(detail.contract);
        setPromises(detail.promises ?? []);
        setEntitlements(detail.entitlements ?? []);
        setPollRetrying(false);
        setPollError(null);
        return detail.contract;
      } catch (e) {
        if (!mountedRef.current) return null;
        const msg =
          e instanceof ApiError
            ? `${e.status ? `HTTP ${e.status}: ` : ""}${e.message}`
            : e instanceof Error
              ? e.message
              : String(e);
        // 404 is fatal — the contract genuinely doesn't exist. During the
        // initial load there is no last-known data to keep, so every failure
        // must become an actionable error screen. Once data is on screen,
        // timeout/network/5xx failures degrade to a small retrying notice.
        if (e instanceof ApiError && e.status === 404) {
          setPhase(options.initialLoad ? "error_contract_load" : "error_poll");
          setError(msg);
          stopPolling();
          return null;
        }
        if (options.initialLoad) {
          setPhase("error_contract_load");
          setError(msg);
          return null;
        }
        setPollRetrying(true);
        setPollError(msg);
        return null;
      }
    },
    [stopPolling],
  );

  /**
   * Poll every 2s until the contract leaves PAYMENT_PENDING-ish states or
   * reaches a terminal post-payment state. Server truth wins.
   */
  const startPollingUntilResolved = useCallback(
    (id: string) => {
      stopPolling();
      setPollingActive(true);
      pollRef.current = setInterval(async () => {
        const c = await refreshContract(id);
        if (!c || !mountedRef.current) return;
        if (
          c.status !== "PAYMENT_PENDING" &&
          c.status !== "PAYMENT_ORDER_CREATED" &&
          c.status !== "AWAITING_BUYER_AUTH"
        ) {
          stopPolling();
          if (c.status === "PAID") setPhase("paid");
          else setPhase("awaiting_authorization"); // handed to other pages
        }
      }, POLL_INTERVAL_MS);
    },
    [refreshContract, stopPolling],
  );

  /** Load a contract page from URL param; resumes polling if payment pending. */
  const loadContract = useCallback(
    async (id: string): Promise<boolean> => {
      setError(null);
      setContractId(id); // Stage 2's openCheckout/canOpenCheckout gate on this
      const c = await refreshContract(id, { initialLoad: true });
      if (!c) return false;
      // Restore the payment-order context so RazorpayPanel keeps its mode
      // badge + simulate/open affordance across a cold refresh (#3). Sandbox
      // can re-derive the essentials locally; a live order must be read back
      // from the server because the public checkout key is not present in the
      // contract record and sessionStorage is not a trust boundary.
      if (!orderInfo) {
        const cached = readOrderSnapshot(id);
        let restored = cached ?? deriveOrderFromContract(c);
        const hasExistingLiveOrder =
          !c.sandbox_mode &&
          !!c.razorpay_order_id &&
          (c.status === "PAYMENT_ORDER_CREATED" ||
            c.status === "PAYMENT_PENDING");
        if (hasExistingLiveOrder) {
          try {
            restored = await apiGet<PaymentOrderResponse>(
              `/api/contracts/${id}/payment-order`,
            );
          } catch {
            // Keep the cached/derived view as a retryable fallback. A live
            // fallback has no key, so canOpenCheckout remains fail-closed.
          }
        }
        if (restored && mountedRef.current) setOrderInfo(restored);
      }
      setPhase("awaiting_authorization");
      if (
        c.status === "PAYMENT_PENDING" ||
        c.status === "PAYMENT_ORDER_CREATED" ||
        c.status === "AWAITING_BUYER_AUTH"
      ) {
        // resume live tracking of an in-flight payment
        startPollingUntilResolved(id);
        if (c.status === "PAYMENT_PENDING") setPhase("payment_pending");
      } else if (c.status === "PAID") {
        setPhase("paid");
      }
      return true;
    },
    [refreshContract, startPollingUntilResolved, orderInfo],
  );

  useEffect(() => () => stopPolling(), [stopPolling]);

  // ---------------------------------------------------------- /buy actions

  const compileAndSearch = useCallback(
    async (rawText: string) => {
      setError(null);
      setResults([]);
      setSearchItems([]);
      setBundleRecommendation(null);
      setSelectedOfferId(null);
      setSelectedOfferIds({});
      let stage: FlowPhase = "compiling";
      try {
        setPhase(stage);
        const compiled = await apiPost<{ intent: BuyerIntent; engine: string }>(
          "/api/intents/compile",
          { raw_text: rawText },
        );
        if (!mountedRef.current) return;
        setIntent(compiled.intent);
        setEngine(compiled.engine);

        stage = "searching";
        setPhase(stage);
        const searched = await apiPost<{
          intent: BuyerIntent;
          results: SearchResult[];
          items?: SearchItemGroup[];
          bundle_recommendation?: BundleRecommendation | null;
          engine: string;
        }>(`/api/intents/${compiled.intent.id}/search`);
        if (!mountedRef.current) return;
        setIntent(searched.intent);
        setResults(searched.results ?? []);
        setSearchItems(searched.items ?? []);
        setBundleRecommendation(searched.bundle_recommendation ?? null);
        setPhase("shortlist");
      } catch (e) {
        fail(stage === "searching" ? "error_search" : "error_compile", e);
      }
    },
    [fail],
  );

  /** Radio choose — records the selection locally; no network side effects. */
  const chooseOffer = useCallback((offerId: string) => {
    setError(null);
    setSelectedOfferId(offerId);
    setPhase("awaiting_selection");
  }, []);

  const chooseItemOffer = useCallback((itemId: string, offerId: string) => {
    setError(null);
    setSelectedOfferIds((previous) => ({ ...previous, [itemId]: offerId }));
    setPhase("awaiting_selection");
  }, []);

  const selectionComplete =
    searchItems.length > 0 &&
    searchItems.every((item) => {
      const selected = selectedOfferIds[item.item_id];
      return item.results.some(
        (result) => result.offer.id === selected && result.evaluation.feasible,
      );
    });

  const selectionTotalPaise = searchItems.reduce((total, item) => {
    const selected = selectedOfferIds[item.item_id];
    const result = item.results.find((candidate) => candidate.offer.id === selected);
    return total + (result?.offer.unit_amount_paise ?? 0) * Math.max(1, item.quantity);
  }, 0);
  const selectionWithinBudget =
    intent?.max_total_amount_paise == null ||
    selectionTotalPaise <= intent.max_total_amount_paise;

  const chooseRecommendedBundle = useCallback(() => {
    if (!bundleRecommendation?.available) return;
    setError(null);
    setSelectedOfferIds(bundleRecommendation.offer_ids);
    setPhase("awaiting_selection");
  }, [bundleRecommendation]);

  const selectOffers = useCallback(async (): Promise<string | null> => {
    if (!intent || !selectionComplete || !selectionWithinBudget) return null;
    setError(null);
    setPhase("freezing");
    try {
      const r = await apiPost<{
        contract: DanteContract;
        promises: Promise_[];
        evidence: EvidenceArtifact[];
      }>(`/api/intents/${intent.id}/select-offer`, {
        items: searchItems.map((item) => ({
          item_id: item.item_id,
          offer_id: selectedOfferIds[item.item_id],
        })),
      });
      if (!mountedRef.current) return null;
      return r.contract.id;
    } catch (e) {
      fail("error_select", e);
      return null;
    }
  }, [
    fail,
    intent,
    searchItems,
    selectedOfferIds,
    selectionComplete,
    selectionWithinBudget,
  ]);

  const selectOffer = useCallback(
    async (offerId: string): Promise<string | null> => {
      if (!intent) return null;
      setError(null);
      setSelectedOfferId(offerId);
      setPhase("freezing");
      try {
        const r = await apiPost<{
          contract: DanteContract;
          promises: Promise_[];
          evidence: EvidenceArtifact[];
        }>(`/api/intents/${intent.id}/select-offer`, { offer_id: offerId });
        if (!mountedRef.current) return null;
        return r.contract.id;
      } catch (e) {
        fail("error_select", e);
        return null;
      }
    },
    [intent, fail],
  );

  // ---------------------------------------------------------- contract actions

  const authorize = useCallback(
    async (id: string): Promise<boolean> => {
      setError(null);
      try {
        const r = await apiPost<{ contract: DanteContract }>(
          `/api/contracts/${id}/authorize`,
        );
        if (!mountedRef.current) return false;
        setContract(r.contract);
        return true;
      } catch (e) {
        fail("error_authorize", e);
        return false;
      }
    },
    [fail],
  );

  const createPaymentOrder = useCallback(
    async (id: string): Promise<PaymentOrderResponse | null> => {
      setError(null);
      setPhase("opening_checkout");
      try {
        const order = await apiPost<PaymentOrderResponse>(
          `/api/contracts/${id}/payment-order`,
        );
        if (!mountedRef.current) return null;
        setOrderInfo(order);
        // Pull server truth (contract.status → PAYMENT_ORDER_CREATED) now so
        // Stage 2's Pay affordance gates on fresh state instead of waiting
        // for the first 2s poll tick.
        await refreshContract(id);
        if (!mountedRef.current) return null;
        setPhase(order.mode === "sandbox" ? "sandbox_ready" : "checkout_ready");
        return order;
      } catch (e) {
        fail("error_order", e);
        return null;
      }
    },
    [fail, refreshContract],
  );

  /**
   * Legacy compatibility path for callers that still submit the Standard
   * Checkout callback. Client success alone is NOT treated as final truth —
   * the signature-verified webhook owns PAID. The contract page intentionally
   * does not call this endpoint in the live-test flow, so a clean checkout
   * leaves no client-verification audit event behind.
   */
  const verifyClient = useCallback(
    async (
      id: string,
      rzp: RazorpayHandlerResponse,
    ): Promise<"confirmed" | "failed"> => {
      try {
        const r = await apiPost<{
          status: string;
          contract_status: ContractStatus;
        }>("/api/payments/verify-client", {
          contract_id: id,
          razorpay_order_id: rzp.razorpay_order_id,
          razorpay_payment_id: rzp.razorpay_payment_id,
          signature: rzp.razorpay_signature,
        });
        setVerifyNote(`client ${r.status}; awaiting webhook confirmation`);
        setPhase("payment_pending");
        startPollingUntilResolved(id);
        return "confirmed";
      } catch (e) {
        fail("error_verify", e);
        return "failed";
      }
    },
    [startPollingUntilResolved, fail],
  );

  /**
   * Sandbox path: ask the demo endpoint to generate a REAL signed webhook
   * internally (documented POST /api/demo/razorpay/simulate-event). We do not
   * fabricate payment state client-side; we trigger the event and let the
   * normal webhook → server-truth pipeline flip the contract to PAID.
   *
   * Idempotent at the call site: a capture already in flight (or already
   * delivered — status past PAYMENT_ORDER_CREATED) refuses a second POST.
   */
  const simulateSandboxCapture = useCallback(
    async (id: string, orderId: string): Promise<boolean> => {
      try {
        await apiPost("/api/demo/razorpay/simulate-event", {
          event_type: "payment.captured",
          order_id: orderId,
        });
        if (!mountedRef.current) return true;
        setVerifyNote("sandbox capture delivered as signed webhook");
        setPhase("payment_pending");
        startPollingUntilResolved(id);
        return true;
      } catch (e) {
        fail("error_verify", e);
        return false;
      }
    },
    [startPollingUntilResolved, fail],
  );

  /** True once a sandbox simulate has been fired for this contract. */
  const [simulatedOrderIds, setSimulatedOrderIds] = useState<Set<string>>(new Set());

  /** Sandbox button target: simulate capture for the current contract/order. */
  const simulateSandboxPayment = useCallback(async (): Promise<boolean> => {
    const id = contract?.id ?? null;
    const orderId =
      orderInfo?.checkout_config.order_id ?? contract?.razorpay_order_id ?? null;
    if (!id || !orderId) return false;
    if (simulatedOrderIds.has(String(orderId))) return true; // already fired
    setSimulatedOrderIds((prev) => new Set(prev).add(String(orderId)));
    return simulateSandboxCapture(id, String(orderId));
  }, [contract, orderInfo, simulatedOrderIds, simulateSandboxCapture]);

  /**
   * Manual re-check button (window-closed fallback). Always surfaces its
   * outcome — a status change announces the new state, otherwise the buyer
   * is told the webhook hasn't landed yet.
   */
  const recheckStatus = useCallback(
    async (id: string) => {
      setRecheckNote("re-checking against server truth…");
      const c = await refreshContract(id);
      if (!mountedRef.current) return;
      if (!c) {
        setRecheckNote(`re-check failed — ${pollError ?? "server unreachable"}; will keep polling`);
        return;
      }
      if (c.status === "PAID") {
        stopPolling();
        setPhase("paid");
        setRecheckNote(null); // PAID banner takes over from here
        return;
      }
      setRecheckNote(`still ${c.status.replaceAll("_", " ")} — awaiting webhook confirmation; polling continues`);
    },
    [refreshContract, stopPolling, pollError],
  );

  /**
   * STAGE 1 of the two-stage §52 gate: POST authorize → POST payment-order,
   * persisting the checkout_config snapshot. It deliberately does NOT open
   * any checkout window — Stage 2's "Pay" button must call rzp.open()
   * directly from its own onClick handler with no await between click and
   * open, so the popup survives the browser's user-gesture requirement.
   *
   * Idempotent: a re-entrant call while one is already in flight is a no-op —
   * no double POSTs, no double Razorpay orders. The server also refuses a
   * second authorize (409 invalid_transition), but the client must never fire
   * the duplicate request in the first place.
   */
  const authorizeContract = useCallback(
    async (id: string): Promise<PaymentOrderResponse | null> => {
      if (authorizeInFlightRef.current) return null; // click already in flight
      setError(null);
      setRecheckNote(null);
      setPhase("opening_checkout");
      authorizeInFlightRef.current = true;
      try {
        const ok = await authorize(id);
        if (!ok) return null; // authorize() set error_authorize
        const order = await createPaymentOrder(id);
        if (!order) return null; // createPaymentOrder() set error_order
        persistOrderSnapshot(id, order); // survives refresh (#3)
        return order;
      } finally {
        authorizeInFlightRef.current = false;
      }
    },
    [authorize, createPaymentOrder],
  );

  /**
   * STAGE 2: open Razorpay Standard Checkout for an existing order. The page
   * calls this synchronously from the explicit Pay button's onClick handler
   * (no await before it) so `rzp.open()` runs inside the user gesture and the
   * checkout window is never popup-blocked. Returns true when the window was
   * opened; false when checkout.js wasn't ready yet (caller surfaces a note).
   */
  const openCheckout = useCallback((): boolean => {
    if (!contractId || !orderInfo || orderInfo.mode !== "live-test-mode") {
      return false;
    }
    if (typeof window === "undefined" || !window.Razorpay) return false;
    startPollingUntilResolved(contractId); // webhook flips PAID while sheet is up
    setPhase("checkout_ready");
    return true;
  }, [contractId, orderInfo, startPollingUntilResolved]);

  /** True when Stage 2's live Pay button can open checkout right now. */
  const canOpenCheckout =
    !!contractId &&
    !!orderInfo &&
    orderInfo.mode === "live-test-mode" &&
    typeof orderInfo.checkout_config.key_id === "string" &&
    orderInfo.checkout_config.key_id !== "";

  /**
   * Public wrapper: resume 2s polling without changing phase (§33.5 —
   * buyer closed the checkout window; webhook reconciliation may still land).
   */
  const pollUntilResolved = useCallback(
    (id: string) => {
      startPollingUntilResolved(id);
      setPhase("payment_pending");
    },
    [startPollingUntilResolved],
  );

  return {
    // state
    phase,
    intent,
    engine,
    results,
    searchItems,
    bundleRecommendation,
    selectedOfferId,
    selectedOfferIds,
    selectionComplete,
    selectionTotalPaise,
    selectionWithinBudget,
    error,
    contractId,
    setContractId,
    contract,
    promises,
    entitlements,
    orderInfo,
    verifyNote,
    pollRetrying,
    pollError,
    recheckNote,
    // derived
    isBusy: ["compiling", "searching", "freezing", "opening_checkout"].includes(phase),
    // /buy
    compileAndSearch,
    chooseOffer,
    chooseItemOffer,
    chooseRecommendedBundle,
    selectOffer,
    selectOffers,
    resetError,
    // contract
    loadContract,
    refreshContract,
    authorize,
    // two-stage gate: Stage 1 creates the order, Stage 2 opens the window
    authorizeContract,
    openCheckout,
    canOpenCheckout,
    createPaymentOrder,
    verifyClient,
    simulateSandboxCapture,
    simulateSandboxPayment,
    recheckStatus,
    pollUntilResolved,
    pollingActive,
  };
}

// ---------------------------------------------------------------- helpers

/** shortHash for mono UI display: first 10 hex chars + ellipsis. */
export function shortHash(h?: string | null, n = 10): string {
  if (!h) return "—";
  return h.length <= n ? h : `${h.slice(0, n)}…`;
}

/** Paise → ₹ display string. Integer paise everywhere; format at the edge. */
export function rupees(paise?: number | null): string {
  if (paise == null) return "—";
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: paise % 100 === 0 ? 0 : 2,
  }).format(paise / 100);
}

// ------------------------------------------------- session handoff /buy → /contract
//
// GET /api/contracts/{id} returns {contract, promises, entitlements} but not
// the MerchantOffer snapshot or evaluator rationale, so the selection context
// rides through sessionStorage at select time. The contract page degrades
// gracefully when this is absent (cold refresh, new tab).

export const BRIEF_SESSION_KEY = "dante.brief.raw";

/**
 * Payment-order snapshot cache (per contract). RazorpayPanel's sandbox
 * detection used to derive from in-memory orderInfo only — a refresh lost it.
 * We persist mode + checkout_config here at order time AND re-derive from the
 * contract's own fields (sandbox_mode + razorpay_order_id) on load, so the
 * simulate affordance survives any reload path (#3). Live checkout keys are
 * recovered through GET /api/contracts/{id}/payment-order instead.
 */
function orderSnapshotKey(contractId: string): string {
  return `dante.contract.${contractId}.order`;
}

function persistOrderSnapshot(
  contractId: string,
  order: PaymentOrderResponse,
): void {
  try {
    window.sessionStorage.setItem(orderSnapshotKey(contractId), JSON.stringify(order));
  } catch {
    /* storage unavailable — contract fields still re-derive the essentials */
  }
}

/** Read back the cached payment-order snapshot; null when absent/corrupt. */
export function readOrderSnapshot(
  contractId: string,
): PaymentOrderResponse | null {
  try {
    const raw = window.sessionStorage.getItem(orderSnapshotKey(contractId));
    return raw ? (JSON.parse(raw) as PaymentOrderResponse) : null;
  } catch {
    return null;
  }
}

/**
 * Re-derive a minimal PaymentOrderResponse from contract fields when no
 * snapshot exists. `mode` is authoritative from contract.sandbox_mode; the
 * checkout config carries what the sandbox simulate path needs. key_id is
 * unknown from this shape, so live callers must use the server read-back path
 * in loadContract.
 */
function deriveOrderFromContract(c: DanteContract): PaymentOrderResponse | null {
  if (
    !c.razorpay_order_id ||
    (c.status !== "PAYMENT_ORDER_CREATED" && c.status !== "PAYMENT_PENDING")
  ) {
    return null;
  }
  return {
    mode: c.sandbox_mode ? "sandbox" : "live-test-mode",
    razorpay_order: {},
    checkout_config: {
      key_id: "",
      order_id: String(c.razorpay_order_id),
      amount_paise: c.amount_paise ?? 0,
      currency: "INR",
    },
  };
}

export type OfferMemoItem = {
  item_id: string;
  label: string;
  quantity: number;
  offer: MerchantOffer;
  explanation?: string;
  softScores?: SoftScore[];
};

export type OfferMemo = {
  offer: MerchantOffer;
  items?: OfferMemoItem[];
  explanation?: string;
  softScores?: SoftScore[];
};

function offerMemoKey(contractId: string): string {
  return `dante.contract.${contractId}.offer`;
}

/** Called by /buy immediately after select-offer returns a contract id. */
export function rememberOfferSelection(
  contractId: string,
  memo: OfferMemo,
): void {
  try {
    window.sessionStorage.setItem(offerMemoKey(contractId), JSON.stringify(memo));
  } catch {
    /* storage unavailable — contract page degrades gracefully */
  }
}

export function rememberBuyerBrief(rawText: string): void {
  try {
    window.sessionStorage.setItem(BRIEF_SESSION_KEY, rawText);
  } catch {
    /* non-fatal */
  }
}

/** Read back the offer memo on the contract page; null when absent. */
export function readOfferSelection(contractId: string): OfferMemo | null {
  try {
    const raw = window.sessionStorage.getItem(offerMemoKey(contractId));
    return raw ? (JSON.parse(raw) as OfferMemo) : null;
  } catch {
    return null;
  }
}
