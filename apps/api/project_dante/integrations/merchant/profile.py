"""Machine-readable merchant profile + honest order status (plan §10).

The AI buyer transacts against EXPLICIT machine-readable statements, never
against scraped prose:

    build_merchant_profile()  -> capability flags + catalog metadata stats
    order_status(contract_id) -> fulfillment stage derived from stored facts/events

Every capability flag is COMPUTED, never asserted: ``structured_warranty`` is
true because a measurable share of the committed fixture carries typed warranty
metadata; ``razorpay_checkout`` is true because a gateway client actually
constructs; ``post_purchase_resolution`` is true because the rights engine
actually imports. If the underlying reality disappeared, the flags would flip
false on their own — there is no hardcoded marketing here.

``order_status`` projects ONLY stored records (facts, audit events, the contract
row) into a coarse stage ladder; it invents nothing and labels synthetic
observations as synthetic.
"""

from __future__ import annotations

from typing import Any

from project_dante.db.store import STORE
from project_dante.domain.events import LOG
from project_dante.integrations.merchant import service
from project_dante.integrations.merchant.catalog_loader import (
    catalog_merchant,
    load_catalog,
)
from project_dante.settings import get_settings

MERCHANT_ID = service.MERCHANT_ID
MERCHANT_NAME = "Aster Electronics"

# Event types that constitute server-confirmed money truth (webhook path).
_PAYMENT_CONFIRMING_EVENTS = {"PAYMENT_VERIFIED_SERVER", "RAZORPAY_PAYMENT_CAPTURED"}

# Lifecycle statuses that imply captured money even before any fact lands.
_PAID_LIFECYCLE = {"PAID", "FULFILLING", "VERIFYING", "DELIVERED", "SATISFIED"}


# ------------------------------------------------------------------ coverage


def _share(total: int, matching: int) -> float:
    return round(matching / total, 4) if total else 0.0


def _has_structured_warranty(product: dict[str, Any]) -> bool:
    return (product.get("terms") or {}).get("warranty_type") not in (None, "unknown")


def _has_delivery_promise(product: dict[str, Any]) -> bool:
    dp = product.get("delivery_promise") or {}
    return isinstance(dp.get("max_days"), int) or bool(dp.get("promised_by_date"))


def _has_return_policy(product: dict[str, Any]) -> bool:
    return isinstance((product.get("terms") or {}).get("return_window_days"), int)


def catalog_stats() -> dict[str, Any]:
    """Metadata coverage measured straight from the committed fixture."""
    catalog = load_catalog()
    total = len(catalog)
    return {
        "total_skus": total,
        "warranty_metadata_coverage": _share(
            total, sum(1 for p in catalog if _has_structured_warranty(p))
        ),
        "delivery_promise_coverage": _share(
            total, sum(1 for p in catalog if _has_delivery_promise(p))
        ),
        "return_policy_coverage": _share(
            total, sum(1 for p in catalog if _has_return_policy(p))
        ),
    }


# -------------------------------------------------------------- capabilities


def _capability_catalog_search() -> bool:
    """True iff a live query over the fixture actually returns offers."""
    try:
        return bool(service.search_catalog(limit=1))
    except Exception:  # noqa: BLE001 - a broken catalog is an absent capability
        return False


def _capability_post_purchase_resolution() -> bool:
    """True iff the Purchase Rights Graph engine is importable and wired."""
    try:
        from project_dante.domain.rights import engine
    except Exception:  # noqa: BLE001 - missing engine is an absent capability
        return False
    return callable(getattr(engine, "build_rights_graph", None))


def _gateway_block() -> dict[str, Any]:
    """Checkout availability + active gateway mode, probed for real."""
    mode = get_settings().razorpay_mode
    try:
        from project_dante.integrations.razorpay.client import get_client

        get_client()  # constructing a client performs no network I/O
        available = True
    except Exception:  # noqa: BLE001 - unusable gateway is an absent capability
        available = False
    return {"razorpay_checkout": available, "mode": mode}


# ------------------------------------------------------------------- profile


def build_merchant_profile() -> dict[str, Any]:
    """The machine-readable statement of what this merchant can commit to.

    Shape (plan §10): identity, capability flags, catalog metadata stats,
    active gateway mode, and the endpoints an agent should call instead of
    parsing human-facing pages.
    """
    stats = catalog_stats()
    gateway = _gateway_block()
    header = catalog_merchant()

    return {
        "merchant_id": header.get("merchant_id") or MERCHANT_ID,
        "name": header.get("name") or MERCHANT_NAME,
        "currency": header.get("currency") or "INR",
        "catalog_version": header.get("catalog_version"),
        "capabilities": {
            "catalog_search": _capability_catalog_search(),
            "structured_warranty": stats["warranty_metadata_coverage"] > 0,
            "delivery_promises": stats["delivery_promise_coverage"] > 0,
            "returns": stats["return_policy_coverage"] > 0,
            "razorpay_checkout": gateway["razorpay_checkout"],
            "post_purchase_resolution": _capability_post_purchase_resolution(),
        },
        "catalog_stats": stats,
        "gateway": {"mode": gateway["mode"]},
        "machine_endpoints": {
            "profile": "/api/merchant/profile",
            "catalog_search": "/api/merchant/catalog/search",
            "product": "/api/merchant/products/{sku}",
            "offer_freeze": "/api/merchant/offers/freeze",
            "order_status": "/api/merchant/orders/{contract_id}/status",
        },
    }


# -------------------------------------------------------------- order status


def _latest_by_key(facts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Latest observation per fact key (records arrive appended, unordered)."""
    out: dict[str, dict[str, Any]] = {}
    for fact in sorted(facts, key=lambda f: f.get("observed_at") or ""):
        out[fact["key"]] = fact
    return out


def _derive_stage(
    contract: dict[str, Any],
    facts_by_key: dict[str, dict[str, Any]],
    event_types: set[str],
) -> str:
    """Coarse fulfillment ladder, highest observed stage wins.

    refunded > delivered > shipped > paid > payment_pending > awaiting_payment.
    Each rung is backed by a concrete stored record, in priority order:
    observed facts, then audit events, then the contract row itself.
    """
    if (
        contract.get("refund_reconciled")
        and contract.get("refund_status") == "fully_refunded"
    ):
        return "refunded"
    if "delivery.delivered_date" in facts_by_key:
        return "delivered"
    if facts_by_key.get("shipment.status", {}).get("value") == "shipped":
        return "shipped"
    if "FULFILLMENT_DELIVERED" in event_types:
        return "delivered"
    if "FULFILLMENT_SHIPPED" in event_types:
        return "shipped"
    if (
        event_types & _PAYMENT_CONFIRMING_EVENTS
        or contract.get("razorpay_payment_id")
        or contract.get("status") in _PAID_LIFECYCLE
    ):
        return "paid"
    if contract.get("razorpay_order_id"):
        return "payment_pending"
    return "awaiting_payment"


def order_status(contract_id: str) -> dict[str, Any]:
    """Honest fulfillment projection for one contract.

    Raises KeyError when the id is unknown or not a contract record. Every
    field traces to a stored fact, a logged event, or the contract record;
    synthetic observations stay labeled as such.
    """
    contract = STORE.get(contract_id)
    if contract is None or contract.get("_type") != "contract":
        raise KeyError(f"Unknown contract: {contract_id}")

    facts = STORE.find("fact", contract_id=contract_id)
    facts_by_key = _latest_by_key(facts)
    events = sorted(LOG.for_aggregate(contract_id), key=lambda e: e.get("created_at") or "")
    event_types: set[str] = set()
    for event in events:
        event_type = event.get("event_type")
        if isinstance(event_type, str):
            event_types.add(event_type)

    shipped_fact = facts_by_key.get("shipment.status", {}).get("value") == "shipped"
    delivered_fact = facts_by_key.get("delivery.delivered_date")
    tracking_ids = [
        e.get("payload", {}).get("tracking_id")
        for e in events
        if e.get("event_type") == "FULFILLMENT_SHIPPED" and e.get("payload")
    ]

    promised = {
        p["key"]: p.get("value")
        for p in STORE.find("promise", contract_id=contract_id)
        if p.get("key")
    }

    timestamps = [
        ts
        for ts in [f.get("observed_at") for f in facts] + [e.get("created_at") for e in events]
        if ts
    ]

    return {
        "contract_id": contract_id,
        "status": _derive_stage(contract, facts_by_key, event_types),
        "lifecycle_status": contract.get("status"),
        "offer_sku": contract.get("offer_sku"),
        "amount_paise": contract.get("amount_paise"),
        "refund_status": contract.get("refund_status"),
        "refunded_amount_paise": contract.get("refunded_amount_paise", 0),
        "refund_reconciled": bool(contract.get("refund_reconciled")),
        "refund_reconciled_at": contract.get("refund_reconciled_at"),
        "fulfillment": {
            "shipped": shipped_fact or "FULFILLMENT_SHIPPED" in event_types,
            "carrier": facts_by_key.get("shipment.carrier", {}).get("value"),
            "tracking_id": tracking_ids[-1] if tracking_ids else None,
            "delivered": bool(delivered_fact) or "FULFILLMENT_DELIVERED" in event_types,
            "delivered_date": (delivered_fact or {}).get("value"),
            "days_late": facts_by_key.get("delivery.days_late", {}).get("value"),
        },
        "replacement_available": facts_by_key.get("replacement.available", {}).get("value"),
        "breach_count": len(STORE.find("breach", contract_id=contract_id)),
        "promised": promised,
        "observed": {key: fact.get("value") for key, fact in facts_by_key.items()},
        "fact_count": len(facts),
        "synthetic_observations": sum(1 for f in facts if f.get("synthetic")),
        "event_count": len(events),
        "last_observed_at": max(timestamps) if timestamps else None,
    }


__all__ = [
    "build_merchant_profile",
    "catalog_stats",
    "order_status",
]
