"""Merchant machine-readable surface tests (plan §10).

Covers: profile shape + HONEST capability computation, catalog stats measured
from the committed fixture, offer-freeze route delegation, order-status
projection from stored facts/events (404 on unknown ids), and capability
honesty (structured_warranty true exactly because coverage > 0).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from project_dante.api.routes.merchant_profile import router as merchant_profile_router
from project_dante.db.store import STORE
from project_dante.domain.events import LOG
from project_dante.integrations.merchant import profile
from project_dante.integrations.merchant.catalog_loader import load_catalog

HERO_SKU = "AST-HP-ANC-001"
HERO_OFFER_ID = f"off_{HERO_SKU}"


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(merchant_profile_router, prefix="/api")
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean():
    STORE.reset()
    LOG.reset()
    yield
    STORE.reset()
    LOG.reset()


def _seed_paid_contract(contract_id: str = "con_surf01") -> None:
    STORE.put(
        {
            "_type": "contract",
            "id": contract_id,
            "status": "PAID",
            "offer_sku": HERO_SKU,
            "amount_paise": 1149900,
        }
    )


# ------------------------------------------------------------------ profile shape


def test_profile_shape_and_identity(client):
    resp = client.get("/api/merchant/profile")
    assert resp.status_code == 200
    body = resp.json()
    assert body["merchant_id"] == "aster-electronics"
    assert body["name"] == "Aster Electronics"
    assert set(body["capabilities"]) == {
        "catalog_search",
        "structured_warranty",
        "delivery_promises",
        "returns",
        "razorpay_checkout",
        "post_purchase_resolution",
    }
    assert isinstance(body["catalog_stats"], dict)
    assert body["currency"] == "INR"
    assert body["machine_endpoints"]["offer_freeze"] == "/api/merchant/offers/freeze"


def test_capability_flags_all_true_in_committed_state():
    """Every capability is genuinely available in the committed repo state."""
    caps = profile.build_merchant_profile()["capabilities"]
    assert all(caps.values()), f"capability honestly unavailable: {caps}"


def test_structured_warranty_honesty_tracks_coverage():
    """The flag is COMPUTED: true because measured coverage > 0, not asserted."""
    stats = profile.catalog_stats()
    catalog = load_catalog()

    expected_warranty = sum(
        1 for p in catalog if (p.get("terms") or {}).get("warranty_type") not in (None, "unknown")
    ) / len(catalog)
    assert stats["warranty_metadata_coverage"] == pytest.approx(expected_warranty, abs=1e-4)
    assert stats["warranty_metadata_coverage"] > 0

    profile_caps = profile.build_merchant_profile()["capabilities"]
    assert profile_caps["structured_warranty"] == (stats["warranty_metadata_coverage"] > 0)


def test_catalog_stats_measured_from_fixture():
    catalog = load_catalog()
    total = len(catalog)
    stats = profile.catalog_stats()
    assert stats["total_skus"] == total and total >= 100
    assert 0.0 <= stats["delivery_promise_coverage"] <= 1.0
    assert 0.0 <= stats["return_policy_coverage"] <= 1.0
    # The hero SKU carries a return window, so the share cannot be zero.
    assert stats["return_policy_coverage"] > 0


def test_razorpay_checkout_flag_reflects_client_construction():
    flag = profile.build_merchant_profile()["capabilities"]["razorpay_checkout"]
    # Sandbox adapter always constructs; live-test-mode needs real keys.
    assert flag is True


def test_gateway_mode_reported():
    body = profile.build_merchant_profile()
    assert body["gateway"]["mode"] in {"sandbox", "live-test-mode"}


def test_post_purchase_resolution_requires_rights_engine():
    flag = profile.build_merchant_profile()["capabilities"]["post_purchase_resolution"]
    try:
        from project_dante.domain.rights import engine  # noqa: F401

        assert flag is True
    except ImportError:
        assert flag is False


# ------------------------------------------------------------------ freeze endpoint


def test_freeze_endpoint_delegates_to_service(client):
    resp = client.post("/api/merchant/offers/freeze", json={"offer_id": HERO_OFFER_ID})
    assert resp.status_code == 200
    body = resp.json()
    assert body["offer"]["sku"] == HERO_SKU
    assert body["offer"]["snapshot_hash"]
    assert body["evidence_payload"]["source_type"] == "checkout_offer"
    assert HERO_SKU in body["rendered_text"]
    # freeze events land on the audit log
    assert any(e["event_type"] == "EVIDENCE_SNAPSHOT_CREATED" for e in LOG.all())


def test_freeze_endpoint_accepts_bare_sku(client):
    resp = client.post("/api/merchant/offers/freeze", json={"offer_id": HERO_SKU})
    assert resp.status_code == 200
    assert resp.json()["offer"]["sku"] == HERO_SKU


def test_freeze_endpoint_unknown_offer_404(client):
    resp = client.post("/api/merchant/offers/freeze", json={"offer_id": "off_NOPE"})
    assert resp.status_code == 404


def test_freeze_endpoint_missing_body_field_422(client):
    assert client.post("/api/merchant/offers/freeze", json={}).status_code == 422
    assert client.post("/api/merchant/offers/freeze", json={"offer_id": ""}).status_code == 422


# ------------------------------------------------------------------ order status


def test_order_status_unknown_contract_404(client):
    resp = client.get("/api/merchant/orders/con_missing/status")
    assert resp.status_code == 404


def test_order_status_rejects_non_contract_records(client):
    STORE.put({"_type": "intent", "id": "int_not_a_contract"})
    resp = client.get("/api/merchant/orders/int_not_a_contract/status")
    assert resp.status_code == 404


def test_order_status_unpaid_contract_is_awaiting_payment(client):
    STORE.put({"_type": "contract", "id": "con_bare", "status": "CONTRACT_FROZEN"})
    resp = client.get("/api/merchant/orders/con_bare/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "awaiting_payment"
    assert body["fulfillment"]["shipped"] is False
    assert body["fulfillment"]["delivered"] is False


def test_order_status_paid_lifecycle_without_facts(client):
    _seed_paid_contract()
    resp = client.get("/api/merchant/orders/con_surf01/status").json()
    assert resp["status"] == "paid"
    assert resp["amount_paise"] == 1149900
    assert resp["fulfillment"]["shipped"] is False


def test_order_status_surfaces_reconciled_full_refund(client):
    _seed_paid_contract("con_refunded")
    STORE.update(
        "con_refunded",
        refund_status="fully_refunded",
        refunded_amount_paise=1149900,
        refund_reconciled=True,
        refund_reconciled_at="2026-08-29T10:00:00+00:00",
    )

    body = client.get("/api/merchant/orders/con_refunded/status").json()
    assert body["status"] == "refunded"
    assert body["refund_status"] == "fully_refunded"
    assert body["refunded_amount_paise"] == 1149900
    assert body["refund_reconciled"] is True


def test_order_status_reflects_ship_fact_then_delivery(client):
    _seed_paid_contract()

    from project_dante.integrations.merchant import service

    shipped = service.apply_fulfillment_event("con_surf01", "ship")
    assert any(f["key"] == "shipment.status" for f in shipped["facts"])

    body = client.get("/api/merchant/orders/con_surf01/status").json()
    assert body["status"] == "shipped"
    assert body["fulfillment"]["shipped"] is True
    assert body["fulfillment"]["carrier"] == "SynthEx"
    assert body["fulfillment"]["tracking_id"]
    assert body["synthetic_observations"] == body["fact_count"] == 2

    delivered = service.apply_fulfillment_event("con_surf01", "deliver", scenario="correct")
    assert len(delivered["facts"]) >= 6

    body = client.get("/api/merchant/orders/con_surf01/status").json()
    assert body["status"] == "delivered"
    assert body["fulfillment"]["delivered"] is True
    assert body["fulfillment"]["delivered_date"]
    observed_keys = set(body["observed"])
    assert "warranty.type" in observed_keys and "price.amount_paise" in observed_keys
    assert body["observed"]["shipment.status"] == "shipped"
    assert body["fact_count"] >= 8
    assert body["last_observed_at"]


def test_order_status_late_scenario_surfaces_days_late(client):
    from datetime import date, timedelta

    contract_id = "con_latelate"
    STORE.put(
        {
            "_type": "contract",
            "id": contract_id,
            "status": "PAID",
            "offer_sku": HERO_SKU,
            "amount_paise": 1149900,
        }
    )
    promised_date = str(date.today() - timedelta(days=1))
    STORE.put(
        {
            "_type": "promise",
            "id": "pr_promised_by_late",
            "contract_id": contract_id,
            "key": "delivery.promised_by_date",
            "value": promised_date,
        }
    )

    from project_dante.integrations.merchant import service

    service.apply_fulfillment_event(contract_id, "deliver", scenario="late")

    body = client.get(f"/api/merchant/orders/{contract_id}/status").json()
    assert body["status"] == "delivered"
    assert body["fulfillment"]["days_late"] == 3
    assert body["promised"]["delivery.promised_by_date"] == promised_date


def test_order_status_latest_fact_wins_per_key(client):
    _seed_paid_contract("con_dupes")
    from project_dante.domain.events import new_id, now_iso

    def _put_fact(observed_at: str, value: str) -> None:
        STORE.put(
            {
                "id": new_id("obs"),
                "_type": "fact",
                "contract_id": "con_dupes",
                "key": "shipment.status",
                "value": value,
                "source_artifact_id": "ev_x",
                "observed_at": observed_at,
                "synthetic": True,
                "scenario_id": "scenario_manual",
            }
        )

    _put_fact(now_iso(), "in_transit")
    later = now_iso()
    _put_fact(later, "shipped")
    _put_fact("2020-01-01T00:00:00+00:00", "preparing")

    body = client.get("/api/merchant/orders/con_dupes/status").json()
    assert body["status"] == "shipped"
    assert body["observed"]["shipment.status"] == "shipped"
