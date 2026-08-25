/**
 * Domain + API-response types for Agent I surfaces (breach / rights /
 * remedy / timeline / audit / merchant / demo). Field names mirror
 * apps/api/project_dante/domain/types.py and docs/API_CONTRACT.md exactly.
 *
 * lib/types.ts (Agent G) keeps the structural mirrors for /buy and the
 * contract page; this module extends coverage for the rights-side records.
 * Read-only mirrors — additive changes only.
 */

import type { ContractStatus } from "./types";

/* ------------------------------------------------------------ domain */

export type DanteEvent = {
  id: string;
  aggregate_type: string;
  aggregate_id: string;
  event_type: string;
  category: string;
  event_version?: number;
  payload?: Record<string, unknown>;
  correlation_id?: string | null;
  causation_id?: string | null;
  idempotency_key?: string | null;
  trace_id?: string | null;
  synthetic?: boolean;
  scenario_id?: string | null;
  created_at?: string | null;
};

export type AuthorityEnvelope = {
  max_amount_paise?: number;
  currency?: string;
  authorized_at?: string | null;
  authorized_by?: string;
  scope?: string;
  contract_hash_at_authorization?: string | null;
};

export type DanteContractFull = {
  id: string;
  display_code?: string | null;
  intent_id: string;
  offer_id: string;
  promise_ids?: string[];
  entitlement_ids?: string[];
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
  sandbox_mode?: boolean;
};

export type PromiseRec = {
  id: string;
  contract_id?: string | null;
  key: string;
  value: unknown;
  normalized_value?: unknown;
  source_artifact_id?: string | null;
  extraction_method?: string;
  verification_status?: string;
  confidence?: number | null;
  material_to_intent?: boolean;
  material_reason?: string | null;
  valid_from?: string | null;
  valid_until?: string | null;
};

export type EvidenceArtifactRec = {
  id: string;
  contract_id?: string | null;
  source_type: string;
  raw_payload_ref?: string;
  sha256: string;
  observed_at?: string;
  trusted_level: "structured_verified" | "merchant_asserted" | "synthetic" | "external";
  synthetic?: boolean;
  scenario_id?: string | null;
  excerpt?: string | null;
};

export type Breach = {
  id: string;
  contract_id: string;
  promise_id: string;
  observed_fact_id: string;
  severity: "informational" | "minor" | "material" | "critical";
  reason_code: string;
  explanation: string;
  detected_at?: string | null;
};

export type EntitlementStatus =
  | "dormant"
  | "eligible"
  | "active"
  | "consumed"
  | "expired"
  | "invalid"
  | "blocked";

export type Entitlement = {
  id: string;
  contract_id?: string | null;
  issuer_type?: string;
  issuer_name?: string;
  /** Stable human slug when the engine provides one, else the raw id. */
  slug?: string;
  type: string;
  activates_when?: { key: string; op: string; value?: unknown }[];
  expires_at?: string | null;
  required_evidence_types?: string[];
  remedy_value_paise?: number | null;
  estimated_resolution_hours?: number | null;
  requires?: string[];
  blocks?: string[];
  fallback_to?: string[];
  execution_mode?: string;
  status: EntitlementStatus;
  /** Engine-private activation hints (surfaced by /rights graph edges). */
  _activation_reasons?: string[];
};

export type RemedyProposal = {
  id: string;
  breach_id?: string | null;
  entitlement_id?: string | null;
  contract_id?: string | null;
  remedy_type: string;
  amount_paise?: number | null;
  expected_buyer_value?: number;
  estimated_time_hours?: number;
  inconvenience_score?: number;
  confidence?: number;
  evidence_ids?: string[];
  explanation?: string;
  rejected_reason?: string | null;
  rank?: number | null;
};

export type PolicyDecision = {
  decision: "ALLOW" | "REQUIRE_APPROVAL" | "DENY";
  policy_ids?: string[];
  reason_codes?: string[];
  explanation?: string;
  evaluated_at?: string | null;
  policy_snapshot_hash?: string;
};

export type MoneyAction = {
  id: string;
  type: string;
  amount_paise: number;
  currency?: string;
  razorpay_payment_id?: string | null;
  razorpay_order_id?: string | null;
  contract_id: string;
  remedy_proposal_id?: string | null;
  reason_code: string;
  human_explanation?: string;
  evidence_ids?: string[];
  policy_snapshot_hash?: string;
  idempotency_key: string;
  status:
    | "proposed"
    | "allowed"
    | "approval_required"
    | "denied"
    | "executing"
    | "executed"
    | "failed";
  result_ref?: string | null;
};

/* ------------------------------------------------------- responses */

export type ContractResponse = {
  contract: DanteContractFull;
  promises: PromiseRec[];
  entitlements: Entitlement[];
};

export type TimelineResponse = { events: DanteEvent[] };

export type VerifyResponse = {
  breaches: Breach[];
  status: ContractStatus;
  satisfied: boolean;
  status_target?: string;
  unobserved_material_keys?: string[];
  checked_promise_count?: number;
};

export type RightsResponse = {
  graph: {
    nodes: import("@/components/rights-graph/RightsGraph").GraphNode[];
    edges: {
      source: string;
      target: string;
      /** Backend field name is `kind`; normalized to `type` for the graph. */
      kind?: string;
      type?: string;
      [k: string]: unknown;
    }[];
  };
  entitlements: Entitlement[];
};

export type RemediesResponse = { proposals: RemedyProposal[] };

export type PolicyResponse = {
  decision: PolicyDecision | null;
  money_action: MoneyAction;
};

export type ApproveResponse = {
  money_action: MoneyAction | null;
  refund?: Record<string, unknown> | null;
};

export type ExecuteResponse = {
  money_action: MoneyAction | null;
  refund: Record<string, unknown> | null;
  decision?: PolicyDecision | null;
  note?: string;
  error?: string;
};

export type MerchantAnalytics = {
  total_products?: number;
  warranty_metadata_coverage?: number;
  machine_readable_return_policy?: number;
  evaluated_intents?: number;
  ai_transactable_rate?: number;
  /** constraint key -> count (Agent F ships a plain dict) */
  blocker_distribution?: Record<string, number>;
  [k: string]: unknown;
};

/* ------------------------------------------------------- demo */

export type DemoResetResponse = { reset?: boolean; products?: number; [k: string]: unknown };

export type DemoDeliverResponse = {
  observed_facts?: { key: string; value: unknown; synthetic?: boolean }[];
  breaches?: Breach[];
  status?: string | null;
  verification_error?: string | null;
  synthetic?: boolean;
  [k: string]: unknown;
};

/** Terminal lifecycle states — polling stops here. */
export const TERMINAL_STATUSES: readonly ContractStatus[] = [
  "SATISFIED",
  "REMEDIATED",
  "CANCELLED",
  "FAILED",
] as const;

export function isTerminal(status: string | null | undefined): boolean {
  return !!status && (TERMINAL_STATUSES as readonly string[]).includes(status);
}

/** Normalize backend `kind` edges into the graph component's `type`. */
export function normalizeEdges(
  edges: RightsResponse["graph"]["edges"]
): { source: string; target: string; type: string }[] {
  return edges.map((e) => ({
    source: e.source,
    target: e.target,
    type: e.type ?? e.kind ?? "SUPPORTED_BY",
  }));
}
