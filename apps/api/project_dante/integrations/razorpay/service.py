"""Razorpay integration service — the ONLY import surface other agents use.

Frozen interface (docs/API_CONTRACT.md):

    mode() -> str
    create_order(amount_paise, receipt="", notes=None) -> dict
    verify_checkout_signature(order_id, payment_id, signature) -> bool
    verify_webhook_signature(raw_body: bytes, signature: str) -> bool
    fetch_payment(payment_id) -> dict | None
    create_refund(payment_id, amount_paise=None, idempotency_key="", notes=None) -> dict

Money is integer paise. Client success is never final truth: a captured
payment becomes authoritative only through the signature-verified webhook
path (routes/webhooks.py). No LLM code touches this module.
"""

from __future__ import annotations

import hmac
from typing import Any

from project_dante.db.store import STORE
from project_dante.integrations.razorpay.client import (
    SANDBOX_KEY_ID,
    SANDBOX_KEY_SECRET,
    RazorpayError,
    SandboxClient,
    compute_checkout_signature,
    compute_webhook_signature,
    get_client,
)
from project_dante.settings import get_settings


def mode() -> str:
    """``"live-test-mode"`` when real test keys are configured, else ``"sandbox"``."""
    return "live-test-mode" if get_settings().razorpay_live_test_mode else "sandbox"


def key_id_public() -> str:
    """Checkout key_id for the frontend. Empty string in sandbox (honest).

    The sandbox has no real gateway account, so there is deliberately no key
    to hand the browser — the demo UI treats "" as "use the simulated checkout".
    """
    settings = get_settings()
    if settings.razorpay_live_test_mode:
        return settings.razorpay_key_id
    return ""


# ------------------------------------------------------------------- orders


def create_order(
    amount_paise: int, receipt: str = "", notes: dict[str, Any] | None = None
) -> dict:
    """Create a Razorpay order (real Test Mode or sandbox record)."""
    return get_client().create_order(amount_paise, receipt=receipt, notes=notes)


# -------------------------------------------------------------- verification


def verify_checkout_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """HMAC-SHA256 of ``"{order_id}|{payment_id}"`` under key_secret."""
    if not (order_id and payment_id and signature):
        return False
    expected = compute_checkout_signature(order_id, payment_id)
    return hmac_equal(expected, signature)


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """HMAC-SHA256 hex of the RAW webhook body under the webhook secret."""
    if not raw_body or not signature:
        return False
    expected = compute_webhook_signature(raw_body, get_settings().razorpay_webhook_secret)
    return hmac_equal(expected, signature)


def sign_webhook_payload(raw_body: bytes) -> str:
    """Compute the signature FOR a payload — sandbox/demo helper.

    Used by ``/api/demo/razorpay/simulate-event`` to build genuinely signed
    payloads; never exposed on any non-demo route.
    """
    return compute_webhook_signature(raw_body, get_settings().razorpay_webhook_secret)


def hmac_equal(expected_hex: str, provided: str) -> bool:
    """Constant-time hex comparison; malformed input simply fails."""
    try:
        provided_bytes = provided.strip().encode("utf-8")
    except AttributeError:
        return False
    return hmac.compare_digest(expected_hex.encode("utf-8"), provided_bytes)


# ---------------------------------------------------------------- payments


def fetch_payment(payment_id: str) -> dict | None:
    """Current gateway-side view of a payment; None when unknown/unreachable."""
    client = get_client()
    if isinstance(client, SandboxClient):
        return client.fetch_payment(payment_id)
    return client.fetch_payment(payment_id)


def fetch_order_payments(order_id: str) -> list[dict]:
    client = get_client()
    if isinstance(client, SandboxClient):
        return client.fetch_order_payments(order_id)
    return client.fetch_order_payments(order_id)


def capture_sandbox_payment(order_id: str, payment_id: str | None = None) -> dict:
    """Sandbox-only stand-in for Razorpay's own capture step (demo path)."""
    client = get_client()
    if not isinstance(client, SandboxClient):
        raise RazorpayError("capture_sandbox_payment is sandbox-only")
    return client.capture_sandbox_payment(order_id, payment_id)


# ----------------------------------------------------------------- refunds


def create_refund(
    payment_id: str,
    amount_paise: int | None = None,
    idempotency_key: str = "",
    notes: dict[str, Any] | None = None,
) -> dict:
    """Idempotent refund: same idempotency_key => identical refund, one effect.

    The STORE-level check lives inside both adapters, so retries after network
    failure and deliberate replays converge on the original refund record.
    """
    return get_client().create_refund(
        payment_id, amount_paise=amount_paise, idempotency_key=idempotency_key, notes=notes
    )


__all__ = [
    "SANDBOX_KEY_ID",
    "SANDBOX_KEY_SECRET",
    "mode",
    "key_id_public",
    "create_order",
    "verify_checkout_signature",
    "verify_webhook_signature",
    "sign_webhook_payload",
    "fetch_payment",
    "fetch_order_payments",
    "capture_sandbox_payment",
    "create_refund",
]
