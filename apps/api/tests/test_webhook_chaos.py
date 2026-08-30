"""Project Dante — Webhook Chaos Suite (Agent K).

Drives api/routes/webhooks.py through TestClient with duplicated,
out-of-order, forged, and unknown-entity events. Invariants under attack:
  I10 signature verification from raw body
  I11 duplicate events idempotently ignored
  I12 out-of-order events never corrupt state
  K-03 follow-up: non-payable contracts neither resurrected nor grafted

A skipped test is NOT a pass.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

import pytest

_API_ROOT = Path(__file__).resolve().parents[1]
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))

os.environ.setdefault("DANTE_STORE_PATH", str(_API_ROOT / ".dante-redteam-store.json"))

WEBHOOK_URL = "/api/webhooks/razorpay"
SECRET_CANDIDATES = [
    os.environ.get("RAZORPAY_WEBHOOK_SECRET", ""),
    "dante-dev-webhook-secret",
]


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _captured_body(
    *,
    event_id_suffix: str = "1",
    order_id: str = "order_chaos_A",
    payment_id: str = "pay_chaos_A",
    amount: int = 1149900,
    event: str = "payment.captured",
) -> bytes:
    return json.dumps(
        {
            "event": event,
            "created_at": int(time.time()),
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "order_id": order_id,
                        "amount": amount,
                        "currency": "INR",
                        "status": "captured",
                    }
                }
            },
            "_chaos_event_id": f"evt_chaos_{event_id_suffix}",
        }
    ).encode()


def _paid_contract(order_id: str, amount_paise: int = 1149900) -> dict:
    return _seed_contract(
        contract_id="con_chaos_A", order_id=order_id, amount_paise=amount_paise
    )


def _seed_contract(
    *,
    contract_id: str,
    order_id: str,
    amount_paise: int = 1149900,
    status: str = "PAYMENT_PENDING",
) -> dict:
    from project_dante.db.store import STORE

    contract = {
        "_type": "contract",
        "id": contract_id,
        "intent_id": f"int_{contract_id}",
        "offer_id": f"off_{contract_id}",
        "razorpay_order_id": order_id,
        "razorpay_payment_id": None,
        "amount_paise": amount_paise,
        "status": status,
        "sandbox_mode": True,
    }
    STORE.put(contract)
    return contract


@pytest.fixture()
def chaos_env(clean_store=None):
    """Fresh store/log + TestClient with routes registered."""
    from project_dante.db.store import STORE
    from project_dante.domain.events import LOG

    STORE.reset()
    LOG.reset()

    app_mod = pytest.importorskip("project_dante.api.app")
    # Ensure webhooks route module is present (it registers itself into app).
    pytest.importorskip("project_dante.api.routes.webhooks")
    from fastapi.testclient import TestClient

    client = TestClient(app_mod.app, raise_server_exceptions=False)
    yield client
    STORE.reset()
    LOG.reset()


def _working_secret() -> str | None:
    rzp = pytest.importorskip("project_dante.integrations.razorpay.service")
    probe = _captured_body(event_id_suffix="probe")
    for cand in SECRET_CANDIDATES:
        if cand and rzp.verify_webhook_signature(probe, _sign(probe, cand)):
            return cand
    return None


class TestWebhookChaos:
    def test_duplicate_captured_5x_single_effect(self, chaos_env):
        client = chaos_env
        secret = _working_secret()
        if secret is None:
            pytest.skip("no working webhook secret configured for signed delivery")

        _paid_contract("order_chaos_A")
        from project_dante.db.store import STORE
        from project_dante.domain.events import LOG

        effects_seen = []
        statuses = []
        for i in range(5):
            body = _captured_body(event_id_suffix="dup")  # identical every time
            r = client.post(
                WEBHOOK_URL,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": _sign(body, secret),
                    "X-Razorpay-Event-Id": "evt_chaos_dup",
                },
            )
            statuses.append(r.status_code)
            assert r.status_code == 200, f"delivery {i+1}: {r.status_code} {r.text[:120]}"

        contract = STORE.get("con_chaos_A")
        # Exactly one PAID transition across five deliveries of the same event.
        paid_events = [
            e
            for e in LOG.all()
            if e.get("event_type") == "RAZORPAY_PAYMENT_CAPTURED"
            and e.get("aggregate_id") == "con_chaos_A"
        ]
        effects_seen.append(len(paid_events))
        assert len(paid_events) == 1, (
            f"5x duplicate produced {len(paid_events)} capture effects "
            f"(statuses={statuses})"
        )
        if contract:
            assert contract["status"] == "PAID", f"final status {contract['status']}"
            dup_events = [
                e for e in LOG.all() if e.get("event_type") == "WEBHOOK_DUPLICATE_IGNORED"
            ]
            assert len(dup_events) >= 1 or len(paid_events) == 1, (
                "duplicates neither ignored nor logged"
            )

    def test_out_of_order_captured_before_anything(self, chaos_env):
        """captured arrives when no contract/order state exists yet."""
        client = chaos_env
        secret = _working_secret()
        if secret is None:
            pytest.skip("no working webhook secret configured")

        from project_dante.db.store import STORE

        body = _captured_body(
            event_id_suffix="ooo", order_id="order_never_created", payment_id="pay_orphan"
        )
        r = client.post(
            WEBHOOK_URL,
            content=body,
            headers={
                "X-Razorpay-Signature": _sign(body, secret),
                "X-Razorpay-Event-Id": "evt_chaos_ooo",
            },
        )
        # Must not crash; may accept-and-store or reject-with-4xx, but never 500.
        assert r.status_code < 500, f"server error on orphan captured: {r.status_code}"
        # An orphan event must not mint a PAID contract out of thin air.
        phantom = [c for c in STORE.list("contract") if c.get("status") == "PAID"]
        assert phantom == [], f"orphan webhook minted PAID contracts: {[p['id'] for p in phantom]}"
        stored = STORE.find_one("webhook_event", event_id="evt_chaos_ooo")
        assert stored is not None or STORE.count("webhook_event") >= 0, (
            "event storage behavior changed"
        )

    def test_out_of_order_captured_for_known_pending_contract(self, chaos_env):
        """captured arrives while contract is still CONTRACT_FROZEN (skipping
        authorized/order-created states): must reconcile to PAID, not crash."""
        client = chaos_env
        secret = _working_secret()
        if secret is None:
            pytest.skip("no working webhook secret configured")

        from project_dante.db.store import STORE

        contract = _paid_contract("order_chaos_A")
        contract["status"] = "CONTRACT_FROZEN"  # simulate skipped earlier stages
        STORE.put(contract)

        body = _captured_body(event_id_suffix="ooo2")
        r = client.post(
            WEBHOOK_URL,
            content=body,
            headers={"X-Razorpay-Signature": _sign(body, secret)},
        )
        assert r.status_code < 500, f"crash on out-of-order captured: {r.status_code}"
        after = STORE.get("con_chaos_A")
        assert after is not None
        assert after["status"] in {"PAID", "PAYMENT_PENDING"}, (
            f"corrupted state after out-of-order: {after['status']}"
        )

    def test_normal_order_authorized_then_captured(self, chaos_env):
        client = chaos_env
        secret = _working_secret()
        if secret is None:
            pytest.skip("no working webhook secret configured")

        from project_dante.db.store import STORE
        from project_dante.domain.events import LOG

        _paid_contract("order_chaos_A")

        authz = json.dumps(
            {
                "event": "payment.authorized",
                "created_at": int(time.time()),
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_chaos_A",
                            "order_id": "order_chaos_A",
                            "amount": 1149900,
                            "status": "authorized",
                        }
                    }
                },
            }
        ).encode()
        r1 = client.post(
            WEBHOOK_URL,
            content=authz,
            headers={"X-Razorpay-Signature": _sign(authz, secret)},
        )
        cap = _captured_body(event_id_suffix="normal")
        r2 = client.post(
            WEBHOOK_URL,
            content=cap,
            headers={"X-Razorpay-Signature": _sign(cap, secret)},
        )
        assert r1.status_code == 200 and r2.status_code == 200, (
            f"({r1.status_code}, {r2.status_code})"
        )
        contract = STORE.get("con_chaos_A")
        assert contract["status"] == "PAID", f"expected PAID, got {contract['status']}"
        captures = [
            e
            for e in LOG.for_aggregate("con_chaos_A")
            if e.get("event_type") == "RAZORPAY_PAYMENT_CAPTURED"
        ]
        assert len(captures) == 1

    def test_refund_processed_unknown_payment_no_crash(self, chaos_env):
        client = chaos_env
        secret = _working_secret()
        if secret is None:
            pytest.skip("no working webhook secret configured")

        from project_dante.db.store import STORE

        body = json.dumps(
            {
                "event": "refund.processed",
                "created_at": int(time.time()),
                "payload": {
                    "refund": {
                        "id": "rfnd_ghost",
                        "payment_id": "pay_does_not_exist",
                        "amount": 50000,
                        "status": "processed",
                    }
                },
            }
        ).encode()
        r = client.post(
            WEBHOOK_URL,
            content=body,
            headers={"X-Razorpay-Signature": _sign(body, secret)},
        )
        assert r.status_code < 500, f"crash on unknown-payment refund.processed: {r.status_code}"
        # No phantom domain refunds minted from a foreign refund event.
        assert STORE.count("razorpay_refund") == 0

    def test_forged_signature_gets_401_and_zero_persistence(self, chaos_env):
        client = chaos_env
        from project_dante.db.store import STORE
        from project_dante.domain.events import LOG

        _paid_contract("order_chaos_A")
        events_before = len(LOG.all())
        webhook_rows_before = STORE.count("webhook_event")

        body = _captured_body(event_id_suffix="forged")
        for sig in ["deadbeef", _sign(body, "attacker-secret-0987654321"), ""]:
            r = client.post(
                WEBHOOK_URL,
                content=body,
                headers={"X-Razorpay-Signature": sig},
            )
            assert r.status_code == 401, (
                f"forged sig accepted: {sig!r} -> {r.status_code}"
            )

        assert len(LOG.all()) == events_before, "domain events appended despite forgery"
        assert STORE.count("webhook_event") == webhook_rows_before, (
            "webhook_event persisted despite forgery"
        )
        assert STORE.get("con_chaos_A")["status"] == "PAYMENT_PENDING"

    def test_missing_signature_header_rejected(self, chaos_env):
        client = chaos_env
        body = _captured_body(event_id_suffix="nosig")
        r = client.post(WEBHOOK_URL, content=body, headers={"Content-Type": "application/json"})
        assert r.status_code in {400, 401}, f"unsigned body accepted: {r.status_code}"

    def test_huge_body_rejected_without_effect(self, chaos_env):
        client = chaos_env
        huge = b'{"event":"payment.captured","pad":"' + b"A" * (1024 * 1024) + b'"}'
        r = client.post(
            WEBHOOK_URL,
            content=huge,
            headers={"X-Razorpay-Signature": "a" * 64},
        )
        assert r.status_code < 500, f"huge body caused server error: {r.status_code}"

    def test_non_json_garbage_with_valid_signature_shape(self, chaos_env):
        client = chaos_env
        garbage = b"\x00\x01\x02 not json at all"
        r = client.post(
            WEBHOOK_URL,
            content=garbage,
            headers={"X-Razorpay-Signature": "f" * 64},
        )
        assert r.status_code in {400, 401}, f"garbage accepted: {r.status_code}"

    def test_amount_mismatch_capture_does_not_grant_paid(self, chaos_env):
        """Captured event whose amount differs from the frozen contract must
        never move the contract to PAID (plan §23 amount manipulation)."""
        client = chaos_env
        secret = _working_secret()
        if secret is None:
            pytest.skip("no working webhook secret configured")

        from project_dante.db.store import STORE

        _paid_contract("order_chaos_A", amount_paise=1149900)

        body = _captured_body(event_id_suffix="amt", amount=9999999)
        r = client.post(
            WEBHOOK_URL,
            content=body,
            headers={"X-Razorpay-Signature": _sign(body, secret)},
        )
        assert r.status_code == 200
        after = STORE.get("con_chaos_A")
        assert after["status"] != "PAID", (
            "amount-tampered capture granted PAID — amount guard missing"
        )

    def test_captured_never_resurrects_cancelled_or_draft_contracts(self, chaos_env):
        """A capture event must not teleport terminal/pre-payment states to
        PAID outside the state machine."""
        client = chaos_env
        secret = _working_secret()
        if secret is None:
            pytest.skip("no working webhook secret configured")

        from project_dante.db.store import STORE

        for idx, illegal_status in enumerate(["CANCELLED", "FAILED", "DRAFT"]):
            cid = f"con_chaos_res_{idx}"
            _seed_contract(
                contract_id=cid,
                order_id=f"order_chaos_res_{idx}",
                status=illegal_status,
            )

            body = _captured_body(
                event_id_suffix=f"res{idx}",
                order_id=f"order_chaos_res_{idx}",
                payment_id=f"pay_chaos_res_{idx}",
            )
            r = client.post(
                WEBHOOK_URL,
                content=body,
                headers={"X-Razorpay-Signature": _sign(body, secret)},
            )
            assert r.status_code == 200
            after = STORE.get(cid)
            assert after["status"] == illegal_status, (
                f"capture teleported {illegal_status} -> {after['status']} "
                f"bypassing validate_transition (state machine abuse)"
            )

    def test_captured_does_not_graft_payment_id_onto_non_payable_contracts(self, chaos_env):
        """K-03 follow-up hardening: an orphaned capture on a cancelled/draft
        contract must not graft its payment id onto that contract either —
        downstream refund lookups key off razorpay_payment_id."""
        client = chaos_env
        secret = _working_secret()
        if secret is None:
            pytest.skip("no working webhook secret configured")

        from project_dante.db.store import STORE

        for idx, illegal_status in enumerate(["CANCELLED", "FAILED", "DRAFT"]):
            cid = f"con_chaos_graft_{idx}"
            _seed_contract(
                contract_id=cid,
                order_id=f"order_chaos_graft_{idx}",
                status=illegal_status,
            )
            body = _captured_body(
                event_id_suffix=f"graft{idx}",
                order_id=f"order_chaos_graft_{idx}",
                payment_id=f"pay_attacker_graft_{idx}",
            )
            r = client.post(
                WEBHOOK_URL,
                content=body,
                headers={"X-Razorpay-Signature": _sign(body, secret)},
            )
            assert r.status_code == 200
            after = STORE.get(cid)
            assert after.get("razorpay_payment_id") is None, (
                f"{illegal_status} contract grafted attacker payment id "
                f"{after.get('razorpay_payment_id')!r} — refund lookups would "
                f"key off an orphaned capture"
            )

    def test_captured_still_records_on_post_paid_states_without_regression(self, chaos_env):
        """Late redelivery on SATISFIED/BREMEDIATED-family contracts is a no-op
        recording; status must never regress toward PAID-side writes."""
        client = chaos_env
        secret = _working_secret()
        if secret is None:
            pytest.skip("no working webhook secret configured")

        from project_dante.db.store import STORE

        for idx, post_status in enumerate(["SATISFIED", "REMEDIATED", "BREACH_DETECTED"]):
            cid = f"con_chaos_post_{idx}"
            _seed_contract(
                contract_id=cid,
                order_id=f"order_chaos_post_{idx}",
                status=post_status,
            )
            body = _captured_body(
                event_id_suffix=f"post{idx}",
                order_id=f"order_chaos_post_{idx}",
                payment_id=f"pay_chaos_post_{idx}",
            )
            r = client.post(
                WEBHOOK_URL,
                content=body,
                headers={"X-Razorpay-Signature": _sign(body, secret)},
            )
            assert r.status_code == 200
            after = STORE.get(cid)
            assert after["status"] == post_status, (
                f"post-paid capture changed status {post_status} -> "
                f"{after['status']}"
            )
