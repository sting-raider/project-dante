"""Project Dante — frozen domain types (P0 contract).

These models are the integration contract for all workstreams. Field names,
literals, and semantics are FROZEN for Wave 1; additive-only changes allowed
with a BREAKING flag in the handoff doc.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------- literals

SourceTypeEnum = Literal[
    "catalog_json",
    "product_page",
    "terms",
    "checkout_offer",
    "shipment_event",
    "delivery_event",
    "device_metadata",
    "razorpay_webhook",
    "merchant_api",
]

TrustedLevel = Literal[
    "structured_verified",  # from merchant structured data (highest)
    "merchant_asserted",  # from unstructured merchant text
    "synthetic",  # demo simulation events
    "external",  # third-party (e.g. manufacturer, payment provider)
]

ExtractionMethod = Literal["structured", "agent_extracted", "derived"]
VerificationStatus = Literal["verified", "merchant_asserted", "unverified"]

BreachSeverity = Literal["informational", "minor", "material", "critical"]
RemedyType = Literal[
    "replacement",
    "refund_full",
    "refund_partial",
    "delivery_compensation",
    "no_action",
]
ExecutionMode = Literal["merchant_api", "razorpay_refund", "external_manual", "synthetic"]

MoneyActionType = Literal["create_order", "refund_full", "refund_partial"]

PolicyDecisionLiteral = Literal["ALLOW", "REQUIRE_APPROVAL", "DENY"]

IssuerType = Literal["merchant", "manufacturer", "payment_provider", "promotion"]

EntitlementType = Literal[
    "return",
    "replacement",
    "refund",
    "partial_refund",
    "warranty",
    "delivery_compensation",
    "buyer_protection",
]

ContractStatus = Literal[
    "DRAFT",
    "INTENT_READY",
    "OFFER_SELECTED",
    "CONTRACT_FROZEN",
    "AWAITING_BUYER_AUTH",
    "PAYMENT_ORDER_CREATED",
    "PAYMENT_PENDING",
    "PAID",
    "FULFILLING",
    "DELIVERED",
    "VERIFYING",
    "SATISFIED",
    "BREACH_DETECTED",
    "REMEDY_PLANNING",
    "AWAITING_REMEDY_APPROVAL",
    "REMEDY_EXECUTING",
    "REMEDIATED",
    "CANCELLED",
    "FAILED",
]

# ---------------------------------------------------------------- base


class DanteModel(BaseModel):
    """Base: forbid extra fields so agent outputs can't smuggle payloads."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- intent


class Constraint(DanteModel):
    key: str  # e.g. "category", "max_price_paise", "warranty.type", "delivery_deadline"
    op: Literal["eq", "lte", "gte", "lt", "gt", "in", "contains"] = "eq"
    value: Any
    critical: bool = True  # hard constraint if True; soft preference if False


class Preference(DanteModel):
    key: str
    weight: float = 1.0  # 0..1
    value: Any = None


class OutcomeSpec(DanteModel):
    description: str
    keys: list[str] = Field(default_factory=list)  # observed-fact keys that matter


class BuyerIntent(DanteModel):
    id: str
    raw_text: str
    hard_constraints: list[Constraint] = Field(default_factory=list)
    soft_preferences: list[Preference] = Field(default_factory=list)
    max_total_amount_paise: int | None = None
    autonomous_spend_limit_paise: int | None = None
    substitutions_allowed: bool = False
    desired_outcome: OutcomeSpec | None = None
    created_at: str | None = None
    compiler_version: str = "v0"


# ---------------------------------------------------------------- offer


class DeliveryPromise(DanteModel):
    min_days: int | None = None
    max_days: int | None = None
    promised_by_date: str | None = None  # ISO date
    service: str | None = None


class OfferTerms(DanteModel):
    warranty_type: Literal["manufacturer", "seller", "none", "unknown"] = "unknown"
    warranty_duration_months: int | None = None
    warranty_region: str | None = None  # ISO country, e.g. "IN"
    return_window_days: int | None = None
    replacement_window_days: int | None = None
    condition: Literal["new", "refurbished", "used", "unknown"] = "new"
    region: str | None = None  # SKU/stock region, e.g. "IN", "AE"
    notes: str | None = None


class MerchantOffer(DanteModel):
    id: str
    merchant_id: str = "aster-electronics"
    sku: str
    title: str
    variant: dict[str, str] = Field(default_factory=dict)
    unit_amount_paise: int
    currency: Literal["INR"] = "INR"
    inventory: int = 0
    delivery_promise: DeliveryPromise = Field(default_factory=DeliveryPromise)
    terms: OfferTerms = Field(default_factory=OfferTerms)
    expires_at: str | None = None
    source_snapshot_id: str | None = None
    category: str | None = None
    brand: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------- evidence


class EvidenceArtifact(DanteModel):
    id: str
    contract_id: str | None = None
    source_type: SourceTypeEnum
    raw_payload_ref: str
    sha256: str
    observed_at: str
    trusted_level: TrustedLevel
    synthetic: bool = False
    scenario_id: str | None = None
    excerpt: str | None = None  # human-readable snippet for UI


# ---------------------------------------------------------------- promise


class Promise(DanteModel):
    id: str
    contract_id: str | None = None
    key: str  # dotted path, e.g. "warranty.type"
    value: Any
    normalized_value: Any = None
    source_artifact_id: str | None = None
    extraction_method: ExtractionMethod = "structured"
    verification_status: VerificationStatus = "unverified"
    confidence: float | None = None
    material_to_intent: bool = False
    material_reason: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None


# ---------------------------------------------------------------- contract


class AuthorityEnvelope(DanteModel):
    """Binds buyer authorization to the exact frozen transaction."""

    max_amount_paise: int
    currency: Literal["INR"] = "INR"
    authorized_at: str | None = None
    authorized_by: str = "demo-buyer"
    scope: Literal["single_purchase"] = "single_purchase"
    contract_hash_at_authorization: str | None = None


class DanteContract(DanteModel):
    id: str
    display_code: str | None = None  # e.g. COV-1842
    intent_id: str
    offer_id: str
    promise_ids: list[str] = Field(default_factory=list)
    entitlement_ids: list[str] = Field(default_factory=list)

    buyer_authority: AuthorityEnvelope | None = None

    offer_hash: str | None = None
    promise_set_hash: str | None = None
    contract_hash: str | None = None

    razorpay_order_id: str | None = None
    razorpay_payment_id: str | None = None
    amount_paise: int | None = None

    status: ContractStatus = "DRAFT"
    created_at: str | None = None
    frozen_at: str | None = None
    sandbox_mode: bool = False  # true => Razorpay SANDBOX adapter (no real keys)


class ObservedFact(DanteModel):
    id: str
    contract_id: str
    key: str
    value: Any
    source_artifact_id: str | None = None
    observed_at: str | None = None
    synthetic: bool = False
    scenario_id: str | None = None


# ---------------------------------------------------------------- breach


class Breach(DanteModel):
    id: str
    contract_id: str
    promise_id: str
    observed_fact_id: str
    severity: BreachSeverity = "material"
    reason_code: str
    explanation: str
    detected_at: str | None = None


# ---------------------------------------------------------------- rights graph


class Predicate(DanteModel):
    key: str
    op: Literal["eq", "neq", "exists", "gt", "gte", "lt", "lte", "in", "truthy"] = "eq"
    value: Any = None


class Entitlement(DanteModel):
    id: str
    contract_id: str | None = None

    issuer_type: IssuerType = "merchant"
    issuer_name: str = "Aster Electronics"

    type: EntitlementType = "return"

    activates_when: list[Predicate] = Field(default_factory=list)
    expires_at: str | None = None
    required_evidence_types: list[str] = Field(default_factory=list)

    remedy_value_paise: int | None = None
    estimated_resolution_hours: float | None = None

    requires: list[str] = Field(default_factory=list)  # prerequisite entitlement ids
    blocks: list[str] = Field(default_factory=list)
    fallback_to: list[str] = Field(default_factory=list)

    execution_mode: ExecutionMode = "razorpay_refund"

    status: Literal[
        "dormant", "eligible", "active", "consumed", "expired", "invalid", "blocked"
    ] = "dormant"


# ---------------------------------------------------------------- remedies / money


class RemedyProposal(DanteModel):
    id: str
    breach_id: str | None = None
    entitlement_id: str | None = None
    contract_id: str | None = None

    remedy_type: RemedyType = "refund_full"
    amount_paise: int | None = None

    expected_buyer_value: float = 0.0
    estimated_time_hours: float = 0.0
    inconvenience_score: float = 0.0
    confidence: float = 0.0

    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str = ""
    rejected_reason: str | None = None  # set when this candidate was not chosen
    rank: int | None = None


class MoneyActionProposal(DanteModel):
    id: str
    type: MoneyActionType
    amount_paise: int
    currency: Literal["INR"] = "INR"

    razorpay_payment_id: str | None = None
    razorpay_order_id: str | None = None

    contract_id: str
    remedy_proposal_id: str | None = None

    reason_code: str
    human_explanation: str = ""

    evidence_ids: list[str] = Field(default_factory=list)
    policy_snapshot_hash: str = ""
    idempotency_key: str

    status: Literal[
        "proposed",
        "allowed",
        "approval_required",
        "denied",
        "executing",
        "executed",
        "failed",
    ] = "proposed"
    result_ref: str | None = None  # razorpay refund/order id once executed


class PolicyDecision(DanteModel):
    decision: PolicyDecisionLiteral
    policy_ids: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    explanation: str = ""
    evaluated_at: str | None = None
    policy_snapshot_hash: str = ""
