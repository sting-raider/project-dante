"""Razorpay webhook intake — the durable, server-truth payment channel.

Security ordering is non-negotiable (master plan §16.5, invariant #10):
    raw bytes → HMAC-SHA256 verify → ONLY THEN json.loads.

Everything else is reliability mechanics:
- every verified event is stored (``webhook_event``) keyed by provider event id;
- duplicates return ``200 {"ok": true, "duplicate": true}`` with ZERO domain
  effect beyond a ``WEBHOOK_DUPLICATE_IGNORED`` audit event;
- ``payment.captured`` is the ONLY signal that moves a contract to PAID;
- out-of-order arrivals reconcile instead of corrupting state (§16.7);
- handlers stay local/in-memory — no external calls except optional
  reconciliation fetches, so Razorpay gets a fast ACK.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from project_dante.db.store import STORE
from project_dante.domain.events import append_event, now_iso
from project_dante.domain.state_machine import InvalidTransition, validate_transition
from project_dante.integrations.razorpay import service

logger = logging.getLogger("project_dante.webhooks")

router = APIRouter(tags=["payments"])

SIGNATURE_HEADER = "X-Razorpay-Signature"
EVENT_ID_HEADER = "X-Razorpay-Event-Id"

# Statuses at-or-past PAID: a captured event arriving now carries no new truth.
_POST_PAID_STATUSES = {
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
}


# ------------------------------------------------------------------ helpers


def _fallback_event_id(raw_body: bytes) -> str:
    """Deterministic id when the provider omits one: identical payload ⇒
    identical id, so replays still dedupe."""
    return "sha256_" + hashlib.sha256(raw_body).hexdigest()


def _extract_entity(payload: dict[str, Any], kind: str) -> dict[str, Any] | None:
    """Razorpay envelopes entities as payload.<kind>.entity."""
    inner = payload.get("payload")
    if not isinstance(inner, dict):
        return None
    wrap = inner.get(kind)
    if not isinstance(wrap, dict):
        return None
    entity = wrap.get("entity")
    return entity if isinstance(entity, dict) else None


def _contract_for_order(order_id: str | None, notes: dict[str, Any] | None) -> dict | None:
    """Resolve the owning contract from the order id (primary) or the
    contract_id we embedded in order notes at creation (fallback)."""
    if order_id:
        rec = STORE.find_one("contract", razorpay_order_id=order_id)
        if rec is not None:
            return rec
    if notes and notes.get("contract_id"):
        return STORE.get(str(notes["contract_id"]))
    return None


def _safe_transition(contract_id: str, current: str, target: str) -> bool:
    try:
        validate_transition(current, target)
    except InvalidTransition:
        return False
    STORE.update(contract_id, status=target)
    return True


# Legal hops from pre-payment states up to PAID, used to walk out-of-order
# captures through the machine instead of teleporting.
_CAPTURE_WALK: dict[str, list[str]] = {
    "CONTRACT_FROZEN": ["AWAITING_BUYER_AUTH", "PAYMENT_ORDER_CREATED", "PAYMENT_PENDING", "PAID"],
    "AWAITING_BUYER_AUTH": ["PAYMENT_ORDER_CREATED", "PAYMENT_PENDING", "PAID"],
    "PAYMENT_ORDER_CREATED": ["PAYMENT_PENDING", "PAID"],
    "PAYMENT_PENDING": ["PAID"],
}


def _walk_to_paid(contract_id: str, current: str) -> bool:
    """Advance along the legal payment path as far as the machine allows.

    Recovery semantics (webhook-chaos review finding): a walk interrupted
    mid-way by an operational fault strands the contract at an intermediate
    status with buyer money captured. Razorpay redelivers webhooks, so the
    recovery path is to let a REDELIVERED capture resume the walk from
    wherever the contract now sits — including intermediate statuses. The
    dedupe layer keys on event id, so a *new* delivery id carrying the same
    payload resumes cleanly instead of being swallowed as a duplicate.
    """
    path = _CAPTURE_WALK.get(current)
    if not path:
        # Mid-walk stranding recovery: contract sits at an intermediate hop
        # (e.g. PAYMENT_ORDER_CREATED after a crash) and a fresh capture
        # arrives. Build the remaining path from that status.
        path = _CAPTURE_WALK.get(
            current,
            next(
                (v[i:] for k, v in _CAPTURE_WALK.items() if current in v
                 for i, hop in enumerate(v) if hop == current),
                None,
            ),
        )
        if not path:
            return False
    from_status = current
    for target in path:
        if not _safe_transition(contract_id, from_status, target):
            return False
        if target != "PAID":
            append_event(
                aggregate_type="contract",
                aggregate_id=contract_id,
                event_type="STATE_RECONCILED",
                payload={"reason": "out_of_order_capture_walk", "to_status": target},
            )
        from_status = target
    return from_status == "PAID"


# ------------------------------------------------------- webhook core engine


async def handle_webhook_bytes(
    raw_body: bytes,
    signature: str | None,
    event_id_hint: str | None,
) -> tuple[int, dict[str, Any]]:
    """Verify → dedupe → dispatch. Shared by the public route AND the demo
    simulator, so the simulated path crosses the exact same verification gate.

    Returns (http_status, response_body). Never raises for provider-side mess.
    """
    # ---- 1. signature FIRST, before any parsing -------------------------
    if not signature or not service.verify_webhook_signature(raw_body, signature):
        logger.warning("webhook rejected: bad or missing signature")
        return 401, {"ok": False, "error": "invalid_signature"}

    # ---- 2. parse only after verification --------------------------------
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("webhook rejected: undecodable body after valid signature")
        return 400, {"ok": False, "error": "malformed_json"}
    if not isinstance(payload, dict):
        return 400, {"ok": False, "error": "malformed_json"}

    event_type = str(payload.get("event") or "unknown")
    event_id = event_id_hint or str(payload.get("id") or "") or _fallback_event_id(raw_body)

    # ---- 3. duplicate detection BEFORE any domain effect -----------------
    if STORE.get(event_id) is not None:
        append_event(
            aggregate_type="razorpay",
            aggregate_id=event_id,
            event_type="WEBHOOK_DUPLICATE_IGNORED",
            payload={"event_type": event_type},
        )
        logger.info("webhook duplicate ignored id=%s type=%s", event_id, event_type)
        return 200, {"ok": True, "duplicate": True}

    # ---- 4. persist the verified event (audit record of arrival) ---------
    STORE.put(
        {
            "_type": "webhook_event",
            "id": event_id,
            "event_type": event_type,
            "received_at": now_iso(),
            "processing_status": "processed",
            "payload": payload,
        }
    )
    append_event(
        aggregate_type="razorpay",
        aggregate_id=event_id,
        event_type="WEBHOOK_RECEIVED",
        payload={"event_type": event_type, "verified": True},
    )

    # ---- 5. dispatch ------------------------------------------------------
    if event_type == "payment.captured":
        _on_payment_captured(event_id, payload)
    elif event_type in ("refund.processed", "refund.completed"):
        _on_refund_processed(event_id, payload)
    else:
        logger.info("webhook stored without domain effect type=%s", event_type)

    return 200, {"ok": True}


# ------------------------------------------------------------ event effects


def _on_payment_captured(event_id: str, payload: dict[str, Any]) -> None:
    entity = _extract_entity(payload, "payment") or {}
    payment_id = str(entity.get("id") or "")
    order_id = str(entity.get("order_id") or "") or None
    notes = entity.get("notes") if isinstance(entity.get("notes"), dict) else {}
    contract = _contract_for_order(order_id, notes)
    if contract is None:
        logger.warning("captured event %s matched no contract (order=%s)", event_id, order_id)
        append_event(
            aggregate_type="razorpay",
            aggregate_id=event_id,
            event_type="STATE_RECONCILED",
            payload={
                "reason": "captured_event_without_contract",
                "order_id": order_id,
                "payment_id": payment_id,
            },
        )
        return

    contract_id = contract["id"]
    amount = entity.get("amount")
    expected = contract.get("amount_paise")

    # Amount tampering guard: a capture whose amount differs from the frozen
    # contract is recorded but NEVER grants PAID (plan §23 amount manipulation).
    if isinstance(expected, int) and isinstance(amount, int) and amount != expected:
        append_event(
            aggregate_type="contract",
            aggregate_id=contract_id,
            event_type="STATE_RECONCILED",
            payload={
                "reason": "captured_amount_mismatch",
                "expected_amount_paise": expected,
                "captured_amount_paise": amount,
                "payment_id": payment_id,
                "action": "paid_withheld",
            },
        )
        logger.error("amount mismatch on contract=%s: %s != %s", contract_id, amount, expected)
        return

    status = contract.get("status")

    # Attach the observed payment id only where a payment relationship is
    # meaningful and NON-CONFLICTING. Review finding: a second capture for a
    # DIFFERENT payment id silently repointed a PAID contract's payment id,
    # corrupting refund routing. Once a contract carries a payment id, only
    # the SAME id may re-affirm it; anything else is recorded as a conflict.
    existing_payment = contract.get("razorpay_payment_id")
    updates: dict[str, Any] = {}
    if payment_id and not existing_payment:
        # Legal pre-payment states may receive their first binding; paid/
        # past-paid states keep the audit trail below instead.
        if str(status) in _CAPTURE_WALK:
            updates["razorpay_payment_id"] = payment_id
    elif payment_id and existing_payment and payment_id != existing_payment:
        append_event(
            aggregate_type="contract",
            aggregate_id=contract_id,
            event_type="STATE_RECONCILED",
            payload={
                "reason": "conflicting_payment_capture",
                "known_payment_id": existing_payment,
                "observed_payment_id": payment_id,
                "action": "payment_binding_unchanged",
            },
        )
        logger.error(
            "conflicting capture on contract=%s: known=%s observed=%s",
            contract_id, existing_payment, payment_id,
        )
    if updates:
        STORE.update(contract_id, **updates)

    if status == "PAID":
        # Idempotent late redelivery after we already went paid.
        append_event(
            aggregate_type="contract",
            aggregate_id=contract_id,
            event_type="RAZORPAY_PAYMENT_CAPTURED",
            payload={"payment_id": payment_id, "note": "already_paid"},
        )
        return

    if status in _POST_PAID_STATUSES:
        # Progressed past payment (e.g. fulfillment raced ahead). Never regress.
        append_event(
            aggregate_type="contract",
            aggregate_id=contract_id,
            event_type="RAZORPAY_PAYMENT_CAPTURED",
            payload={
                "payment_id": payment_id,
                "note": "contract_already_past_paid",
                "status": status,
            },
        )
        return

    # Out-of-order tolerance: walk every pre-payment state along the legal
    # transition path to PAID, logging each hop as a reconciliation. A normal
    # PAYMENT_PENDING arrival walks its single hop with no reconciliation noise.
    if _walk_to_paid(contract_id, str(status)):
        append_event(
            aggregate_type="contract",
            aggregate_id=contract_id,
            event_type="RAZORPAY_PAYMENT_CAPTURED",
            payload={
                "payment_id": payment_id,
                "order_id": order_id,
                "reconciled": True,
                "walked_from": status,
            },
        )
        return

    # Non-payable state (DRAFT/pre-offer, CANCELLED, FAILED): gateway reality
    # is recorded honestly but the lifecycle is NEVER resurrected — a capture
    # on a cancelled contract is an orphaned payment for human handling, not a
    # teleport to PAID (plan §22, invariant I12).
    append_event(
        aggregate_type="contract",
        aggregate_id=contract_id,
        event_type="STATE_RECONCILED",
        payload={
            "reason": "captured_event_for_non_payable_state",
            "from_status": status,
            "observed_payment_id": payment_id,
            "observed_order_id": order_id,
            "action": "paid_withheld",
        },
    )


def _on_refund_processed(event_id: str, payload: dict[str, Any]) -> None:
    entity = _extract_entity(payload, "refund") or {}
    refund_id = str(entity.get("id") or "")
    payment_id = str(entity.get("payment_id") or "")
    amount = entity.get("amount")

    contract = STORE.find_one("contract", razorpay_payment_id=payment_id) if payment_id else None
    aggregate_id = contract["id"] if contract else (refund_id or event_id)
    aggregate_type = "contract" if contract else "razorpay"

    rec = STORE.get(refund_id)
    if rec is not None and rec.get("_type") == "razorpay_refund":
        STORE.update(refund_id, status="processed")

    append_event(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type="REFUND_PROCESSED",
        payload={
            "refund_id": refund_id,
            "payment_id": payment_id,
            "amount_paise": amount,
        },
    )


# ------------------------------------------------------------------- route


@router.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request) -> JSONResponse:
    """Public intake for Razorpay server-to-server webhooks.

    Reads the RAW body, verifies X-Razorpay-Signature BEFORE parsing, stores
    every verified event, applies domain effects idempotently, ACKs fast.
    """
    raw_body = await request.body()
    signature = request.headers.get(SIGNATURE_HEADER)
    event_id = request.headers.get(EVENT_ID_HEADER)
    status, body = await handle_webhook_bytes(raw_body, signature, event_id)
    return JSONResponse(status_code=status, content=body)
