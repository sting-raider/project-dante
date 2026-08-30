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
import time
from datetime import UTC, datetime
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

# A handler is local and intentionally fast, but a crashed worker can leave a
# claimed event behind. After this lease expires a redelivery may reclaim it;
# a live duplicate inside the lease is acknowledged without a second effect.
_WEBHOOK_PROCESSING_LEASE_SECONDS = 300
# Razorpay includes a Unix ``created_at`` on every webhook. Rejecting an old
# signed body prevents a replay that changes only the unsigned delivery header
# from entering the event store as a fresh event. Known event ids are allowed
# through this check so a failed domain dispatch can still be redelivered.
_WEBHOOK_MAX_AGE_SECONDS = 300
_WEBHOOK_MAX_FUTURE_SKEW_SECONDS = 300

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


def _processing_started_at_is_stale(value: Any) -> bool:
    """Return True when a webhook processing claim is missing or expired."""
    if not isinstance(value, str) or not value:
        return True
    try:
        started = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
    except ValueError:
        return True
    age = (datetime.now(UTC) - started.astimezone(UTC)).total_seconds()
    return age >= _WEBHOOK_PROCESSING_LEASE_SECONDS


def _webhook_timestamp_error(payload: dict[str, Any]) -> str | None:
    """Return a freshness/schema error for a provider webhook envelope.

    Razorpay's replay guidance uses the top-level integer ``created_at``. Keep
    the boundary strict: accepting strings, booleans, NaN-like values, or a
    missing timestamp would turn the freshness check into decoration.
    """
    created_at = payload.get("created_at")
    if not isinstance(created_at, int) or isinstance(created_at, bool):
        return "webhook_created_at_invalid"
    age = time.time() - created_at
    if age > _WEBHOOK_MAX_AGE_SECONDS:
        return "webhook_created_at_stale"
    if age < -_WEBHOOK_MAX_FUTURE_SKEW_SECONDS:
        return "webhook_created_at_in_future"
    return None


def _binding_mismatch(bound: Any, observed: str | None) -> bool:
    """Return whether two present provider identifiers disagree."""
    return bool(bound) and bool(observed) and str(bound) != str(observed)


def _payment_record_binding_conflict(
    record: dict[str, Any], order_id: str | None
) -> str | None:
    """Return a conflict before a captured payment projection is written."""
    if record.get("_type") != "razorpay_payment":
        return "payment_id_record_conflict"
    bound_order = record.get("order_id")
    if bound_order and (not order_id or str(bound_order) != str(order_id)):
        return "payment_record_order_mismatch"
    return None


def _withhold_captured_event(
    contract_id: str, *, reason: str, **details: Any
) -> None:
    """Audit a captured event that must not advance the contract to PAID."""
    append_event(
        aggregate_type="contract",
        aggregate_id=contract_id,
        event_type="STATE_RECONCILED",
        payload={**details, "reason": reason, "action": "paid_withheld"},
    )


def _refund_binding_conflict(
    payment_id: str,
    order_id: str | None,
    refund_record: dict[str, Any] | None = None,
) -> str | None:
    """Find a known payment/order binding conflict before applying a refund.

    A signed webhook authenticates the sender, not the relationship between
    every identifier in its entity. If both identifiers are present, all
    locally-known projections must agree. Missing payment binding remains
    legal for the refund-before-capture ordering.
    """
    if refund_record is not None:
        if _binding_mismatch(refund_record.get("payment_id"), payment_id):
            return "refund_payment_mismatch"
        if _binding_mismatch(refund_record.get("order_id"), order_id):
            return "refund_order_mismatch"

    if payment_id:
        payment = STORE.get(payment_id)
        if (
            payment is not None
            and payment.get("_type") == "razorpay_payment"
            and _binding_mismatch(payment.get("order_id"), order_id)
        ):
            return "payment_order_mismatch"

    payment_contract = (
        STORE.find_one("contract", razorpay_payment_id=payment_id) if payment_id else None
    )
    order_contract = (
        STORE.find_one("contract", razorpay_order_id=order_id) if order_id else None
    )
    if (
        payment_contract is not None
        and order_contract is not None
        and str(payment_contract.get("id")) != str(order_contract.get("id"))
    ):
        return "payment_and_order_target_different_contracts"
    if payment_contract is not None and _binding_mismatch(
        payment_contract.get("razorpay_order_id"), order_id
    ):
        return "contract_order_mismatch"
    if order_contract is not None and _binding_mismatch(
        order_contract.get("razorpay_payment_id"), payment_id
    ):
        return "contract_payment_mismatch"
    return None


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
    contract_id we embedded in order notes at creation (fallback).

    Review finding: a foreign order id could claim any contract via the
    notes fallback when the primary lookup missed. The fallback now only
    fires when the resolved contract actually owns this order id — i.e. its
    razorpay_order_id matches or it has none yet (pre-binding walk)."""
    if order_id:
        rec = STORE.find_one("contract", razorpay_order_id=order_id)
        if rec is not None:
            return rec
    if notes and notes.get("contract_id"):
        cand = STORE.get(str(notes["contract_id"]))
        # Final-assault finding [03]: the fallback previously accepted ANY
        # record id and any unbound contract — a signature-valid envelope
        # could graft a foreign payment onto a pre-order contract. Now:
        # 1. the candidate must genuinely be a contract record;
        # 2. it must be bound to THIS order id (primary path already covers
        #    that), OR be unbound while a Dante-ISSUED razorpay_order record
        #    exists whose notes name this contract (provenance check);
        # 3. otherwise the claim is refused — orphaned captures are recorded
        #    upstream, never grafted.
        if cand is not None and cand.get("_type") == "contract":
            bound = cand.get("razorpay_order_id")
            if bound and str(bound) == str(order_id):
                return cand
            if not bound and order_id:
                issued = STORE.get(order_id)
                if (
                    issued is not None
                    and issued.get("_type") == "razorpay_order"
                    and isinstance(issued.get("notes"), dict)
                    and str(issued["notes"].get("contract_id") or "")
                    == str(notes["contract_id"])
                ):
                    return cand
        return None
    return None


def _contract_id_for_webhook(event_type: str, payload: dict[str, Any]) -> str | None:
    """Resolve the contract that a verified provider event can belong to.

    Raw webhook audit events use the provider event id as their aggregate id,
    so the contract timeline needs an explicit correlation edge. Keep this
    resolver aligned with the domain handlers' ownership checks: payment
    events resolve through Dante's order/notes binding, while refund events
    resolve through the already-bound payment or order binding.
    """
    if event_type.startswith("payment."):
        entity = _extract_entity(payload, "payment") or {}
        order_id = str(entity.get("order_id") or "") or None
        notes = entity.get("notes") if isinstance(entity.get("notes"), dict) else {}
        contract = _contract_for_order(order_id, notes)
        return str(contract["id"]) if contract else None

    if event_type in ("refund.created", "refund.processed", "refund.completed"):
        entity = _extract_entity(payload, "refund") or {}
        payment_id = str(entity.get("payment_id") or "") or None
        order_id = str(entity.get("order_id") or "") or None
        contract = _refund_contract(payment_id or "", {}, None, order_id=order_id)
        return str(contract["id"]) if contract else None

    return None


def _safe_transition(contract_id: str, current: str, target: str) -> bool:
    # Treat an already-reached target as idempotent. Otherwise the status
    # compare-and-swap below prevents two distinct provider deliveries from
    # both validating the same stale status and overwriting one another.
    latest = STORE.get(contract_id)
    if latest is None:
        return False
    actual = str(latest.get("status") or "")
    if actual == target:
        return True
    if actual != current:
        return False
    try:
        validate_transition(actual, target)
    except InvalidTransition:
        return False
    return STORE.update_if(contract_id, {"status": actual}, status=target)


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

    # ---- 2b. freshness/replay gate ---------------------------------------
    # Check the existing event id before rejecting a stale envelope. This is
    # important for Razorpay redelivery after a transient local failure: it
    # must be able to reclaim the same failed claim, while a stale body paired
    # with a new event id cannot create a second verified arrival.
    timestamp_error = _webhook_timestamp_error(payload)
    if timestamp_error and STORE.get(event_id) is None:
        logger.warning("webhook rejected: %s event_id=%s", timestamp_error, event_id)
        return 400, {"ok": False, "error": timestamp_error}

    # ---- 3. atomically claim the verified event ---------------------------
    # A get-then-put sequence lets concurrent deliveries both enter the
    # domain handler. put_if_absent is implemented by both storage backends,
    # so the event id is claimed exactly once.
    now = now_iso()
    claimed = STORE.put_if_absent(
        {
            "_type": "webhook_event",
            "id": event_id,
            "event_type": event_type,
            "received_at": now,
            "processing_started_at": now,
            "processing_status": "processing",
            "attempts": 1,
            "error": None,
            "payload": payload,
        }
    )
    redelivery = False
    if not claimed:
        existing = STORE.get(event_id)
        if existing is None or existing.get("_type") != "webhook_event":
            # A provider event id colliding with a different Dante record is a
            # storage identity conflict, not a safe duplicate.
            logger.error("webhook event id conflicts with non-webhook record id=%s", event_id)
            return 409, {"ok": False, "error": "event_id_conflict"}

        existing_payload = existing.get("payload")
        existing_contract_id = _contract_id_for_webhook(
            str(existing.get("event_type") or ""),
            existing_payload if isinstance(existing_payload, dict) else {},
        )
        processing_status = existing.get("processing_status")
        if processing_status == "processed":
            append_event(
                aggregate_type="razorpay",
                aggregate_id=event_id,
                event_type="WEBHOOK_DUPLICATE_IGNORED",
                payload={
                    "event_id": event_id,
                    "event_type": event_type,
                    "processing_status": "processed",
                },
                correlation_id=existing_contract_id,
            )
            logger.info("webhook duplicate ignored id=%s type=%s", event_id, event_type)
            return 200, {"ok": True, "duplicate": True}

        can_reclaim = processing_status == "failed" or (
            processing_status == "processing"
            and _processing_started_at_is_stale(existing.get("processing_started_at"))
        )
        if can_reclaim:
            attempts = existing.get("attempts")
            try:
                next_attempt = int(attempts) + 1
            except (TypeError, ValueError):
                next_attempt = 2
            claimed = STORE.update_if(
                event_id,
                {"processing_status": processing_status},
                processing_status="processing",
                processing_started_at=now,
                attempts=next_attempt,
                error=None,
            )
            redelivery = claimed

        if not claimed:
            append_event(
                aggregate_type="razorpay",
                aggregate_id=event_id,
                event_type="WEBHOOK_DUPLICATE_IGNORED",
                payload={
                    "event_id": event_id,
                    "event_type": event_type,
                    "processing_status": processing_status,
                    "in_flight": processing_status == "processing",
                },
                correlation_id=existing_contract_id,
            )
            logger.info(
                "webhook duplicate ignored while status=%s id=%s",
                processing_status,
                event_id,
            )
            return 200, {"ok": True, "duplicate": True}

    # ---- 4. persist the verified arrival audit ----------------------------
    append_event(
        aggregate_type="razorpay",
        aggregate_id=event_id,
        event_type="WEBHOOK_RECEIVED",
        payload={
            "event_id": event_id,
            "event_type": event_type,
            "verified": True,
            "redelivery": redelivery,
        },
        correlation_id=_contract_id_for_webhook(event_type, payload),
    )

    # ---- 5. dispatch, then mark processed ---------------------------------
    try:
        if event_type == "payment.captured":
            _on_payment_captured(event_id, payload)
        elif event_type in ("refund.processed", "refund.completed"):
            _on_refund_processed(event_id, payload)
        else:
            logger.info("webhook stored without domain effect type=%s", event_type)
    except Exception as exc:  # noqa: BLE001 - preserve redelivery semantics
        logger.exception("webhook domain dispatch failed id=%s type=%s", event_id, event_type)
        STORE.update_if(
            event_id,
            {"processing_status": "processing"},
            processing_status="failed",
            processing_finished_at=now_iso(),
            error=str(exc)[:500],
        )
        append_event(
            aggregate_type="razorpay",
            aggregate_id=event_id,
            event_type="STATE_RECONCILED",
            payload={
                "reason": "webhook_processing_failed",
                "event_type": event_type,
                "error": str(exc)[:500],
                "redeliverable": True,
            },
        )
        return 500, {"ok": False, "error": "processing_failed"}

    STORE.update_if(
        event_id,
        {"processing_status": "processing"},
        processing_status="processed",
        processed_at=now_iso(),
        processing_finished_at=now_iso(),
        error=None,
    )

    return 200, {"ok": True}


# ------------------------------------------------------------ event effects


def _on_payment_captured(event_id: str, payload: dict[str, Any]) -> None:
    entity = _extract_entity(payload, "payment") or {}
    raw_payment_id = entity.get("id")
    payment_id = raw_payment_id.strip() if isinstance(raw_payment_id, str) else ""
    raw_order_id = entity.get("order_id")
    order_id = (
        raw_order_id.strip()
        if isinstance(raw_order_id, str) and raw_order_id.strip()
        else None
    )
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
    if not payment_id:
        _withhold_captured_event(
            contract_id,
            reason="captured_payment_id_invalid",
            observed_payment_id=raw_payment_id,
            observed_order_id=order_id,
        )
        logger.error("capture without a valid payment id on contract=%s", contract_id)
        return

    amount = entity.get("amount")
    expected = contract.get("amount_paise")
    currency = entity.get("currency")

    # A capture is the provider-side source of truth only after its wire
    # values pass the same strict amount/currency boundary as order creation.
    # In particular, do not let JSON strings, floats, booleans, missing
    # values, or a non-INR capture reach the PAID transition.
    valid_expected = (
        isinstance(expected, int) and not isinstance(expected, bool) and expected > 0
    )
    valid_amount = (
        isinstance(amount, int) and not isinstance(amount, bool) and amount > 0
    )
    if not valid_expected or not valid_amount:
        append_event(
            aggregate_type="contract",
            aggregate_id=contract_id,
            event_type="STATE_RECONCILED",
            payload={
                "reason": "captured_amount_invalid",
                "expected_amount_paise": expected,
                "captured_amount_paise": amount,
                "payment_id": payment_id,
                "action": "paid_withheld",
            },
        )
        logger.error(
            "invalid capture amount on contract=%s: observed=%r expected=%r",
            contract_id,
            amount,
            expected,
        )
        return

    # Amount tampering guard: a capture whose amount differs from the frozen
    # contract is recorded but NEVER grants PAID (plan §23 amount manipulation).
    if amount != expected:
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

    if currency != "INR":
        append_event(
            aggregate_type="contract",
            aggregate_id=contract_id,
            event_type="STATE_RECONCILED",
            payload={
                "reason": "captured_currency_mismatch",
                "expected_currency": "INR",
                "captured_currency": currency,
                "payment_id": payment_id,
                "action": "paid_withheld",
            },
        )
        logger.error(
            "currency mismatch on contract=%s: observed=%r",
            contract_id,
            currency,
        )
        return

    status = str(contract.get("status") or "")

    # A second payment id must never re-point a contract's refund target. The
    # check is repeated after the CAS below because two verified deliveries can
    # race while the first one is binding the contract.
    existing_payment = contract.get("razorpay_payment_id")
    if existing_payment and str(existing_payment) != payment_id:
        _withhold_captured_event(
            contract_id,
            reason="conflicting_payment_capture",
            known_payment_id=existing_payment,
            observed_payment_id=payment_id,
        )
        logger.error(
            "conflicting capture on contract=%s: known=%s observed=%s",
            contract_id,
            existing_payment,
            payment_id,
        )
        return

    # A provider payment id is also a binding key for the local payment
    # projection. Never overwrite a payment record that already belongs to a
    # different order (or to a different record type).
    payment_record = STORE.get(payment_id)
    record_conflict = (
        _payment_record_binding_conflict(payment_record, order_id)
        if payment_record is not None
        else None
    )
    if record_conflict:
        _withhold_captured_event(
            contract_id,
            reason=record_conflict,
            payment_id=payment_id,
            observed_order_id=order_id,
            known_order_id=(payment_record or {}).get("order_id"),
        )
        logger.error(
            "captured payment projection conflict payment=%s order=%s reason=%s",
            payment_id,
            order_id,
            record_conflict,
        )
        return

    # Persist the provider's captured payment observation before downstream
    # refund reconciliation needs it. This is a merge for a payment already
    # created by the sandbox or an earlier webhook; it never overwrites a
    # record of a different type.
    inserted_payment_projection = False
    if payment_record is None:
        observed_refunded = entity.get("amount_refunded")
        initial_refunded = (
            observed_refunded
            if isinstance(observed_refunded, int)
            and not isinstance(observed_refunded, bool)
            and observed_refunded >= 0
            else 0
        )
        capture_record: dict[str, Any] = {
            "_type": "razorpay_payment",
            "id": payment_id,
            "payment_id": payment_id,
            "entity": "payment",
            "amount": amount,
            "amount_paise": amount if isinstance(amount, int) else None,
            "currency": entity.get("currency", "INR"),
            "status": entity.get("status", "captured"),
            "order_id": order_id,
            "captured": entity.get("captured", True),
            "amount_refunded": initial_refunded,
            "refund_status": entity.get("refund_status"),
            "notes": entity.get("notes") if isinstance(entity.get("notes"), dict) else {},
            "webhook_event_id": event_id,
            "mode": service.mode(),
            "sandbox": service.mode() == "sandbox",
        }
        inserted_payment_projection = STORE.put_if_absent(capture_record)
        if inserted_payment_projection:
            payment_record = capture_record
        else:
            # Another delivery claimed this payment id between our read and
            # insert. Re-check its order binding before doing anything else.
            payment_record = STORE.get(payment_id)
            record_conflict = (
                _payment_record_binding_conflict(payment_record, order_id)
                if payment_record is not None
                else "payment_projection_claim_lost"
            )
            if record_conflict:
                _withhold_captured_event(
                    contract_id,
                    reason=record_conflict,
                    payment_id=payment_id,
                    observed_order_id=order_id,
                    known_order_id=(payment_record or {}).get("order_id"),
                )
                return

    if not inserted_payment_projection and payment_record is not None:
        capture_updates = {
            "payment_id": payment_id,
            "amount": amount,
            "amount_paise": (
                amount if isinstance(amount, int) else payment_record.get("amount_paise")
            ),
            "currency": entity.get("currency", payment_record.get("currency", "INR")),
            "status": entity.get("status", payment_record.get("status", "captured")),
            "order_id": order_id or payment_record.get("order_id"),
            "captured": entity.get("captured", payment_record.get("captured", True)),
            "webhook_event_id": event_id,
        }
        # Razorpay's payment.captured payload normally snapshots
        # ``amount_refunded`` as 0. A refund webhook can legitimately arrive
        # first, though, leaving a local refund projection with a larger total.
        # Never let that stale provider snapshot erase already-reconciled money.
        observed_refunded = entity.get("amount_refunded")
        if (
            isinstance(observed_refunded, int)
            and not isinstance(observed_refunded, bool)
            and observed_refunded >= 0
        ):
            prior_refunded = payment_record.get("amount_refunded")
            prior_amount = (
                prior_refunded
                if isinstance(prior_refunded, int)
                and not isinstance(prior_refunded, bool)
                and prior_refunded >= 0
                else 0
            )
            ledger_total, ledger_refund_ids = _processed_refund_totals(payment_id)
            capture_updates["amount_refunded"] = max(
                observed_refunded, prior_amount, ledger_total
            )
            if ledger_refund_ids:
                capture_updates["processed_refund_ids"] = ledger_refund_ids
                capture_updates["last_refund_id"] = ledger_refund_ids[-1]
                capture_updates["refund_status"] = "processed"
        if not STORE.update_if(
            payment_id,
            {"order_id": payment_record.get("order_id")},
            **capture_updates,
        ):
            latest_payment = STORE.get(payment_id)
            _withhold_captured_event(
                contract_id,
                reason=(
                    _payment_record_binding_conflict(latest_payment, order_id)
                    or "payment_record_changed_during_capture"
                ),
                payment_id=payment_id,
                observed_order_id=order_id,
                known_order_id=(latest_payment or {}).get("order_id"),
            )
            return

    # Attach the observed payment id only where a payment relationship is
    # meaningful and NON-CONFLICTING. Review finding: a second capture for a
    # DIFFERENT payment id silently repointed a PAID contract's payment id,
    # corrupting refund routing. Once a contract carries a payment id, only
    # the SAME id may re-affirm it; anything else is recorded as a conflict.
    if payment_id and not existing_payment and str(status) in _CAPTURE_WALK:
        # The payment binding is a one-winner compare-and-swap. A second
        # capture cannot silently repoint a contract while the first capture
        # is walking it toward PAID.
        bound = STORE.update_if(
            contract_id,
            {"razorpay_payment_id": None, "status": status},
            razorpay_payment_id=payment_id,
        )
        if bound:
            contract = STORE.get(contract_id) or contract
            status = str(contract.get("status") or status)
            existing_payment = payment_id
        else:
            latest = STORE.get(contract_id) or contract
            existing_payment = latest.get("razorpay_payment_id")
            status = str(latest.get("status") or status)
            if existing_payment and str(existing_payment) != payment_id:
                _withhold_captured_event(
                    contract_id,
                    reason="conflicting_payment_capture",
                    known_payment_id=existing_payment,
                    observed_payment_id=payment_id,
                )
                logger.error(
                    "conflicting capture on contract=%s: known=%s observed=%s",
                    contract_id, existing_payment, payment_id,
                )
                return

    # Re-read after the compare-and-swap. A competing capture may have won the
    # binding between the initial read and the transition walk; never let this
    # event advance the contract using a now-foreign payment id.
    latest_contract = STORE.get(contract_id) or contract
    existing_payment = latest_contract.get("razorpay_payment_id")
    status = str(latest_contract.get("status") or status)
    if existing_payment and str(existing_payment) != payment_id:
        _withhold_captured_event(
            contract_id,
            reason="conflicting_payment_capture",
            known_payment_id=existing_payment,
            observed_payment_id=payment_id,
        )
        logger.error(
            "conflicting capture after binding race on contract=%s: known=%s observed=%s",
            contract_id,
            existing_payment,
            payment_id,
        )
        return
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


def _refund_amount_paise(record: dict[str, Any]) -> int | None:
    """Normalize Razorpay's wire ``amount`` and Dante's stored field."""
    for key in ("amount_paise", "amount"):
        value = record.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def _processed_refund_totals(payment_id: str) -> tuple[int, list[str]]:
    """Return distinct processed refund total and ids for one payment."""
    total = 0
    refund_ids: list[str] = []
    seen: set[str] = set()
    for refund in STORE.find("razorpay_refund", payment_id=payment_id):
        if refund.get("status") not in (None, "processed", "paid"):
            continue
        refund_id = str(refund.get("id") or "")
        if refund_id and refund_id in seen:
            continue
        if refund_id:
            seen.add(refund_id)
            refund_ids.append(refund_id)
        amount = _refund_amount_paise(refund)
        if amount is not None:
            total += amount
    return total, refund_ids


def _refund_contract(
    payment_id: str,
    notes: dict[str, Any],
    refund_record: dict[str, Any] | None,
    *,
    order_id: str | None = None,
) -> dict[str, Any] | None:
    """Resolve a refund to a contract without trusting an unbound note alone.

    The payment binding is the normal path. A refund can legitimately arrive
    before the capture webhook, however, so a provider-supplied order id may
    resolve the contract while still requiring an exact Dante order binding.
    """
    if _refund_binding_conflict(payment_id, order_id, refund_record):
        return None

    contract = STORE.find_one("contract", razorpay_payment_id=payment_id) if payment_id else None
    if contract is not None:
        return contract

    if order_id:
        contract = STORE.find_one("contract", razorpay_order_id=order_id)
        if contract is not None:
            return contract

    note_contract_id = notes.get("contract_id")
    if not note_contract_id and refund_record:
        stored_notes = refund_record.get("notes")
        if isinstance(stored_notes, dict):
            note_contract_id = stored_notes.get("contract_id")
    if not note_contract_id:
        action = STORE.find_one("money_action", razorpay_payment_id=payment_id)
        note_contract_id = action.get("contract_id") if action else None
    if not note_contract_id:
        return None

    candidate = STORE.get(str(note_contract_id))
    if candidate is not None and candidate.get("_type") == "contract":
        payment_matches = bool(payment_id) and str(
            candidate.get("razorpay_payment_id") or ""
        ) == str(payment_id)
        order_matches = bool(order_id) and str(
            candidate.get("razorpay_order_id") or ""
        ) == str(order_id)
        if payment_matches or order_matches:
            return candidate
    return None


def _on_refund_processed(event_id: str, payload: dict[str, Any]) -> None:
    """Reconcile a gateway refund into refund, payment, and contract state.

    Refund webhooks can arrive after an executor response, before its local
    response is persisted, or for a dashboard/manual refund with no local
    money action. The refund id is therefore persisted first, totals are
    recomputed from distinct refund records, and the contract receives an
    explicit financial reconciliation marker instead of relying on one audit
    annotation.
    """
    entity = _extract_entity(payload, "refund") or {}
    refund_id = str(entity.get("id") or "")
    payment_id = str(entity.get("payment_id") or "")
    order_id = str(entity.get("order_id") or "") or None
    amount = entity.get("amount")
    raw_notes = entity.get("notes")
    notes: dict[str, Any] = raw_notes if isinstance(raw_notes, dict) else {}

    existing = STORE.get(refund_id) if refund_id else None
    if existing is not None and existing.get("_type") != "razorpay_refund":
        append_event(
            aggregate_type="razorpay",
            aggregate_id=event_id,
            event_type="STATE_RECONCILED",
            payload={
                "reason": "refund_id_conflict",
                "refund_id": refund_id,
                "payment_id": payment_id,
                "action": "refund_record_unchanged",
            },
        )
        return

    binding_conflict = _refund_binding_conflict(payment_id, order_id, existing)
    if binding_conflict:
        append_event(
            aggregate_type="razorpay",
            aggregate_id=refund_id or event_id,
            event_type="STATE_RECONCILED",
            payload={
                "reason": "refund_binding_conflict",
                "conflict": binding_conflict,
                "refund_id": refund_id,
                "payment_id": payment_id,
                "order_id": order_id,
                "action": "refund_event_withheld",
            },
        )
        return

    # Do not mint local refund/payment records from a signed event that is not
    # connected to any known Dante payment, contract, or money action. It is
    # still an auditable upstream observation, but treating a foreign refund
    # as one of Dante's would create a phantom financial effect.
    known_payment = STORE.get(payment_id) if payment_id else None
    known_contract = _refund_contract(payment_id, notes, existing, order_id=order_id)
    if existing is None and known_payment is None and known_contract is None:
        append_event(
            aggregate_type="razorpay",
            aggregate_id=event_id,
            event_type="STATE_RECONCILED",
            payload={
                "reason": "refund_event_without_known_payment",
                "refund_id": refund_id,
                "payment_id": payment_id,
                "action": "local_refund_record_withheld",
            },
        )
        return

    if refund_id and existing is None:
        refund_record: dict[str, Any] = {
            "_type": "razorpay_refund",
            "id": refund_id,
            "entity": "refund",
            "payment_id": payment_id,
            "amount": amount,
            "amount_paise": amount if isinstance(amount, int) else None,
            "currency": entity.get("currency", "INR"),
            "order_id": order_id,
            "status": "processed",
            "notes": notes,
            **(
                {"idempotency_key": str(notes["idempotency_key"])}
                if notes.get("idempotency_key")
                else {}
            ),
            "webhook_event_id": event_id,
            "webhook_event_ids": [event_id],
            "source": "webhook",
            "mode": service.mode(),
            "sandbox": service.mode() == "sandbox",
            "created_at": now_iso(),
        }
        existing = (
            refund_record if STORE.put_if_absent(refund_record) else STORE.get(refund_id)
        )
    elif existing is not None:
        event_ids = list(existing.get("webhook_event_ids") or [])
        if event_id not in event_ids:
            event_ids.append(event_id)
        updates: dict[str, Any] = {
            "status": "processed",
            "webhook_event_ids": event_ids,
        }
        if not existing.get("webhook_event_id"):
            updates["webhook_event_id"] = event_id
        # Preserve the original amount as the financial record. A conflicting
        # replay is audited below rather than silently changing the refund.
        if _refund_amount_paise(existing) is None and isinstance(amount, int):
            updates["amount"] = amount
            updates["amount_paise"] = amount
        STORE.update(refund_id, **updates)
        existing = STORE.get(refund_id) or existing

    stored_amount = _refund_amount_paise(existing or {})
    if stored_amount is not None and isinstance(amount, int) and amount != stored_amount:
        append_event(
            aggregate_type="razorpay",
            aggregate_id=event_id,
            event_type="STATE_RECONCILED",
            payload={
                "reason": "refund_amount_conflict",
                "refund_id": refund_id,
                "stored_amount_paise": stored_amount,
                "observed_amount_paise": amount,
                "action": "stored_refund_amount_unchanged",
            },
        )

    contract = _refund_contract(payment_id, notes, existing, order_id=order_id)
    aggregate_id = contract["id"] if contract else (refund_id or event_id)
    aggregate_type = "contract" if contract else "razorpay"

    total_refunded = 0
    refund_ids: list[str] = []
    if payment_id:
        total_refunded, refund_ids = _processed_refund_totals(payment_id)

        payment = STORE.get(payment_id)
        if payment is not None and payment.get("_type") == "razorpay_payment":
            prior = payment.get("amount_refunded")
            prior_amount = int(prior) if isinstance(prior, int) and prior >= 0 else 0
            STORE.update(
                payment_id,
                amount_refunded=max(prior_amount, total_refunded),
                refund_status="processed",
                processed_refund_ids=refund_ids,
                last_refund_id=refund_id or payment.get("last_refund_id"),
            )
        else:
            # Keep an out-of-order/manual refund visible even when its payment
            # capture webhook has not arrived yet. No captured amount is
            # invented; a bound contract may provide that later.
            payment_record: dict[str, Any] = {
                "_type": "razorpay_payment",
                "id": payment_id,
                "payment_id": payment_id,
                "entity": "payment",
                "amount_refunded": total_refunded,
                "refund_status": "processed",
                "processed_refund_ids": refund_ids,
                "last_refund_id": refund_id or None,
                "currency": entity.get("currency", "INR"),
                "order_id": order_id,
                "status": "unknown",
                "source": "refund_webhook",
                "mode": service.mode(),
                "sandbox": service.mode() == "sandbox",
            }
            if contract and isinstance(contract.get("amount_paise"), int):
                payment_record["amount"] = contract["amount_paise"]
                payment_record["amount_paise"] = contract["amount_paise"]
            STORE.put_if_absent(payment_record)

    money_action = STORE.find_one("money_action", result_ref=refund_id) if refund_id else None
    if money_action is None and existing:
        idem = existing.get("idempotency_key")
        if idem:
            money_action = STORE.find_one("money_action", idempotency_key=idem)
    if money_action is not None and money_action.get("status") in {
        "proposed",
        "allowed",
        "approval_required",
        "executing",
    }:
        STORE.update(
            money_action["id"],
            status="executed",
            result_ref=refund_id or money_action.get("result_ref"),
            executed_at=money_action.get("executed_at") or now_iso(),
        )

    lifecycle_action = "financially_reconciled"
    if contract:
        contract_amount = contract.get("amount_paise")
        full = (
            isinstance(contract_amount, int)
            and contract_amount > 0
            and total_refunded >= contract_amount
        )
        STORE.update(
            contract["id"],
            refunded_amount_paise=total_refunded,
            refund_status="fully_refunded" if full else "partially_refunded",
            refund_reconciled=True,
            refund_reconciled_at=now_iso(),
            last_refund_id=refund_id or contract.get("last_refund_id"),
        )
        current_status = str(contract.get("status") or "")
        if full and current_status in {
            "BREACH_DETECTED",
            "REMEDY_PLANNING",
            "AWAITING_REMEDY_APPROVAL",
            "REMEDY_EXECUTING",
        }:
            # A gateway-confirmed full refund is sufficient to close a breach
            # even when the webhook races the local executor. The helper walks
            # only the remedy subgraph and never teleports a merely PAID order.
            try:
                latest = STORE.get(contract["id"]) or contract
                if latest.get("status") != "REMEDIATED":
                    from project_dante.domain.money.policy import _transition_contract

                    _transition_contract(contract["id"], "REMEDIATED")
                lifecycle_action = "breach_remediated_by_refund"
                append_event(
                    aggregate_type="contract",
                    aggregate_id=contract["id"],
                    event_type="CONTRACT_REMEDIATED",
                    payload={
                        "refund_id": refund_id,
                        "amount_paise": total_refunded,
                        "source": "refund_webhook",
                        "out_of_band": money_action is None,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - retain financial marker
                logger.warning(
                    "refund lifecycle transition deferred contract=%s: %s",
                    contract["id"], str(exc)[:200],
                )

        if full and lifecycle_action == "financially_reconciled":
            append_event(
                aggregate_type="contract",
                aggregate_id=contract["id"],
                event_type="STATE_RECONCILED",
                payload={
                    "reason": "full_refund_without_remedy_transition",
                    "refund_id": refund_id,
                    "amount_paise": total_refunded,
                    "from_status": current_status,
                    "action": "financially_reconciled",
                },
            )

    append_event(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type="REFUND_PROCESSED",
        payload={
            "refund_id": refund_id,
            "payment_id": payment_id,
            "amount_paise": stored_amount if stored_amount is not None else amount,
            "total_refunded_paise": total_refunded,
            "money_action_id": money_action.get("id") if money_action else None,
            "out_of_band": money_action is None,
            "lifecycle_action": lifecycle_action,
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
