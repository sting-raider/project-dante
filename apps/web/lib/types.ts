/**
 * Frontend mirrors of the frozen domain models (apps/api/.../domain/types.py).
 * Kept structural — pages consume these via lib/api.ts. Field names match the
 * serialized Pydantic shapes exactly; treat as read-only.
 */

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

export interface BuyerIntent {
  id: string;
  raw_text: string;
  hard_constraints: { key: string; op: string; value: unknown; critical: boolean }[];
  soft_preferences?: { key: string; weight?: number; value?: unknown }[];
  items?: {
    id: string;
    label: string;
    hard_constraints: { key: string; op: string; value: unknown; critical: boolean }[];
    soft_preferences?: { key: string; weight?: number; value?: unknown }[];
    max_price_paise?: number | null;
    quantity?: number;
  }[];
  max_total_amount_paise?: number | null;
  autonomous_spend_limit_paise?: number | null;
  substitutions_allowed?: boolean;
  created_at?: string | null;
}

export interface MerchantOffer {
  id: string;
  merchant_id?: string;
  sku: string;
  title: string;
  variant?: Record<string, string>;
  unit_amount_paise: number;
  currency?: "INR";
  inventory?: number;
  category?: string | null;
  brand?: string | null;
  delivery_promise?: {
    min_days?: number | null;
    max_days?: number | null;
    promised_by_date?: string | null;
    service?: string | null;
  };
  terms?: {
    warranty_type?: "manufacturer" | "seller" | "none" | "unknown";
    warranty_duration_months?: number | null;
    warranty_region?: string | null;
    return_window_days?: number | null;
    replacement_window_days?: number | null;
    condition?: string;
    region?: string | null;
  };
  attributes?: Record<string, unknown>;
}

export interface Promise_ {
  id: string;
  contract_id?: string | null;
  key: string;
  value: unknown;
  normalized_value?: unknown;
  extraction_method?: "structured" | "agent_extracted" | "derived";
  verification_status?: "verified" | "merchant_asserted" | "unverified";
  confidence?: number | null;
  material_to_intent?: boolean;
  material_reason?: string | null;
}

export interface DanteContract {
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
  status: ContractStatus;
  amount_paise?: number | null;
  contract_hash?: string | null;
  promise_set_hash?: string | null;
  offer_hash?: string | null;
  razorpay_order_id?: string | null;
  razorpay_payment_id?: string | null;
  sandbox_mode?: boolean;
  created_at?: string | null;
  frozen_at?: string | null;
}

export interface DomainEvent {
  id: string;
  aggregate_type: string;
  aggregate_id: string;
  event_type: string;
  category: "Agent" | "Money" | "Merchant" | "Fulfillment" | "Policy" | "Evidence" | "System";
  payload?: Record<string, unknown>;
  synthetic?: boolean;
  scenario_id?: string | null;
  occurred_at?: string | null;
  ts?: string | null;
}

export const TIMELINE_CATEGORIES = [
  "Agent",
  "Money",
  "Merchant",
  "Fulfillment",
  "Policy",
  "Evidence",
] as const;

export type TimelineCategory = (typeof TIMELINE_CATEGORIES)[number];
