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


def _close_client(client: Any) -> None:
    """Release a per-call live HTTP client without coupling to the sandbox."""
    close = getattr(client, "close", None)
    if callable(close):
        close()


# ------------------------------------------------------------------- orders


def create_order(
    amount_paise: int, receipt: str = "", notes: dict[str, Any] | None = None
) -> dict:
    """Create a Razorpay order (real Test Mode or sandbox record)."""
    client = get_client()
    try:
        return client.create_order(amount_paise, receipt=receipt, notes=notes)
    finally:
        _close_client(client)


# -------------------------------------------------------------- verification


def verify_checkout_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """HMAC-SHA256 of ``"{order_id}|{payment_id}"`` under key_secret."""
    if not (order_id and payment_id and signature):
        return False
    expected = compute_checkout_signature(order_id, payment_id)
    return hmac_equal(expected, signature)


_DEFAULT_WEBHOOK_SECRET = "dante-dev-webhook-secret"


def _webhook_secret() -> str:
    """Effective webhook secret; fails CLOSED in live-test mode.

    Review finding: the repo-default secret stayed armed when operators
    dropped in real test keys, letting anyone who read the public repo forge
    payment.captured webhooks that grant PAID. In live-test mode a default
    secret is treated as unconfigured -> verification always fails.
    """
    settings = get_settings()
    secret = settings.razorpay_webhook_secret
    if settings.razorpay_live_test_mode and (
        not secret or secret == _DEFAULT_WEBHOOK_SECRET
    ):
        raise WebhookSecretUnconfigured(
            "RAZORPAY_WEBHOOK_SECRET must be set to your dashboard webhook "
            "secret before live Test Mode webhooks can be verified"
        )
    return secret


class WebhookSecretUnconfigured(RuntimeError):
    """Live-test mode active but the webhook secret is still the default."""


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """HMAC-SHA256 hex of the RAW webhook body under the webhook secret."""
    if not raw_body or not signature:
        return False
    try:
        secret = _webhook_secret()
    except WebhookSecretUnconfigured:
        return False  # fail closed: no verification without a real secret
    expected = compute_webhook_signature(raw_body, secret)
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
    try:
        return client.fetch_payment(payment_id)
    finally:
        _close_client(client)


def fetch_order_payments(order_id: str) -> list[dict]:
    client = get_client()
    try:
        return client.fetch_order_payments(order_id)
    finally:
        _close_client(client)


def fetch_order_by_receipt(receipt: str) -> dict | None:
    """Find a gateway order by its merchant receipt for lost-response recovery."""
    client = get_client()
    try:
        return client.fetch_order_by_receipt(receipt)
    finally:
        _close_client(client)


def capture_sandbox_payment(order_id: str, payment_id: str | None = None) -> dict:
    """Sandbox-only stand-in for Razorpay's own capture step (demo path)."""
    client = get_client()
    try:
        if not isinstance(client, SandboxClient):
            raise RazorpayError("capture_sandbox_payment is sandbox-only")
        return client.capture_sandbox_payment(order_id, payment_id)
    finally:
        _close_client(client)


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
    client = get_client()
    try:
        return client.create_refund(
            payment_id,
            amount_paise=amount_paise,
            idempotency_key=idempotency_key,
            notes=notes,
        )
    finally:
        _close_client(client)


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
    "fetch_order_by_receipt",
    "capture_sandbox_payment",
    "create_refund",
]
