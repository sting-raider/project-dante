"""Agent B — Razorpay service layer tests (sandbox adapter).

Covers: mode selection, order creation shape, checkout-signature verification
(happy + forged), webhook-signature verification (happy + forged), refund
idempotency, payment fetch round-trip, and pure signature math.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest
from project_dante.db.store import STORE
from project_dante.domain.events import LOG
from project_dante.integrations.razorpay import service
from project_dante.settings import get_settings

TEST_WEBHOOK_SECRET = "dante-test-webhook-secret"


@pytest.fixture()
def sandbox_env(tmp_path, monkeypatch):
    """Force sandbox mode + deterministic secret; isolate STORE and event log."""
    monkeypatch.setenv("RAZORPAY_KEY_ID", "")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET)
    get_settings.cache_clear()

    saved_records = dict(STORE._records)
    saved_path = STORE._path
    STORE._path = str(tmp_path / "store-b-service.json")
    STORE.reset()
    saved_events = list(LOG._events)
    LOG.reset()
    yield
    STORE._records.clear()
    STORE._records.update(saved_records)
    STORE._path = saved_path
    LOG._events.clear()
    LOG._events.extend(saved_events)
    get_settings.cache_clear()


def _hmac_hex(key: str, msg: bytes) -> str:
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()


# ------------------------------------------------------------------- mode


def test_mode_sandbox_without_keys(sandbox_env):
    assert service.mode() == "sandbox"
    # Honest checkout surface: no fake key handed to the browser.
    assert service.key_id_public() == ""


# ------------------------------------------------------------------ orders


def test_create_order_sandbox_shape(sandbox_env):
    order = service.create_order(1149900, receipt="dante:c1", notes={"contract_id": "c1"})
    assert order["id"].startswith("order_") and len(order["id"]) == len("order_") + 14
    assert isinstance(order["amount"], int) and order["amount"] == 1149900
    assert order["currency"] == "INR"
    assert order["sandbox"] is True  # honest labeling invariant

    stored = STORE.get(order["id"])
    assert stored is not None and stored["_type"] == "razorpay_order"

    bad = order.copy()
    del bad["_type"], bad["id"]
    assert service.mode() == "sandbox"


@pytest.mark.parametrize("bad_amount", [0, -5, 1.5])
def test_create_order_rejects_non_positive_paise(sandbox_env, bad_amount):
    with pytest.raises(ValueError):
        service.create_order(bad_amount)


# ------------------------------------------------------ checkout signature


def test_checkout_signature_happy(sandbox_env):
    order = service.create_order(500000)
    payment_id = "pay_TestPayment123"
    expected = _hmac_hex(service.SANDBOX_KEY_SECRET, f"{order['id']}|{payment_id}".encode())
    assert service.verify_checkout_signature(order["id"], payment_id, expected) is True


def test_checkout_signature_forged(sandbox_env):
    order = service.create_order(500000)
    forged = _hmac_hex("attacker-secret", f"{order['id']}|pay_X".encode())
    assert service.verify_checkout_signature(order["id"], pay_id := "pay_X", forged) is False
    # Tampered pair under a valid-looking digest also fails.
    real = _hmac_hex(service.SANDBOX_KEY_SECRET, f"{order['id']}|{pay_id}".encode())
    assert service.verify_checkout_signature(order["id"], "pay_OTHER", real) is False


def test_checkout_signature_empty_inputs_fail_closed(sandbox_env):
    assert service.verify_checkout_signature("", "p", "s") is False
    assert service.verify_checkout_signature("o", "", "s") is False
    assert service.verify_checkout_signature("o", "p", "") is False


def test_checkout_signature_math_matches_razorpay_recipe():
    """Pure-function check independent of adapters/settings."""
    from project_dante.integrations.razorpay.client import compute_checkout_signature

    got = compute_checkout_signature("order_ABC", "pay_XYZ", secret="k")
    assert got == hmac.new(b"k", b"order_ABC|pay_XYZ", hashlib.sha256).hexdigest()


# ------------------------------------------------------- webhook signature


def test_webhook_signature_happy(sandbox_env):
    body = b'{"event":"payment.captured","payload":{}}'
    sig = service.sign_webhook_payload(body)
    assert sig == _hmac_hex(TEST_WEBHOOK_SECRET, body)
    assert service.verify_webhook_signature(body, sig) is True


def test_webhook_signature_forged(sandbox_env):
    body = b'{"event":"payment.captured","payload":{}}'
    assert service.verify_webhook_signature(body, _hmac_hex("wrong", body)) is False


def test_webhook_signature_body_tamper_detected(sandbox_env):
    body = b'{"event":"payment.captured","amount":100}'
    sig = service.sign_webhook_payload(body)
    tampered = b'{"event":"payment.captured","amount":99000000}'
    assert service.verify_webhook_signature(tampered, sig) is False


def test_webhook_signature_empty_inputs_fail_closed(sandbox_env):
    assert service.verify_webhook_signature(b"", "sig") is False
    assert service.verify_webhook_signature(b"body", "") is False


# ----------------------------------------------------------------- refunds


def test_refund_idempotency_same_key_single_effect(sandbox_env):
    order = service.create_order(1149900)
    payment = service.capture_sandbox_payment(order["id"])

    r1 = service.create_refund(payment["id"], amount_paise=1149900, idempotency_key="rem_abc:v1")
    r2 = service.create_refund(payment["id"], amount_paise=1149900, idempotency_key="rem_abc:v1")

    assert r1["id"] == r2["id"], "retry with same key must return the original refund"
    assert r1["amount"] == 1149900
    stored = STORE.find("razorpay_refund", idempotency_key="rem_abc:v1")
    assert len(stored) == 1, "one idempotency key => exactly one refund record"


def test_refund_different_keys_are_distinct_effects(sandbox_env):
    order = service.create_order(100000)
    payment = service.capture_sandbox_payment(order["id"])
    r1 = service.create_refund(payment["id"], amount_paise=30000, idempotency_key="late-sla")
    r2 = service.create_refund(payment["id"], amount_paise=20000, idempotency_key="missing-cable")
    assert r1["id"] != r2["id"]
    refreshed = service.fetch_payment(payment["id"])
    assert refreshed["amount_refunded"] == 50000


def test_refund_rejects_over_refund(sandbox_env):
    order = service.create_order(100000)
    payment = service.capture_sandbox_payment(order["id"])
    from project_dante.integrations.razorpay.client import RazorpayError

    with pytest.raises(RazorpayError):
        service.create_refund(payment["id"], amount_paise=200000, idempotency_key="too-much")


# ------------------------------------------------------------ fetch/payment


def test_fetch_payment_roundtrip_and_miss(sandbox_env):
    order = service.create_order(250000)
    payment = service.capture_sandbox_payment(order["id"])
    fetched = service.fetch_payment(payment["id"])
    assert fetched is not None and fetched["status"] == "captured"
    assert fetched["order_id"] == order["id"]
    assert service.fetch_payment("pay_doesNotExist999") is None


def test_fetch_order_payments_lists_only_that_order(sandbox_env):
    o1 = service.create_order(100000)
    o2 = service.create_order(200000)
    p1 = service.capture_sandbox_payment(o1["id"])
    service.capture_sandbox_payment(o2["id"])
    ids = [p["id"] for p in service.fetch_order_payments(o1["id"])]
    assert ids == [p1["id"]]
