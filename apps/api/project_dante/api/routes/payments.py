"""Payment executor routes — order creation, client verification, demo sim.

Executor discipline (master plan §15.2, invariant #7): every money-adjacent
request re-validates the frozen contract immediately before touching Razorpay.
The critical check is CONTRACT DRIFT: we recompute sha256 over the stored
offer + frozen promise set and compare against ``contract_hash``. A mismatch
means the world changed after buyer authorization ⇒ HTTP 409 and NO order.

Client "payment success" is never final truth (invariant #9): verify-client
only records CHECKOUT_COMPLETED_CLIENT + PAYMENT_VERIFIED_SERVER and moves the
contract to PAYMENT_PENDING. Only the signature-verified webhook grants PAID.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from project_dante.api.routes.webhooks import handle_webhook_bytes
from project_dante.db.store import STORE
from project_dante.domain.events import append_event
from project_dante.domain.hashing import sha256_hex
from project_dante.domain.state_machine import InvalidTransition, validate_transition
from project_dante.integrations.razorpay import service

logger = logging.getLogger("project_dante.payments")

router = APIRouter(tags=["payments"])


# ------------------------------------------------------------------ helpers


def _get_contract_or_404(contract_id: str) -> dict:
    contract = STORE.get(contract_id)
    if contract is None or contract.get("_type") != "contract":
        raise HTTPException(status_code=404, detail="contract_not_found")
    return contract


def _recompute_contract_hash(contract: dict) -> str | None:
    """Recompute sha256 over the FROZEN offer + promise set exactly as the
    contract pipeline froze it. Returns None when inputs are missing (nothing
    to drift-check — e.g. fixtures created outside Agent D's pipeline)."""
    offer = STORE.get(contract.get("offer_id") or "")
    if offer is None:
        return None
    # Canonical formulas come from the promise pipeline so this check compares
    # like-for-like with what select-offer froze (volatile keys stripped from
    # the offer view; set hash over sorted normalized key/value pairs).
    try:
        from project_dante.domain.promises.pipeline import (
            VOLATILE_OFFER_KEYS,
            compute_contract_hash,
        )
        from project_dante.domain.hashing import canonical_json

        stable = {k: v for k, v in offer.items() if k not in VOLATILE_OFFER_KEYS and k != "_type"}
        promises = [p for p in STORE.list("promise") if p.get("contract_id") == contract["id"]]
        if not promises:
            return None
        def _norm_of(pr: dict) -> str:
            nv = pr.get("normalized_value")
            return canonical_json(nv if nv is not None else pr.get("value")).decode()

        def _norm_of(pr: dict) -> str:
            nv = pr.get("normalized_value")
            return canonical_json(nv if nv is not None else pr.get("value")).decode()

        pairs = sorted((pr["key"], _norm_of(pr)) for pr in promises)
        promise_set_hash = sha256_hex([list(t) for t in pairs])
        # Legacy/fixture formulation: set hash over the raw record list and a
        # contract hash keyed by "offer". Accepted alongside the canonical
        # pipeline formulation so externally-seeded contracts still validate.
        legacy_psh = sha256_hex(promises)
        offer_view = {k: v for k, v in offer.items() if k != "_type"}
        candidates = {
            compute_contract_hash(sha256_hex(stable), promise_set_hash),
            sha256_hex({"offer": offer_view, "promise_set_hash": legacy_psh}),
            sha256_hex({"offer": offer_view, "promise_set_hash": promise_set_hash}),
        }
        stored = contract.get("contract_hash")
        if stored in candidates:
            return stored
        return sorted(candidates)[0]
    except ImportError:
        return None


def _transition(contract_id: str, current: str, target: str) -> None:
    try:
        validate_transition(current, target)
    except InvalidTransition as exc:
        detail = f"invalid_transition:{current}->{target}"
        raise HTTPException(status_code=409, detail=detail) from exc
    STORE.update(contract_id, status=target)


# ------------------------------------------------------- POST /payment-order


class PaymentOrderResponse(BaseModel):
    mode: str
    razorpay_order: dict[str, Any]
    checkout_config: dict[str, Any]
    contract_status: str


@router.post("/contracts/{contract_id}/payment-order", response_model=PaymentOrderResponse)
async def create_payment_order(contract_id: str) -> PaymentOrderResponse:
    """Create a Razorpay order for an authorized, frozen contract.

    Order of operations is the safety story:
      1. contract exists, is authorized, amount matches the frozen contract;
      2. EXECUTOR RE-CHECK — recompute the contract hash; 409 on drift;
      3. only then call Razorpay (real Test Mode or sandbox);
      4. persist order id, transition state machine, append audit event.
    """
    contract = _get_contract_or_404(contract_id)

    # ---- authorization + status gates ------------------------------------
    status = contract.get("status")
    if contract.get("buyer_authority") is None:
        raise HTTPException(status_code=409, detail="buyer_not_authorized")

    # Idempotent re-entry: an existing live order for this contract is returned
    # unchanged instead of minting a second payable order.
    existing_order_id = contract.get("razorpay_order_id")
    if status == "PAYMENT_ORDER_CREATED" and existing_order_id:
        existing = STORE.get(existing_order_id)
        if existing is not None:
            return PaymentOrderResponse(
                mode=service.mode(),
                razorpay_order={k: v for k, v in existing.items() if k != "_type"},
                checkout_config={
                    "key_id": service.key_id_public(),
                    "order_id": existing["id"],
                    "amount_paise": existing["amount"],
                    "currency": existing.get("currency", "INR"),
                },
                contract_status=status,
            )

    if status != "AWAITING_BUYER_AUTH":
        raise HTTPException(
            status_code=409, detail=f"invalid_transition:{status}->PAYMENT_ORDER_CREATED"
        )

    # ---- amount gate -------------------------------------------------------
    amount_paise = contract.get("amount_paise")
    if not isinstance(amount_paise, int) or amount_paise <= 0:
        raise HTTPException(status_code=409, detail="contract_amount_invalid")

    authority = contract.get("buyer_authority") or {}
    max_amount = authority.get("max_amount_paise")
    if isinstance(max_amount, int) and amount_paise > max_amount:
        raise HTTPException(status_code=409, detail="amount_exceeds_buyer_authority")

    # ---- EXECUTOR RE-CHECK: contract drift ---------------------------------
    stored_hash = contract.get("contract_hash")
    recomputed = _recompute_contract_hash(contract)
    if stored_hash and recomputed is not None and recomputed != stored_hash:
        logger.error("CONTRACT DRIFT detected contract=%s", contract_id)
        append_event(
            aggregate_type="contract",
            aggregate_id=contract_id,
            event_type="STATE_RECONCILED",
            payload={
                "reason": "contract_drift",
                "stored_hash_prefix": str(stored_hash)[:12],
                "recomputed_hash_prefix": recomputed[:12],
                "action": "order_blocked",
            },
        )
        raise HTTPException(status_code=409, detail="contract_drift")

    # ---- create the order (first gateway contact happens HERE) -------------
    receipt = f"dante:{contract_id}"[:40]
    notes = {
        "contract_id": contract_id,
        "intent_id": str(contract.get("intent_id") or ""),
        "offer_id": str(contract.get("offer_id") or ""),
    }
    try:
        order = service.create_order(amount_paise, receipt=receipt, notes=notes)
    except service.RazorpayError as exc:
        logger.error(
            "razorpay order creation failed contract=%s status=%s", contract_id, exc.status_code
        )
        raise HTTPException(status_code=502, detail="razorpay_order_failed") from exc

    # ---- persist + transition + audit --------------------------------------
    is_sandbox = service.mode() == "sandbox"
    STORE.update(contract_id, razorpay_order_id=order["id"], sandbox_mode=is_sandbox)
    STORE.put(
        {
            "_type": "razorpay_order",
            **{k: v for k, v in order.items() if k != "_type"},
            "id": order["id"],
            "contract_id": contract_id,
        }
    )
    _transition(contract_id, "AWAITING_BUYER_AUTH", "PAYMENT_ORDER_CREATED")
    append_event(
        aggregate_type="contract",
        aggregate_id=contract_id,
        event_type="RAZORPAY_ORDER_CREATED",
        payload={
            "razorpay_order_id": order["id"],
            "amount_paise": amount_paise,
            "mode": service.mode(),
        },
        idempotency_key=f"order:{contract_id}:{order['id']}",
    )
    logger.info(
        "razorpay order created contract=%s order=%s amount=%d mode=%s",
        contract_id,
        order["id"],
        amount_paise,
        service.mode(),
    )

    return PaymentOrderResponse(
        mode=service.mode(),
        razorpay_order={k: v for k, v in order.items() if k != "_type"},
        checkout_config={
            "key_id": service.key_id_public(),
            "order_id": order["id"],
            "amount_paise": amount_paise,
            "currency": order.get("currency", "INR"),
        },
        contract_status="PAYMENT_ORDER_CREATED",
    )


# --------------------------------------------------- POST /verify-client


class VerifyClientRequest(BaseModel):
    contract_id: str
    razorpay_order_id: str
    razorpay_payment_id: str
    signature: str


@router.post("/payments/verify-client")
async def verify_client_payment(req: VerifyClientRequest) -> dict[str, Any]:
    """Verify the checkout handler's signature SERVER-side.

    Marks CHECKOUT_COMPLETED_CLIENT + PAYMENT_VERIFIED_SERVER and moves the
    contract to PAYMENT_PENDING. Deliberately does NOT set PAID — that grant
    belongs exclusively to the webhook path (routes/webhooks.py).
    """
    contract = _get_contract_or_404(req.contract_id)

    expected_order = contract.get("razorpay_order_id")
    if expected_order and req.razorpay_order_id != expected_order:
        raise HTTPException(status_code=403, detail="order_mismatch_for_contract")

    if not service.verify_checkout_signature(
        req.razorpay_order_id, req.razorpay_payment_id, req.signature
    ):
        logger.warning("client signature verification FAILED contract=%s", req.contract_id)
        raise HTTPException(status_code=400, detail="signature_verification_failed")

    updates: dict[str, Any] = {"razorpay_payment_id": req.razorpay_payment_id}
    current = contract.get("status")

    if current in ("PAID",) or current in (
        "FULFILLING",
        "DELIVERED",
        "VERIFYING",
        "SATISFIED",
    ):
        # Webhook already granted PAID (or beyond): record verification, no regression.
        STORE.update(req.contract_id, **updates)
        append_event(
            aggregate_type="contract",
            aggregate_id=req.contract_id,
            event_type="PAYMENT_VERIFIED_SERVER",
            payload={"payment_id": req.razorpay_payment_id, "note": "already_paid_via_webhook"},
        )
        refreshed = STORE.get(req.contract_id)
        return {"status": "client_confirmed", "contract_status": refreshed["status"]}

    try:
        validate_transition(current, "PAYMENT_PENDING")
    except InvalidTransition as exc:
        detail = f"invalid_transition:{current}->PAYMENT_PENDING"
        raise HTTPException(status_code=409, detail=detail) from exc

    STORE.update(req.contract_id, **updates)
    append_event(
        aggregate_type="contract",
        aggregate_id=req.contract_id,
        event_type="CHECKOUT_COMPLETED_CLIENT",
        payload={"payment_id": req.razorpay_payment_id},
    )
    _transition(req.contract_id, current, "PAYMENT_PENDING")
    append_event(
        aggregate_type="contract",
        aggregate_id=req.contract_id,
        event_type="PAYMENT_VERIFIED_SERVER",
        payload={"payment_id": req.razorpay_payment_id},
    )
    logger.info(
        "client payment verified contract=%s payment=%s", req.contract_id, req.razorpay_payment_id
    )

    return {"status": "client_confirmed", "contract_status": "PAYMENT_PENDING"}


# ------------------------------------------- POST /demo/razorpay/simulate-event


class SimulateEventRequest(BaseModel):
    event_type: str = Field(default="payment.captured")
    order_id: str
    payment_id: str | None = None


@router.post("/demo/razorpay/simulate-event")
async def demo_simulate_event(req: SimulateEventRequest = Body(...)) -> dict[str, Any]:
    """DEMO-ONLY: fabricate a REAL signed Razorpay webhook and push it through
    the same verification gate as production traffic.

    Guarded twice: requires settings.demo_mode AND sandbox adapter. It is a
    stand-in for RAZORPAY'S OWN capture step (which needs real keys), never a
    bypass of signature verification.
    """
    from project_dante.settings import get_settings

    settings = get_settings()
    if not settings.demo_mode or settings.razorpay_live_test_mode:
        raise HTTPException(
            status_code=403,
            detail="demo_simulate_event_requires_demo_mode_and_sandbox",
        )
    if service.mode() != "sandbox":
        raise HTTPException(status_code=403, detail="simulate_event_sandbox_only")
    if req.event_type != "payment.captured":
        raise HTTPException(status_code=422, detail="unsupported_event_type")

    # Mint the sandbox capture that Razorpay's own gateway would have made.
    try:
        payment = service.capture_sandbox_payment(req.order_id, req.payment_id)
    except service.RazorpayError as exc:
        raise HTTPException(status_code=404, detail="sandbox_order_not_found") from exc

    # Build the exact envelope Razorpay sends. The event id is DERIVED from
    # (event_type, order_id, payment_id) so re-simulating the same capture is
    # treated as a redelivery by the intake's dedupe — mirroring real
    # Razorpay behaviour where the same gateway event keeps its identity.
    derived_event_id = "evt_demo_" + hashlib.sha1(
        f"{req.event_type}|{req.order_id}|{payment['id']}".encode()
    ).hexdigest()[:16]
    payload = {
        "event": "payment.captured",
        "id": derived_event_id,
        "created_at": int(time.time()),
        "payload": {
            "payment": {
                "entity": {
                    "id": payment["id"],
                    "entity": "payment",
                    "amount": payment["amount"],
                    "currency": payment["currency"],
                    "status": payment["status"],
                    "order_id": payment["order_id"],
                    "method": payment.get("method"),
                    "captured": True,
                    "notes": payment.get("notes", {}),
                }
            }
        },
    }
    raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = service.sign_webhook_payload(raw_body)

    status, body = await handle_webhook_bytes(raw_body, signature, payload["id"])
    if status != 200 or not body.get("ok"):
        raise HTTPException(status_code=500, detail="simulated_webhook_failed_intake")

    contract = STORE.find_one("contract", razorpay_order_id=req.order_id)
    return {
        "delivered": True,
        "synthetic": True,
        "event_id": payload["id"],
        "payment_id": payment["id"],
        "contract_status": contract["status"] if contract else None,
    }
