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
from project_dante.integrations.razorpay.client import SANDBOX_KEY_SECRET
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


def captured_envelope(
    order_id: str,
    payment_id: str,
    amount: object,
    event_id: str | None = None,
    amount_refunded: object | None = None,
):
    entity = {
        "id": payment_id,
        "amount": amount,
        "currency": "INR",
        "status": "captured",
        "order_id": order_id,
        "captured": True,
    }
    if amount_refunded is not None:
        entity["amount_refunded"] = amount_refunded
    payload = {
        "event": "payment.captured",
        "id": event_id or f"evt_{uuid.uuid4().hex[:12]}",
        "created_at": int(time.time()),
        "payload": {
            "payment": {"entity": entity}
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
    offer_view = {k: v for k, v in offer.items() if k != "_type"}
    contract_hash = sha256_hex({"offer": offer_view, "promise_set_hash": promise_set_hash})
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
    return [
        e
        for e in LOG.all()
        if e.get("aggregate_id") == contract_id and e.get("event_type") == etype
    ]


# ------------------------------------------------------- signature security


def test_forged_webhook_is_401_and_stores_nothing(client):
    body = json.dumps(
        {"event": "payment.captured", "payload": {"payment": {"entity": {}}}}
    ).encode()
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
    body = json.dumps(
        {
            "event": "payment.authorized",
            "id": "evt_valid_timestamp",
            "created_at": int(time.time()),
            "payload": {"payment": {"entity": {}}},
        }
    ).encode()
    r = client.post(
        "/api/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": sign(body)}
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert len(STORE.list("webhook_event")) == 1


@pytest.mark.parametrize(
    ("offset_seconds", "error"),
    [
        (301, "webhook_created_at_stale"),
        (-301, "webhook_created_at_in_future"),
    ],
)
def test_signed_webhook_outside_freshness_window_is_rejected_before_persistence(
    client, offset_seconds, error
):
    body = json.dumps(
        {
            "event": "payment.authorized",
            "id": "evt_stale_timestamp",
            "created_at": int(time.time()) - offset_seconds,
            "payload": {"payment": {"entity": {}}},
        }
    ).encode()

    response = client.post(
        "/api/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sign(body)},
    )

    assert response.status_code == 400
    assert response.json()["error"] == error
    assert STORE.list("webhook_event") == []
    assert not any(event["event_type"] == "WEBHOOK_RECEIVED" for event in LOG.all())


def test_signed_webhook_without_created_at_is_rejected_before_persistence(client):
    body = json.dumps(
        {
            "event": "payment.authorized",
            "id": "evt_missing_timestamp",
            "payload": {"payment": {"entity": {}}},
        }
    ).encode()

    response = client.post(
        "/api/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sign(body)},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "webhook_created_at_invalid"
    assert STORE.list("webhook_event") == []


def test_known_failed_stale_webhook_can_be_reclaimed(client):
    """Freshness rejects new stale replays but not provider redelivery of a
    previously claimed event whose domain dispatch failed."""
    payload = {
        "event": "payment.authorized",
        "id": "evt_failed_stale_redelivery",
        "created_at": int(time.time()) - 301,
        "payload": {"payment": {"entity": {}}},
    }
    raw = json.dumps(payload).encode()
    STORE.put(
        {
            "_type": "webhook_event",
            "id": payload["id"],
            "event_type": payload["event"],
            "processing_status": "failed",
            "processing_started_at": "2000-01-01T00:00:00+00:00",
            "attempts": 1,
            "payload": payload,
        }
    )

    response = client.post(
        "/api/webhooks/razorpay",
        content=raw,
        headers={"X-Razorpay-Signature": sign(raw)},
    )

    assert response.status_code == 200
    assert STORE.get(payload["id"])["processing_status"] == "processed"
    assert STORE.get(payload["id"])["attempts"] == 2


# ------------------------------------------------------------------ duplicates


def test_duplicate_event_x5_single_domain_effect(client):
    contract = make_authorized_contract()
    order_r = client.post(f"/api/contracts/{contract['id']}/payment-order")
    assert order_r.status_code == 200, order_r.text
    order_id = order_r.json()["checkout_config"]["order_id"]
    payment_id = "pay_DupTest0000001"

    raw, sig, event_id = captured_envelope(order_id, payment_id, 1149900)
    for i in range(5):
        r = client.post(
            "/api/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sig}
        )
        assert r.status_code == 200
        assert bool(r.json().get("duplicate")) is (i > 0)

    stored_events = STORE.find("webhook_event", id=event_id)
    assert len(stored_events) == 1, "event body stored once"

    dup_markers = [
        e
        for e in LOG.all()
        if e.get("aggregate_id") == event_id and e.get("event_type") == "WEBHOOK_DUPLICATE_IGNORED"
    ]
    assert len(dup_markers) == 4, "4 replays ignored, each audited"
    received = next(e for e in LOG.all() if e.get("event_type") == "WEBHOOK_RECEIVED")
    assert received["correlation_id"] == contract["id"]
    assert received["payload"]["event_id"] == event_id
    assert {e["payload"]["event_id"] for e in dup_markers} == {event_id}
    assert {e["correlation_id"] for e in dup_markers} == {contract["id"]}

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
    r = client.post(
        "/api/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sig}
    )
    assert r.status_code == 200

    refreshed = STORE.get(contract["id"])
    assert refreshed["status"] == "PAYMENT_PENDING", "amount mismatch must NOT grant PAID"
    mismatch = [
        e
        for e in events_for(contract["id"], "STATE_RECONCILED")
        if e["payload"].get("reason") == "captured_amount_mismatch"
    ]
    assert mismatch, "mismatch recorded for audit"


@pytest.mark.parametrize(
    ("wire_amount", "wire_currency", "reason"),
    [
        ("1149900", "INR", "captured_amount_invalid"),
        (1149900.0, "INR", "captured_amount_invalid"),
        (None, "INR", "captured_amount_invalid"),
        (1149900, "USD", "captured_currency_mismatch"),
    ],
)
def test_captured_requires_strict_amount_and_currency(
    client, wire_amount, wire_currency, reason
):
    """Only a positive integer INR capture may grant the frozen contract."""
    contract = make_authorized_contract()
    order_r = client.post(f"/api/contracts/{contract['id']}/payment-order")
    order_id = order_r.json()["checkout_config"]["order_id"]
    STORE.update(contract["id"], status="PAYMENT_PENDING")

    raw, sig, _ = captured_envelope(
        order_id, "pay_StrictBoundary001", wire_amount
    )
    body = json.loads(raw)
    body["payload"]["payment"]["entity"]["currency"] = wire_currency
    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
    sig = sign(raw)

    response = client.post(
        "/api/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sig}
    )
    assert response.status_code == 200
    assert STORE.get(contract["id"])["status"] == "PAYMENT_PENDING"
    assert any(
        event["payload"].get("reason") == reason
        for event in events_for(contract["id"], "STATE_RECONCILED")
    )


def test_captured_without_payment_id_never_grants_paid(client):
    """A signed-but-malformed capture cannot create a paid contract."""
    contract = make_authorized_contract()
    order_id = client.post(
        f"/api/contracts/{contract['id']}/payment-order"
    ).json()["checkout_config"]["order_id"]
    STORE.update(contract["id"], status="PAYMENT_PENDING")

    raw, sig, _ = captured_envelope(order_id, "", 1149900)
    response = client.post(
        "/api/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sig}
    )

    assert response.status_code == 200
    refreshed = STORE.get(contract["id"])
    assert refreshed["status"] == "PAYMENT_PENDING"
    assert refreshed.get("razorpay_payment_id") is None
    withheld = events_for(contract["id"], "STATE_RECONCILED")
    assert any(
        event["payload"].get("reason") == "captured_payment_id_invalid"
        and event["payload"].get("action") == "paid_withheld"
        for event in withheld
    )


def test_conflicting_capture_payment_id_never_grants_paid(client):
    """A second payment id cannot change the contract's refund target."""
    contract = make_authorized_contract()
    order_id = client.post(
        f"/api/contracts/{contract['id']}/payment-order"
    ).json()["checkout_config"]["order_id"]
    bound_payment = "pay_already_bound01"
    observed_payment = "pay_conflicting01"
    STORE.update(
        contract["id"], status="PAYMENT_PENDING", razorpay_payment_id=bound_payment
    )

    raw, sig, _ = captured_envelope(order_id, observed_payment, 1149900)
    response = client.post(
        "/api/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sig}
    )

    assert response.status_code == 200
    refreshed = STORE.get(contract["id"])
    assert refreshed["status"] == "PAYMENT_PENDING"
    assert refreshed["razorpay_payment_id"] == bound_payment
    assert STORE.get(observed_payment) is None
    assert any(
        event["payload"].get("reason") == "conflicting_payment_capture"
        and event["payload"].get("action") == "paid_withheld"
        for event in events_for(contract["id"], "STATE_RECONCILED")
    )


def test_capture_does_not_repoint_foreign_payment_projection(client):
    """A payment id already attached to another order stays attached there."""
    contract = make_authorized_contract()
    order_id = client.post(
        f"/api/contracts/{contract['id']}/payment-order"
    ).json()["checkout_config"]["order_id"]
    STORE.update(contract["id"], status="PAYMENT_PENDING")
    payment_id = "pay_foreign_projection"
    STORE.put(
        {
            "_type": "razorpay_payment",
            "id": payment_id,
            "amount": 1149900,
            "currency": "INR",
            "status": "captured",
            "order_id": "order_owned_elsewhere",
            "amount_refunded": 0,
        }
    )

    raw, sig, _ = captured_envelope(order_id, payment_id, 1149900)
    response = client.post(
        "/api/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sig}
    )

    assert response.status_code == 200
    assert STORE.get(contract["id"])["status"] == "PAYMENT_PENDING"
    assert STORE.get(contract["id"]).get("razorpay_payment_id") is None
    assert STORE.get(payment_id)["order_id"] == "order_owned_elsewhere"
    assert any(
        event["payload"].get("reason") == "payment_record_order_mismatch"
        and event["payload"].get("action") == "paid_withheld"
        for event in events_for(contract["id"], "STATE_RECONCILED")
    )


def test_captured_after_paid_is_idempotent_no_regression(client):
    contract = make_authorized_contract()
    client.post(f"/api/contracts/{contract['id']}/payment-order")
    STORE.update(contract["id"], status="FULFILLING")

    raw, sig, _ = captured_envelope(
        STORE.get(contract["id"])["razorpay_order_id"], "pay_LateArrival001", 1149900
    )
    r = client.post(
        "/api/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sig}
    )
    assert r.status_code == 200
    assert STORE.get(contract["id"])["status"] == "FULFILLING", "never regress past PAID"


@pytest.mark.parametrize("terminal_status", ["DRAFT", "CANCELLED", "FAILED"])
def test_captured_never_resurrects_non_payable_contracts(client, terminal_status):
    """K-03 regression: a signature-VALID capture on a draft/cancelled/failed
    contract records gateway reality but NEVER changes status."""
    from project_dante.integrations.razorpay import service

    contract = make_authorized_contract()
    order = service.create_order(1149900)
    STORE.update(contract["id"], razorpay_order_id=order["id"], status=terminal_status)

    raw, sig, _ = captured_envelope(order["id"], "pay_OrphanCapture001", 1149900)
    r = client.post("/api/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sig})
    assert r.status_code == 200

    refreshed = STORE.get(contract["id"])
    assert refreshed["status"] == terminal_status, f"{terminal_status} must never teleport to PAID"
    withheld = [
        e
        for e in events_for(contract["id"], "STATE_RECONCILED")
        if e["payload"].get("reason") == "captured_event_for_non_payable_state"
    ]
    assert withheld, "orphaned capture documented honestly for human handling"
    assert withheld[0]["payload"]["action"] == "paid_withheld"
    # Orphaned payment id must not graft onto the contract record.
    assert not refreshed.get("razorpay_payment_id")


@pytest.mark.parametrize(
    "past_paid_status", ["FULFILLING", "SATISFIED", "REMEDIATED", "BREACH_DETECTED"]
)
def test_captured_on_post_paid_states_is_idempotent_no_resurrection(
    client, past_paid_status
):
    """Captures arriving after payment was granted never regress or mutate
    lifecycle state — recorded idempotently, status untouched."""
    from project_dante.integrations.razorpay import service

    contract = make_authorized_contract()
    order = service.create_order(1149900)
    STORE.update(contract["id"], razorpay_order_id=order["id"], status=past_paid_status)

    raw, sig, _ = captured_envelope(order["id"], "pay_LateCapture0001", 1149900)
    r = client.post("/api/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sig})
    assert r.status_code == 200
    assert STORE.get(contract["id"])["status"] == past_paid_status


# ------------------------------------------------------------------- refunds


def test_refund_processed_webhook_appends_event(client):
    contract = make_authorized_contract()
    STORE.update(contract["id"], razorpay_payment_id="pay_Refunded00001")
    before = len(events_for(contract["id"], "REFUND_PROCESSED"))

    payload = {
        "event": "refund.processed",
        "id": f"evt_{uuid.uuid4().hex[:12]}",
        "created_at": int(time.time()),
        "payload": {
            "refund": {
                "entity": {
                    "id": "rf_TestRefund00001",
                    "payment_id": "pay_Refunded00001",
                    "amount": 50000,
                }
            }
        },
    }
    raw = json.dumps(payload).encode()
    r = client.post(
        "/api/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sign(raw)}
    )
    assert r.status_code == 200
    assert len(events_for(contract["id"], "REFUND_PROCESSED")) == before + 1


def test_refund_before_capture_binds_by_order_and_remediates(client):
    """A refund can race its capture webhook without becoming an orphan.

    The signed refund carries the order id already issued for the frozen
    contract. It may therefore reconcile the contract even though no payment
    capture record or contract payment binding exists yet.
    """
    contract = make_authorized_contract()
    order_r = client.post(f"/api/contracts/{contract['id']}/payment-order")
    assert order_r.status_code == 200
    order_id = order_r.json()["checkout_config"]["order_id"]
    STORE.update(contract["id"], status="BREACH_DETECTED")

    payment_id = "pay_RefundBeforeCapture1"
    refund_id = "rf_RefundBeforeCapture1"
    event_id = "evt_RefundBeforeCapture1"
    payload = {
        "event": "refund.processed",
        "id": event_id,
        "created_at": int(time.time()),
        "payload": {
            "refund": {
                "entity": {
                    "id": refund_id,
                    "payment_id": payment_id,
                    "order_id": order_id,
                    "amount": contract["amount_paise"],
                    "currency": "INR",
                    "status": "processed",
                }
            }
        },
    }
    raw = json.dumps(payload).encode()
    response = client.post(
        "/api/webhooks/razorpay",
        content=raw,
        headers={"X-Razorpay-Signature": sign(raw)},
    )

    assert response.status_code == 200
    refreshed = STORE.get(contract["id"])
    assert refreshed["status"] == "REMEDIATED"
    assert refreshed.get("razorpay_payment_id") is None
    assert refreshed["refunded_amount_paise"] == contract["amount_paise"]
    assert refreshed["refund_status"] == "fully_refunded"
    assert refreshed["refund_reconciled"] is True
    assert len(events_for(contract["id"], "REFUND_PROCESSED")) == 1
    assert len(events_for(contract["id"], "CONTRACT_REMEDIATED")) == 1
    received = next(
        event for event in LOG.all()
        if event.get("event_type") == "WEBHOOK_RECEIVED"
    )
    assert received["correlation_id"] == contract["id"]
    assert received["payload"]["event_id"] == event_id
    payment = STORE.get(payment_id)
    assert payment is not None
    assert payment["order_id"] == order_id
    assert payment["status"] == "unknown"


def test_refund_order_payment_mismatch_is_withheld(client):
    """A matching order cannot override an already-bound payment identity."""
    contract = make_authorized_contract()
    order_r = client.post(f"/api/contracts/{contract['id']}/payment-order")
    assert order_r.status_code == 200
    order_id = order_r.json()["checkout_config"]["order_id"]
    bound_payment_id = "pay_RefundBoundPayment1"
    foreign_payment_id = "pay_RefundForeignPay1"
    STORE.update(
        contract["id"],
        status="BREACH_DETECTED",
        razorpay_payment_id=bound_payment_id,
    )

    payload = {
        "event": "refund.processed",
        "id": "evt_RefundBindingConflict1",
        "created_at": int(time.time()),
        "payload": {
            "refund": {
                "entity": {
                    "id": "rf_RefundBindingConflict1",
                    "payment_id": foreign_payment_id,
                    "order_id": order_id,
                    "amount": contract["amount_paise"],
                    "currency": "INR",
                    "status": "processed",
                }
            }
        },
    }
    raw = json.dumps(payload).encode()
    response = client.post(
        "/api/webhooks/razorpay",
        content=raw,
        headers={"X-Razorpay-Signature": sign(raw)},
    )

    assert response.status_code == 200
    refreshed = STORE.get(contract["id"])
    assert refreshed["status"] == "BREACH_DETECTED"
    assert refreshed.get("refund_reconciled") is not True
    assert refreshed.get("refunded_amount_paise") is None
    assert STORE.get(foreign_payment_id) is None
    assert STORE.get("rf_RefundBindingConflict1") is None
    conflicts = [
        event
        for event in LOG.all()
        if event.get("event_type") == "STATE_RECONCILED"
        and event.get("payload", {}).get("reason") == "refund_binding_conflict"
    ]
    assert len(conflicts) == 1
    assert conflicts[0]["payload"]["conflict"] == "contract_payment_mismatch"


def test_capture_snapshot_cannot_erase_refund_seen_first(client):
    """A captured-payment snapshot with amount_refunded=0 is stale when a
    refund webhook already projected the refund; keep the ledger total."""
    contract = make_authorized_contract()
    order_r = client.post(f"/api/contracts/{contract['id']}/payment-order")
    assert order_r.status_code == 200
    order_id = order_r.json()["checkout_config"]["order_id"]
    STORE.update(contract["id"], status="BREACH_DETECTED")

    payment_id = "pay_RefundFirstThenCapture1"
    refund_id = "rf_RefundFirstThenCapture1"
    refund_payload = {
        "event": "refund.processed",
        "id": "evt_RefundFirstThenCapture1",
        "created_at": int(time.time()),
        "payload": {
            "refund": {
                "entity": {
                    "id": refund_id,
                    "payment_id": payment_id,
                    "order_id": order_id,
                    "amount": 250000,
                    "currency": "INR",
                    "status": "processed",
                }
            }
        },
    }
    refund_raw = json.dumps(refund_payload).encode()
    first = client.post(
        "/api/webhooks/razorpay",
        content=refund_raw,
        headers={"X-Razorpay-Signature": sign(refund_raw)},
    )
    assert first.status_code == 200
    assert STORE.get(payment_id)["amount_refunded"] == 250000

    capture_raw, capture_sig, _ = captured_envelope(
        order_id,
        payment_id,
        contract["amount_paise"],
        amount_refunded=0,
    )
    second = client.post(
        "/api/webhooks/razorpay",
        content=capture_raw,
        headers={"X-Razorpay-Signature": capture_sig},
    )
    assert second.status_code == 200
    payment = STORE.get(payment_id)
    assert payment is not None
    assert payment["amount_refunded"] == 250000
    assert payment["processed_refund_ids"] == [refund_id]
    assert payment["refund_status"] == "processed"


def test_failed_webhook_dispatch_is_redeliverable(client, monkeypatch):
    """A handler failure leaves a failed claim that the next delivery retries."""
    import project_dante.api.routes.webhooks as webhook_routes

    calls = 0

    def flaky_handler(event_id, payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient handler failure")

    monkeypatch.setattr(webhook_routes, "_on_payment_captured", flaky_handler)
    raw, sig, event_id = captured_envelope(
        "order_retry_0001", "pay_retry_000001", 1149900, "evt_retry_dispatch"
    )

    first = client.post(
        "/api/webhooks/razorpay",
        content=raw,
        headers={"X-Razorpay-Signature": sig},
    )
    assert first.status_code == 500
    failed = STORE.get(event_id)
    assert failed is not None
    assert failed["processing_status"] == "failed"
    assert failed["attempts"] == 1

    second = client.post(
        "/api/webhooks/razorpay",
        content=raw,
        headers={"X-Razorpay-Signature": sig},
    )
    assert second.status_code == 200
    processed = STORE.get(event_id)
    assert processed is not None
    assert processed["processing_status"] == "processed"
    assert processed["attempts"] == 2
    assert calls == 2


# ------------------------------------------------------------ verify-client


def test_verify_client_happy_then_webhook_grants_paid(client):
    contract = make_authorized_contract()
    order_r = client.post(f"/api/contracts/{contract['id']}/payment-order")
    order_id = order_r.json()["checkout_config"]["order_id"]
    payment_id = "pay_ClientVerify001"
    checkout_secret = SANDBOX_KEY_SECRET
    sig = sign(f"{order_id}|{payment_id}".encode(), secret=checkout_secret)

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


def test_verify_client_rejects_contract_without_server_order(client):
    """A valid sandbox HMAC cannot advance a contract with no bound order."""
    contract = make_authorized_contract()
    payment_id = "pay_NoServerOrder001"
    signature = sign(
        f"order_Arbitrary0001|{payment_id}".encode(), secret=SANDBOX_KEY_SECRET
    )
    response = client.post(
        "/api/payments/verify-client",
        json={
            "contract_id": contract["id"],
            "razorpay_order_id": "order_Arbitrary0001",
            "razorpay_payment_id": payment_id,
            "signature": signature,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "payment_order_not_created"
    refreshed = STORE.get(contract["id"])
    assert refreshed["status"] == "AWAITING_BUYER_AUTH"
    assert refreshed.get("razorpay_payment_id") is None


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


def test_payment_order_readback_and_pending_reentry_keep_same_order(client):
    contract = make_authorized_contract()
    path = f"/api/contracts/{contract['id']}/payment-order"
    created = client.post(path)
    assert created.status_code == 200
    first = created.json()
    order_id = first["checkout_config"]["order_id"]

    readback = client.get(path)
    assert readback.status_code == 200
    recovered = readback.json()
    assert recovered["checkout_config"] == first["checkout_config"]
    assert recovered["contract_status"] == "PAYMENT_ORDER_CREATED"

    STORE.update(contract["id"], status="PAYMENT_PENDING")
    reentered = client.post(path)
    assert reentered.status_code == 200
    assert reentered.json()["checkout_config"]["order_id"] == order_id
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
    # Key-shaped but clearly fake (test-mode prefix + placeholder secret):
    # real-key detection now requires the rzp_test_ prefix, while staying
    # grep-clean for secrets scans.
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_DUMMYKEYGUARD00")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "dummy-secret-value-for-guard-test")
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
    order_r = client.post(f"/api/contracts/{contract['id']}/payment-order")
    order_id = order_r.json()["checkout_config"]["order_id"]
    pid = "pay_SimDupTarget01"

    for _ in range(2):
        r = client.post(
            "/api/demo/razorpay/simulate-event",
            json={"event_type": "payment.captured", "order_id": order_id, "payment_id": pid},
        )
        assert r.status_code == 200

    assert len(events_for(contract["id"], "RAZORPAY_PAYMENT_CAPTURED")) == 1
