"""Demo simulation tests (Agent F): fulfillment scenarios, synthetic facts,
demo route guards, reset flow.

Fulfillment facts land in STORE as `_type: fact` records and mirror into the
append-only LOG. Every record carries synthetic=true + scenario_id.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from project_dante.api.routes.demo import router as demo_router
from project_dante.db.store import STORE
from project_dante.domain.events import LOG, append_event
from project_dante.integrations.merchant import service


@pytest.fixture()
def isolated_env(tmp_path, monkeypatch):
    """Fresh STORE + LOG per test so runs never leak into each other."""
    monkeypatch.setenv("DANTE_STORE_PATH", str(tmp_path / "store.json"))
    fresh_store = STORE.__class__(str(tmp_path / "store.json"))
    monkeypatch.setattr(service, "STORE", fresh_store)

    import project_dante.api.routes.demo as demo_mod

    monkeypatch.setattr(demo_mod, "STORE", fresh_store)
    # Reset the shared in-memory log between tests.
    LOG.reset()
    yield {"store": fresh_store}


def _seed_contract(store, contract_id="con_test01"):
    """Minimal contract + frozen promises for fulfillment scenarios."""
    store.put({
        "_type": "contract",
        "id": contract_id,
        "status": "PAID",
        "offer_sku": "AST-HP-ANC-001",
        "amount_paise": 1149900,
    })
    ids = {}
    for key, value in [
        ("warranty.type", "manufacturer"),
        ("warranty.region", "IN"),
        ("product.region", "IN"),
        ("condition", "new"),
        ("price.amount_paise", 1149900),
        ("warranty.duration_months", 12),
        ("delivery.promised_by_date", None),
    ]:
        pid = f"pr_{key.replace('.', '_')}_{contract_id[-4:]}"
        ids[key] = pid
        store.put({
            "_type": "promise",
            "id": pid,
            "contract_id": contract_id,
            "key": key,
            "value": value,
        })
    return ids


# ------------------------------------------------------------------ ship


def test_ship_creates_synthetic_facts(isolated_env):
    store = isolated_env["store"]
    _seed_contract(store)
    result = service.apply_fulfillment_event("con_test01", "ship")
    facts = result["facts"]
    keys = {f["key"]: f for f in facts}
    assert keys["shipment.status"]["value"] == "shipped"
    assert keys["shipment.carrier"]["value"] == "SynthEx"
    for fact in facts:
        assert fact["synthetic"] is True
        assert fact["scenario_id"]
        stored = store.get(fact["id"])
        assert stored is not None and stored["_type"] == "fact"
    shipped_events = [e for e in LOG.all() if e["event_type"] == "FULFILLMENT_SHIPPED"]
    assert shipped_events and shipped_events[0]["synthetic"] is True


# ------------------------------------------------------------------ deliver: wrong_variant


def test_deliver_wrong_variant_facts(isolated_env):
    store = isolated_env["store"]
    _seed_contract(store)
    result = service.apply_fulfillment_event("con_test01", "deliver", scenario="wrong_variant")
    by_key = {f["key"]: f for f in result["facts"]}
    assert by_key["warranty.type"]["value"] == "seller"
    assert by_key["product.region"]["value"] == "AE"
    assert by_key["warranty.region"]["value"] == "AE"
    # price + condition observed even on the failure path
    assert by_key["price.amount_paise"]["value"] == 1149900
    assert by_key["condition"]["value"] == "new"
    for fact in result["facts"]:
        assert fact["synthetic"] is True
        assert fact["scenario_id"] == "scenario_wrong_variant"


# ------------------------------------------------------------------ deliver: late


def test_deliver_late_after_promised_date(isolated_env):
    from datetime import date, timedelta

    store = isolated_env["store"]
    ids = _seed_contract(store)
    promised = str(date.today() - timedelta(days=1))  # promised yesterday
    store.update(ids["delivery.promised_by_date"], value=promised)

    result = service.apply_fulfillment_event("con_test01", "deliver", scenario="late")
    by_key = {f["key"]: f for f in result["facts"]}
    delivered = date.fromisoformat(by_key["delivery.delivered_date"]["value"])
    assert delivered == date.fromisoformat(promised) + timedelta(days=3)
    # correct warranty/region values on late scenario — only timing slipped
    assert by_key["warranty.type"]["value"] == "manufacturer"
    assert by_key["product.region"]["value"] == "IN"
    assert by_key["warranty.region"]["value"] == "IN"
    days_late_fact = by_key.get("delivery.days_late")
    assert days_late_fact and days_late_fact["value"] == 3


def test_deliver_correct_copies_promise_values(isolated_env):
    store = isolated_env["store"]
    _seed_contract(store)
    result = service.apply_fulfillment_event("con_test01", "deliver", scenario="correct")
    by_key = {f["key"]: f for f in result["facts"]}
    assert by_key["warranty.type"]["value"] == "manufacturer"
    assert by_key["warranty.region"]["value"] == "IN"
    assert by_key["product.region"]["value"] == "IN"
    assert by_key["condition"]["value"] == "new"
    assert by_key["price.amount_paise"]["value"] == 1149900
    assert by_key["delivery.delivered_date"]["synthetic"] is True


def test_deliver_price_fact_prefers_contract_amount(isolated_env):
    """Contract amount_paise wins over the promise value when both exist."""
    from datetime import date, timedelta

    store = isolated_env["store"]
    ids = _seed_contract(store)
    store.update(
        ids["delivery.promised_by_date"],
        value=str(date.today() - timedelta(days=1)),
    )
    service.apply_fulfillment_event("con_test01", "deliver", scenario="late")
    stored = [r for r in store.list("fact") if r.get("key") == "price.amount_paise"]
    assert stored and stored[0]["value"] == 1149900


# ------------------------------------------------------------------ replacement


def test_replacement_unavailable_fact(isolated_env):
    store = isolated_env["store"]
    _seed_contract(store)
    result = service.apply_fulfillment_event(
        "con_test01", "replacement_check", scenario="unavailable"
    )
    fact = result["facts"][0]
    assert fact["key"] == "replacement.available"
    assert fact["value"] is False
    assert fact["synthetic"] is True


def test_replacement_unavailable_scopes_each_basket_line(isolated_env):
    store = isolated_env["store"]
    store.put(
        {
            "_type": "contract",
            "id": "con_lines",
            "status": "PAID",
            "amount_paise": 300000,
            "line_items": [
                {"id": "li_monitor", "amount_paise": 200000},
                {"id": "li_keyboard", "amount_paise": 100000},
            ],
        }
    )

    result = service.apply_fulfillment_event(
        "con_lines", "replacement_check", scenario="unavailable"
    )

    facts = result["facts"]
    assert {fact["line_item_id"] for fact in facts} == {
        "li_monitor",
        "li_keyboard",
    }
    assert all(fact["value"] is False for fact in facts)


# ------------------------------------------------------------------ demo routes


def test_demo_endpoints_blocked_without_demo_mode(monkeypatch):
    import project_dante.api.routes.demo as demo_mod

    app = FastAPI()
    app.include_router(demo_router, prefix="/api")

    class LockedSettings:
        demo_mode = False

    # The route resolves settings for each request so operator-token rotation
    # cannot leave a stale module-level snapshot in the state-changing gate.
    monkeypatch.setattr(demo_mod, "get_settings", lambda: LockedSettings())
    client = TestClient(app)
    for path in (
        "/api/demo/reset",
        "/api/demo/contracts/con_x/ship",
        "/api/demo/contracts/con_x/replacement-unavailable",
    ):
        resp = client.post(path)
        assert resp.status_code == 403, path
    resp = client.post("/api/demo/contracts/con_x/deliver", json={"scenario": "correct"})
    assert resp.status_code == 403


def test_demo_reset_seeds_catalog(isolated_env, monkeypatch):
    import project_dante.api.routes.demo as demo_mod

    app = FastAPI()
    app.include_router(demo_router, prefix="/api")
    monkeypatch.setattr(demo_mod, "STORE", isolated_env["store"])
    client = TestClient(app)

    # dirty the shared log first; reset must clear it
    append_event(
        aggregate_type="test", aggregate_id="t1", event_type="CATALOG_SEARCHED"
    )
    assert LOG.all(), "precondition"

    resp = client.post("/api/demo/reset")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reset"] is True
    assert body["products"] >= 100
    assert body["synthetic"] is True
    # reset clears the log, then seeding appends exactly one audit event back
    remaining = LOG.all()
    assert all(e["event_type"] != "CATALOG_SEARCHED" or e["payload"].get("seeded")
               for e in remaining)
    assert len(remaining) <= 1, "reset must clear pre-existing events"
    assert isolated_env["store"].count("offer") >= 100


def test_demo_ship_and_deliver_routes(isolated_env, monkeypatch):
    import project_dante.api.routes.demo as demo_mod

    store = isolated_env["store"]
    _seed_contract(store)
    app = FastAPI()
    app.include_router(demo_router, prefix="/api")
    monkeypatch.setattr(demo_mod, "STORE", store)
    client = TestClient(app)

    resp = client.post("/api/demo/contracts/con_test01/ship")
    assert resp.status_code == 200
    assert resp.json()["synthetic"] is True

    resp = client.post(
        "/api/demo/contracts/con_test01/deliver", json={"scenario": "wrong_variant"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["synthetic"] is True
    facts = {f["key"]: f["value"] for f in body["observed_facts"]}
    assert facts["warranty.type"] == "seller"
    assert facts["product.region"] == "AE"
    # verifier module doesn't exist yet (Agent D) -> honest error marker
    assert body["verification_error"] is not None

    resp = client.post("/api/demo/contracts/con_test01/replacement-unavailable")
    assert resp.status_code == 200
    facts = {f["key"]: f["value"] for f in resp.json()["observed_facts"]}
    assert facts["replacement.available"] is False


def test_demo_route_unknown_contract_404(isolated_env, monkeypatch):
    import project_dante.api.routes.demo as demo_mod

    app = FastAPI()
    app.include_router(demo_router, prefix="/api")
    monkeypatch.setattr(demo_mod, "STORE", isolated_env["store"])
    client = TestClient(app)
    resp = client.post("/api/demo/contracts/con_missing/ship")
    assert resp.status_code == 404


def test_demo_deliver_invalid_scenario_422(isolated_env, monkeypatch):
    import project_dante.api.routes.demo as demo_mod

    store = isolated_env["store"]
    _seed_contract(store)
    app = FastAPI()
    app.include_router(demo_router, prefix="/api")
    monkeypatch.setattr(demo_mod, "STORE", store)
    client = TestClient(app)
    resp = client.post(
        "/api/demo/contracts/con_test01/deliver", json={"scenario": "exploded"}
    )
    assert resp.status_code == 422


# ------------------------------------------------------------------ merchant routes


def test_merchant_search_route(isolated_env):
    from project_dante.api.routes.merchant import router as merchant_router

    app = FastAPI()
    app.include_router(merchant_router, prefix="/api")
    client = TestClient(app)

    resp = client.get(
        "/api/merchant/catalog/search",
        params={"q": "anc headphones", "max_price_paise": 1200000},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results
    assert all(r["unit_amount_paise"] <= 1200000 for r in results)


def test_merchant_product_route_404(isolated_env):
    from project_dante.api.routes.merchant import router as merchant_router

    app = FastAPI()
    app.include_router(merchant_router, prefix="/api")
    client = TestClient(app)
    assert client.get("/api/merchant/products/NOPE").status_code == 404
    ok = client.get("/api/merchant/products/AST-HP-ANC-001")
    assert ok.status_code == 200
    assert ok.json()["offers"]


def test_merchant_analytics_honest_math(isolated_env, monkeypatch):
    from project_dante.api.routes.merchant import router as merchant_router

    store = isolated_env["store"]
    # two evaluations: one feasible, one blocked on warranty metadata
    store.put({
        "_type": "evaluation", "id": "eval_1", "feasible": True, "hard_failures": [],
    })
    store.put({
        "_type": "evaluation", "id": "eval_2", "feasible": False,
        "hard_failures": [{"key": "warranty.type", "op": "eq"}],
    })
    app = FastAPI()
    app.include_router(merchant_router, prefix="/api")
    monkeypatch.setattr("project_dante.api.routes.merchant.STORE", store)
    client = TestClient(app)

    resp = client.get("/api/merchant/analytics")
    assert resp.status_code == 200
    metrics = resp.json()
    assert metrics["total_products"] >= 100
    assert 0 < metrics["warranty_metadata_coverage"] < 1
    assert 0 < metrics["machine_readable_return_policy"] < 1
    assert metrics["evaluated_intents"] == 2
    assert abs(metrics["ai_transactable_rate"] - 0.5) < 0.001
    assert metrics["blocker_distribution"].get("warranty.type") == 1
