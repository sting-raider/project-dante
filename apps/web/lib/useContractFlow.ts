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

// ---------------------------------------------------------------- API base

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://localhost:8000";

async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`POST ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------- domain types
// Mirrors project_dante.domain.types — keep in sync; additive only.

export type Constraint = {
  key: string;
  op: "eq" | "lte" | "gte" | "lt" | "gt" | "in" | "contains";
  value: unknown;
  critical: boolean;
};

export type Preference = { key: string; weight: number; value: unknown };

export type BuyerIntent = {
  id: string;
  raw_text: string;
  hard_constraints: Constraint[];
  soft_preferences: Preference[];
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
};

export type HardFailure = { key: string; op: string; expected: unknown; actual: unknown };

export type SoftScore = { key: string; weight: number; score: number; note: string };

export type OfferEvaluation = {
  feasible: boolean;
  hard_failures: HardFailure[];
  soft_scores: SoftScore[];
  explanation: string;
};

export type SearchResult = { offer: MerchantOffer; evaluation: OfferEvaluation };

export type Promise_ = {
  id: string;
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
};

// Razorpay Standard Checkout handler payload (subset we consume).
export type RazorpayHandlerResponse = {
  razorpay_order_id: string;
  razorpay_payment_id: string;
  razorpay_signature: string;
};

declare global {
  interface Window {
    Razorpay?: new (options: Record<string, unknown>) => { open: () => void };
  }
}

// ---------------------------------------------------------------- flow states

export type FlowPhase =
  // /buy pipeline
  | "idle"
  | "compiling"
  | "searching"
  | "shortlist" // results shown, awaiting buyer selection
  | "selecting"
  | "navigating"
  // contract pipeline
  | "awaiting_authorization"
  | "creating_order"
  | "checkout_ready" // live-test-mode: Razorpay JS open / awaiting user
  | "sandbox_ready" // sandbox: awaiting simulated capture
  | "payment_pending" // window closed / verify sent; waiting on server truth
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
  selecting: "Evaluating offers…",
  shortlist: "Offers evaluated — select one to freeze its promises.",
  navigating: "Freezing promises…",
  awaiting_authorization: "Contract frozen. Awaiting your authorization.",
  creating_order: "Creating Razorpay order…",
  checkout_ready: "Checkout open — complete the test payment.",
  sandbox_ready: "SANDBOX adapter active — simulate the capture below.",
  payment_pending: "Payment submitted — confirming against server truth…",
  paid: "PAID — verified by server-side webhook truth.",
};

const POLL_INTERVAL_MS = 2000;

export type UseContractFlow = ReturnType<typeof useContractFlow>;

export function useContractFlow() {
  // ---- /buy state
  const [phase, setPhase] = useState<FlowPhase>("idle");
  const [intent, setIntent] = useState<BuyerIntent | null>(null);
  const [engine, setEngine] = useState<string | null>(null);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [selectedOfferId, setSelectedOfferId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // ---- contract state
  const [contractId, setContractId] = useState<string | null>(null);
  const [contract, setContract] = useState<DanteContract | null>(null);
  const [promises, setPromises] = useState<Promise_[]>([]);
  const [entitlements, setEntitlements] = useState<ContractDetail["entitlements"]>([]);
  const [orderInfo, setOrderInfo] = useState<PaymentOrderResponse | null>(null);
  const [verifyNote, setVerifyNote] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [pollingActive, setPollingActive] = useState(false);
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      stopPolling();
    };
  }, []);

  const fail = useCallback((p: FlowPhase, e: unknown) => {
    if (!mountedRef.current) return;
    setPhase(p);
    setError(e instanceof Error ? e.message : String(e));
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

  const refreshContract = useCallback(
    async (id: string): Promise<DanteContract | null> => {
      try {
        const detail = await apiGet<ContractDetail>(`/api/contracts/${id}`);
        if (!mountedRef.current) return detail.contract;
        setContract(detail.contract);
        setPromises(detail.promises ?? []);
        setEntitlements(detail.entitlements ?? []);
        return detail.contract;
      } catch (e) {
        if (mountedRef.current) {
          setPhase("error_poll");
          setError(e instanceof Error ? e.message : String(e));
        }
        return null;
      }
    },
    [],
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
      const c = await refreshContract(id);
      if (!c) return false; // refreshContract already set error phase
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
    [refreshContract, startPollingUntilResolved],
  );

  useEffect(() => () => stopPolling(), [stopPolling]);

  // ---------------------------------------------------------- /buy actions

  const compileAndSearch = useCallback(
    async (rawText: string) => {
      setError(null);
      setResults([]);
      setSelectedOfferId(null);
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
          engine: string;
        }>(`/api/intents/${compiled.intent.id}/search`);
        if (!mountedRef.current) return;
        setIntent(searched.intent);
        setResults(searched.results ?? []);
        setPhase("shortlist");
      } catch (e) {
        fail(stage === "searching" ? "error_search" : "error_compile", e);
      }
    },
    [fail],
  );

  const selectOffer = useCallback(
    async (offerId: string): Promise<string | null> => {
      if (!intent) return null;
      setError(null);
      setSelectedOfferId(offerId);
      setPhase("selecting");
      try {
        const r = await apiPost<{
          contract: DanteContract;
          promises: Promise_[];
          evidence: EvidenceArtifact[];
        }>(`/api/intents/${intent.id}/select-offer`, { offer_id: offerId });
        if (!mountedRef.current) return null;
        setPhase("navigating");
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
      setPhase("creating_order");
      try {
        const order = await apiPost<PaymentOrderResponse>(
          `/api/contracts/${id}/payment-order`,
        );
        if (!mountedRef.current) return null;
        setOrderInfo(order);
        setPhase(order.mode === "sandbox" ? "sandbox_ready" : "checkout_ready");
        return order;
      } catch (e) {
        fail("error_order", e);
        return null;
      }
    },
    [fail],
  );

  /**
   * Live-test-mode path: verify the Standard Checkout handler payload
   * server-side. Client success alone is NOT treated as final truth —
   * the caller then polls until the webhook confirms PAID.
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
   */
  const simulateSandboxCapture = useCallback(
    async (id: string, orderId: string): Promise<boolean> => {
      try {
        await apiPost("/api/demo/razorpay/simulate-event", {
          event_type: "payment.captured",
          order_id: orderId,
          payment_id: `pay_sim_${orderId.slice(-8)}`,
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

  /** Manual re-check button (window-closed fallback). */
  const recheckStatus = useCallback(
    async (id: string) => {
      const c = await refreshContract(id);
      if (c && c.status === "PAID") {
        stopPolling();
        setPhase("paid");
      }
    },
    [refreshContract, stopPolling],
  );

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
    selectedOfferId,
    error,
    contractId,
    setContractId,
    contract,
    promises,
    entitlements,
    orderInfo,
    verifyNote,
    // derived
    isBusy: ["compiling", "searching", "selecting", "creating_order"].includes(phase),
    // /buy
    compileAndSearch,
    selectOffer,
    resetError,
    // contract
    loadContract,
    refreshContract,
    authorize,
    createPaymentOrder,
    verifyClient,
    simulateSandboxCapture,
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

export type OfferMemo = {
  offer: MerchantOffer;
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

