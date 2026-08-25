"""Agent B — webhook route tests (signature gate, dedupe, reconciliation).

These mount ONLY the payments + webhooks routers on a minimal FastAPI app so
the suite stays green regardless of parallel work in other agents' modules.
The full-flow test drives contract → order → simulate-event → PAID through
the same public HTTP surface the demo uses.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from project_dante.db.store import STORE
from project_dante.domain.events import LOG
from project_dante.settings import get_settings

TEST_WEBHOOK_SECRET = "dante-test-webhook-secret"


def _make_app() -> FastAPI:
    from project_dante.api.routes.payments import router as payments_router
    from project_dante.api.routes.webhooks import router as webhooks_router

    app = FastAPI()
    app.include_router(payments_router, prefix="/api")
    app.include_router(webhooks_router, prefix="/api")
    return app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET)
    monkeypatch.setenv("DEMO_MODE", "true")
    get_settings.cache_clear()

    saved_records = dict(STORE._records)
    saved_path = STORE._path
    STORE._path = str(tmp_path / "store-b-webhooks.json")
    STORE.reset()
    LOG.reset()
    with TestClient(_make_app()) as c:
        yield c
    STORE._records.clear()
    STORE._records.update(saved_records)
    STORE._path = saved_path
    LOG._events.clear()
    get_settings.cache_clear()


# ------------------------------------------------------------------ helpers


def sign(body: bytes, secret: str = TEST_WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def captured_envelope(order_id: str, payment_id: str, amount: int, event_id: str | None = None):
    payload = {
        "event": "payment.captured",
        "id": event_id or f"evt_{uuid.uuid4().hex[:12]}",
        "created_at": int(time.time()),
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "amount": amount,
                    "currency": "INR",
                    "status": "captured",
                    "order_id": order_id,
                    "captured": True,
                }
            }
        },
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return raw, sign(raw), payload["id"]


def make_authorized_contract(amount_paise: int = 1149900, *, frozen_hash: bool = True) -> dict:
    """Seed offer + promise + authorized contract exactly as Agents C/D would.

    Freeze order matters (the drift check enforces it): promise gets its
    contract backlink BEFORE the promise-set/contract hashes are computed.
    """
    from project_dante.domain.hashing import sha256_hex

    offer = {
        "_type": "offer",
        "id": f"off_{uuid.uuid4().hex[:10]}",
        "sku": "AST-ANC-PRO",
        "title": "Aster ANC Pro",
        "unit_amount_paise": amount_paise,
        "currency": "INR",
    }
    STORE.put(offer)
    contract_id = f"con_{uuid.uuid4().hex[:10]}"
    promise = {
        "_type": "promise",
        "id": f"pr_{uuid.uuid4().hex[:10]}",
        "contract_id": contract_id,
        "key": "warranty.type",
        "value": "manufacturer",
    }
    STORE.put(promise)
    promises = [STORE.get(promise["id"])]
    promise_set_hash = sha256_hex(promises)
    contract_hash = sha256_hex(
        {"offer": {k: v for k, v in offer.items() if k != "_type"}, "promise_set_hash": promise_set_hash}
    )
    contract = {
        "_type": "contract",
        "id": contract_id,
        "intent_id": "int_test",
        "offer_id": offer["id"],
        "promise_ids": [promise["id"]],
        "buyer_authority": {
            "max_amount_paise": amount_paise,
            "currency": "INR",
            "scope": "single_purchase",
        },
        "offer_hash": "x" * 64,
        "promise_set_hash": promise_set_hash,
        "amount_paise": amount_paise,
        "status": "AWAITING_BUYER_AUTH",
    }
    if frozen_hash:
        contract["contract_hash"] = contract_hash
    STORE.put(contract)
    return contract


def events_for(contract_id: str, etype: str) -> list[dict]:
    return [e for e in LOG.all() if e.get("aggregate_id") == contract_id and e.get("event_type") == etype]


# ------------------------------------------------------- signature security


def test_forged_webhook_is_401_and_stores_nothing(client):
    body = json.dumps({"event": "payment.captured", "payload": {"payment": {"entity": {}}}}).encode()
    r = client.post(
        "/api/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sign(body, secret="attacker-key")},
    )
    assert r.status_code == 401
    assert len(STORE.list("webhook_event")) == 0, "unverified bytes must never be stored"
    assert not any(e["event_type"] == "WEBHOOK_RECEIVED" for e in LOG.all())


def test_missing_signature_header_is_401(client):
    body = b'{"event":"ping"}'
    r = client.post("/api/webhooks/razorpay", content=body)
    assert r.status_code == 401


def test_valid_webhook_accepted_and_parsed(client):
    body = json.dumps({"event": "payment.authorized", "payload": {"payment": {"entity": {}}}}).encode()
    r = client.post("/api/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": sign(body)})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert len(STORE.list("webhook_event")) == 1


# ------------------------------------------------------------------ duplicates


def test_duplicate_event_x5_single_domain_effect(client):
    contract = make_authorized_contract()
    order_r = client.post(f"/api/contracts/{contract['id']}/payment-order")
    assert order_r.status_code == 200, order_r.text
    order_id = order_r.json()["checkout_config"]["order_id"]
    payment_id = "pay_DupTest0000001"

    raw, sig, event_id = captured_envelope(order_id, payment_id, 1149900)
    for i in range(5):
        r = client.post("/api/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sig})
        assert r.status_code == 200
        assert r.json().get("duplicate") is (i > 0)

    stored_events = STORE.find("webhook_event", id=event_id)
    assert len(stored_events) == 1, "event body stored once"

    dup_markers = [
        e
        for e in LOG.all()
        if e.get("aggregate_id") == event_id and e.get("event_type") == "WEBHOOK_DUPLICATE_IGNORED"
    ]
    assert len(dup_markers) == 4, "4 replays ignored, each audited"

    captures = events_for(contract["id"], "RAZORPAY_PAYMENT_CAPTURED")
    assert len(captures) == 1, "exactly one domain effect"
    refreshed = STORE.get(contract["id"])
    assert refreshed["status"] == "PAID"
    assert refreshed["razorpay_payment_id"] == payment_id


# ------------------------------------------------------------ out-of-order


def test_captured_before_pending_reconciles_to_paid(client):
    """Capture arrives while contract is still AWAITING_BUYER_AUTH (client
    never called verify). The webhook walks the legal path to PAID and logs
    STATE_RECONCILED hops honestly."""
    contract = make_authorized_contract()
    order_r = client.post(f"/api/contracts/{contract['id']}/payment-order")
    order_id = order_r.json()["checkout_config"]["order_id"]
    # Force the out-of-order condition: rewind status behind order creation.
    STORE.update(contract["id"], status="AWAITING_BUYER_AUTH")

    raw, sig, _ = captured_envelope(order_id, "pay_OOO00000000001", 1149900)
    r = client.post("/api/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sig})
    assert r.status_code == 200

    refreshed = STORE.get(contract["id"])
    assert refreshed["status"] == "PAID", "gateway capture is server truth; projection catches up"
    reconciles = events_for(contract["id"], "STATE_RECONCILED")
    assert reconciles, "reconciliation hops are documented, not silent"
    assert len(events_for(contract["id"], "RAZORPAY_PAYMENT_CAPTURED")) == 1


def test_captured_with_wrong_amount_never_grants_paid(client):
    contract = make_authorized_contract()
    order_r = client.post(f"/api/contracts/{contract['id']}/payment-order")
    order_id = order_r.json()["checkout_config"]["order_id"]
    STORE.update(contract["id"], status="PAYMENT_PENDING")

    raw, sig, _ = captured_envelope(order_id, "pay_AmountHack001", 999999999)
    r = client.post("/api/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sig})
    assert r.status_code == 200

    refreshed = STORE.get(contract["id"])
    assert refreshed["status"] == "PAYMENT_PENDING", "amount mismatch must NOT grant PAID"
    mismatch = [e for e in events_for(contract["id"], "STATE_RECONCILED") if e["payload"].get("reason") == "captured_amount_mismatch"]
    assert mismatch, "mismatch recorded for audit"


def test_captured_after_paid_is_idempotent_no_regression(client):
    contract = make_authorized_contract()
    client.post(f"/api/contracts/{contract['id']}/payment-order")
    STORE.update(contract["id"], status="FULFILLING")

    raw, sig, _ = captured_envelope(
        STORE.get(contract["id"])["razorpay_order_id"], "pay_LateArrival001", 1149900
    )
    r = client.post("/api/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sig})
    assert r.status_code == 200
    assert STORE.get(contract["id"])["status"] == "FULFILLING", "never regress past PAID"


# ------------------------------------------------------------------- refunds


def test_refund_processed_webhook_appends_event(client):
    contract = make_authorized_contract()
    STORE.update(contract["id"], razorpay_payment_id="pay_Refunded00001")
    before = len(events_for(contract["id"], "REFUND_PROCESSED"))

    payload = {
        "event": "refund.processed",
        "id": f"evt_{uuid.uuid4().hex[:12]}",
        "payload": {"refund": {"entity": {"id": "rf_TestRefund00001", "payment_id": "pay_Refunded00001", "amount": 50000}}},
    }
    raw = json.dumps(payload).encode()
    r = client.post("/api/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sign(raw)})
    assert r.status_code == 200
    assert len(events_for(contract["id"], "REFUND_PROCESSED")) == before + 1


# ------------------------------------------------------------ verify-client


def test_verify_client_happy_then_webhook_grants_paid(client):
    contract = make_authorized_contract()
    order_r = client.post(f"/api/contracts/{contract['id']}/payment-order")
    order_id = order_r.json()["checkout_config"]["order_id"]
    payment_id = "pay_ClientVerify001"
    sig = sign(f"{order_id}|{payment_id}".encode(), secret=__import__(
        "project_dante.integrations.razorpay.client", fromlist=["SANDBOX_KEY_SECRET"]
    ).SANDBOX_KEY_SECRET)

    r = client.post(
        "/api/payments/verify-client",
        json={
            "contract_id": contract["id"],
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "signature": sig,
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "client_confirmed"
    assert data["contract_status"] == "PAYMENT_PENDING", "verify-client must NOT set PAID"
    assert len(events_for(contract["id"], "CHECKOUT_COMPLETED_CLIENT")) == 1
    assert len(events_for(contract["id"], "PAYMENT_VERIFIED_SERVER")) == 1

    bad_sig = sign(f"{order_id}|{payment_id}".encode(), secret="wrong")
    r2 = client.post(
        "/api/payments/verify-client",
        json={
            "contract_id": contract["id"],
            "razorpay_order_id": order_id,
            "razorpay_payment_id": "pay_Forged99999999",
            "signature": bad_sig,
        },
    )
    assert r2.status_code == 400


def test_verify_client_rejects_order_mismatch(client):
    contract = make_authorized_contract()
    client.post(f"/api/contracts/{contract['id']}/payment-order")
    r = client.post(
        "/api/payments/verify-client",
        json={
            "contract_id": contract["id"],
            "razorpay_order_id": "order_TotallyOther00",
            "razorpay_payment_id": "pay_whatever000001",
            "signature": "aa",
        },
    )
    assert r.status_code == 403


# ------------------------------------------------------------- payment-order


def test_payment_order_requires_authorization_and_valid_status(client):
    contract = make_authorized_contract()
    STORE.update(contract["id"], buyer_authority=None)
    r = client.post(f"/api/contracts/{contract['id']}/payment-order")
    assert r.status_code == 409

    contract2 = make_authorized_contract()
    STORE.update(contract2["id"], status="CONTRACT_FROZEN")
    r2 = client.post(f"/api/contracts/{contract2['id']}/payment-order")
    assert r2.status_code == 409

    r3 = client.post("/api/contracts/con_doesNotExist/payment-order")
    assert r3.status_code == 404


def test_payment_order_detects_contract_drift(client):
    """Executor re-check: tamper the stored OFFER after freeze ⇒ hash mismatch
    ⇒ 409 contract_drift and NO Razorpay order exists anywhere."""
    contract = make_authorized_contract()
    offer = STORE.get(contract["offer_id"])
    STORE.update(offer["id"], unit_amount_paise=999999999)  # post-freeze mutation

    r = client.post(f"/api/contracts/{contract['id']}/payment-order")
    assert r.status_code == 409
    assert r.json()["detail"] == "contract_drift"

    refreshed = STORE.get(contract["id"])
    assert refreshed["status"] == "AWAITING_BUYER_AUTH"
    assert refreshed.get("razorpay_order_id") is None
    assert len(STORE.list("razorpay_order")) == 0, "drift blocks any gateway effect"


def test_payment_order_idempotent_reentry_same_order(client):
    contract = make_authorized_contract()
    r1 = client.post(f"/api/contracts/{contract['id']}/payment-order").json()
    r2 = client.post(f"/api/contracts/{contract['id']}/payment-order").json()
    assert r1["checkout_config"]["order_id"] == r2["checkout_config"]["order_id"]
    assert len(STORE.list("razorpay_order")) == 1


def test_payment_order_response_shape_per_contract_doc(client):
    contract = make_authorized_contract()
    r = client.post(f"/api/contracts/{contract['id']}/payment-order")
    data = r.json()
    assert set(data.keys()) >= {"mode", "razorpay_order", "checkout_config"}
    cfg = data["checkout_config"]
    assert set(cfg.keys()) == {"key_id", "order_id", "amount_paise", "currency"}
    assert data["mode"] == "sandbox"
    assert cfg["key_id"] == "", "sandbox hands no key to the browser"
    assert cfg["currency"] == "INR"


# ------------------------------------------------------- demo simulate-event


def test_simulate_event_full_flow_contract_to_paid(client):
    contract = make_authorized_contract()
    order_r = client.post(f"/api/contracts/{contract['id']}/payment-order")
    order_id = order_r.json()["checkout_config"]["order_id"]

    sim = client.post(
        "/api/demo/razorpay/simulate-event",
        json={"event_type": "payment.captured", "order_id": order_id},
    )
    assert sim.status_code == 200, sim.text
    body = sim.json()
    assert body["delivered"] is True
    assert body["synthetic"] is True, "demo simulation labeled honestly"

    refreshed = STORE.get(contract["id"])
    assert refreshed["status"] == "PAID", "full flow: contract → order → simulate-event → PAID"
    assert refreshed["razorpay_payment_id"].startswith("pay_")
    assert len(events_for(contract["id"], "RAZORPAY_ORDER_CREATED")) == 1
    assert len(events_for(contract["id"], "RAZORPAY_PAYMENT_CAPTURED")) == 1
    # The simulated payload crossed the REAL verification machinery.
    wh = STORE.list("webhook_event")
    assert len(wh) == 1 and wh[0]["event_type"] == "payment.captured"


def test_simulate_event_guarded_off_when_live_keys_present(client, monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_LiveKey12345678")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "live-secret-value")
    get_settings.cache_clear()
    try:
        r = client.post(
            "/api/demo/razorpay/simulate-event",
            json={"event_type": "payment.captured", "order_id": "order_x"},
        )
        assert r.status_code == 403, "simulate-event forbidden once real keys exist"
    finally:
        monkeypatch.delenv("RAZORPAY_KEY_ID")
        monkeypatch.delenv("RAZORPAY_KEY_SECRET")
        get_settings.cache_clear()


def test_simulate_event_rejects_unknown_order(client):
    r = client.post(
        "/api/demo/razorpay/simulate-event",
        json={"event_type": "payment.captured", "order_id": "order_Nonexistent00"},
    )
    assert r.status_code == 404


def test_simulate_event_duplicate_delivery_is_one_effect(client):
    contract = make_authorized_contract()
    order_id = client.post(f"/api/contracts/{contract['id']}/payment-order").json()["checkout_config"]["order_id"]
    pid = "pay_SimDupTarget01"

    for _ in range(2):
        r = client.post(
            "/api/demo/razorpay/simulate-event",
            json={"event_type": "payment.captured", "order_id": order_id, "payment_id": pid},
        )
        assert r.status_code == 200

    assert len(events_for(contract["id"], "RAZORPAY_PAYMENT_CAPTURED")) == 1
