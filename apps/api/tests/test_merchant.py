"""Merchant interface + catalog fixture tests (Agent F).

Covers: fixture shape/integrity, hero product exact values, feasible +
near-miss demo cast, search scoring/ordering, product lookup, inventory,
freeze_offer snapshot + rendered text, seeding idempotency.
"""

from __future__ import annotations

import pytest
from project_dante.db.store import STORE
from project_dante.integrations.merchant import service
from project_dante.integrations.merchant.catalog_loader import load_catalog

HERO_SKU = "AST-HP-ANC-001"


# ------------------------------------------------------------------ fixture


def test_catalog_loads_100_plus_products():
    catalog = load_catalog()
    assert len(catalog) >= 100


def test_catalog_unique_skus_and_categories():
    catalog = load_catalog()
    skus = [p["sku"] for p in catalog]
    assert len(skus) == len(set(skus)), "duplicate SKUs in fixture"
    categories = {p["category"] for p in catalog}
    assert {
        "headphones", "phones", "routers", "laptops",
        "keyboards", "mice", "monitors", "chargers-cables",
    } <= categories


def test_hero_product_exact_values():
    hero = next(p for p in load_catalog() if p["sku"] == HERO_SKU)
    assert hero["title"] == "Aster ANC Pro Wireless Over-Ear Headphones"
    assert hero["brand"] == "Aster"
    assert hero["category"] == "headphones"
    assert hero["unit_amount_paise"] == 1149900
    assert hero["attributes"]["form_factor"] == "over-ear"
    assert hero["attributes"]["anc"] is True
    assert hero["attributes"]["connectivity"] == "bluetooth-5.3"
    terms = hero["terms"]
    assert terms["warranty_type"] == "manufacturer"
    assert terms["warranty_duration_months"] == 12
    assert terms["warranty_region"] == "IN"
    assert terms["return_window_days"] == 10
    assert terms["replacement_window_days"] == 10
    assert terms["condition"] == "new"
    dp = hero["delivery_promise"]
    assert dp["min_days"] == 2
    assert dp["max_days"] == 4


def test_feasible_anc_alternatives_exist():
    """Several ANC over-ear offers under Rs 12,000 with manufacturer-IN warranty."""
    feasible = [
        p for p in load_catalog()
        if p["category"] == "headphones"
        and p["attributes"].get("anc") is True
        and p["attributes"].get("form_factor") == "over-ear"
        and p["unit_amount_paise"] < 1200000
        and p["terms"]["warranty_type"] == "manufacturer"
        and p["terms"]["warranty_region"] == "IN"
        and p["terms"]["condition"] == "new"
        and p["inventory"] > 0
    ]
    skus = {p["sku"] for p in feasible}
    assert HERO_SKU in skus
    assert len(feasible) >= 5


def test_near_miss_decoys_exist():
    catalog = load_catalog()
    by_sku = {p["sku"]: p for p in catalog}
    # seller-warranty decoys
    assert by_sku["AST-HP-007"]["terms"]["warranty_type"] == "seller"
    assert by_sku["AST-HP-008"]["terms"]["warranty_type"] == "seller"
    # wrong-region decoys
    assert by_sku["AST-HP-009"]["terms"]["warranty_region"] == "AE"
    assert by_sku["AST-HP-010"]["terms"]["warranty_region"] == "AE"
    # over-budget decoys (manufacturer-IN but above Rs 12,000)
    assert by_sku["AST-HP-011"]["unit_amount_paise"] >= 1200000
    assert by_sku["AST-HP-012"]["unit_amount_paise"] >= 1200000
    # refurbished decoy
    assert by_sku["AST-HP-013"]["terms"]["condition"] == "refurbished"


def test_warranty_mix_is_deliberately_mixed():
    catalog = load_catalog()
    total = len(catalog)
    shares = {}
    for wt in ("manufacturer", "seller", "none", "unknown"):
        share = sum(1 for p in catalog if p["terms"]["warranty_type"] == wt) / total
        shares[wt] = share
    assert abs(shares["manufacturer"] - 0.55) < 0.05
    assert abs(shares["unknown"] - 0.20) < 0.05
    assert shares["none"] > 0.05 and shares["seller"] > 0.10


def test_price_and_inventory_bounds_respected():
    for p in load_catalog():
        assert 499 <= p["unit_amount_paise"] <= 1899990, p["sku"]
        assert 0 <= p["inventory"] <= 60, p["sku"]
        assert p["delivery_promise"]["max_days"] <= 7, p["sku"]


def test_monitor_and_keyboard_feature_fields_are_structured():
    """Catalog claims used by the buyer brief must not live only in titles."""
    by_sku = {p["sku"]: p for p in load_catalog()}

    monitor = by_sku["AST-MN-004"]
    assert monitor["attributes"] == {
        "screen_size_inches": 27,
        "panel": "fast-ips",
        "resolution": "qhd",
        "refresh_rate_hz": 165,
        "connectivity": "hdmi-displayport",
    }
    assert monitor["terms"]["warranty_region"] == "IN"
    assert monitor["delivery_promise"]["max_days"] == 4

    keyboard = by_sku["AST-KB-008"]
    assert keyboard["attributes"]["form_factor"] == "75-percent"
    assert keyboard["attributes"]["connectivity"] == "wireless-multi"
    assert keyboard["attributes"]["mechanical"] is True
    assert keyboard["attributes"]["hot_swappable"] is True
    assert keyboard["attributes"]["switch_type"] == "tactile"


# ------------------------------------------------------------------ search


def test_search_finds_headphones_under_cap():
    results = service.search_catalog(
        query="headphones anc", category="headphones", max_price_paise=1200000
    )
    assert results, "expected matches under cap"
    for p in results:
        assert p["category"] == "headphones"
        assert p["unit_amount_paise"] <= 1200000
    titles = [p["title"].lower() for p in results]
    assert any("anc" in t for t in titles)


def test_search_ordering_score_then_price():
    results = service.search_catalog(query="anc over ear headphones")
    prices = [p["unit_amount_paise"] for p in results]
    assert prices == sorted(prices), "equal-score group must be price ascending"


def test_search_no_match_returns_empty():
    assert service.search_catalog(query="zzzznonexistent") == []


def test_search_limit_respected():
    results = service.search_catalog(query="", limit=3)
    assert len(results) <= 3


def test_search_category_filter():
    results = service.search_catalog(query="router", category="routers")
    assert results
    assert all(p["category"] == "routers" for p in results)


# ------------------------------------------------------------------ products / inventory


def test_get_product_shape():
    found = service.get_product(HERO_SKU)
    assert found is not None
    assert found["product"]["sku"] == HERO_SKU
    assert found["offers"], "product must expose its offer envelope"
    offer = found["offers"][0]
    assert offer["offer_id"] == f"off_{HERO_SKU}"
    assert offer["terms"]["warranty_type"] == "manufacturer"


def test_get_product_unknown_returns_none():
    assert service.get_product("AST-DOES-NOT-EXIST") is None


def test_check_inventory_known_and_unknown():
    assert isinstance(service.check_inventory(HERO_SKU), int)
    assert service.check_inventory(HERO_SKU) == next(
        p["inventory"] for p in load_catalog() if p["sku"] == HERO_SKU
    )
    assert service.check_inventory("AST-MISSING") == 0


# ------------------------------------------------------------------ freeze


def test_freeze_offer_snapshot_and_rendered_text():
    frozen = service.freeze_offer(f"off_{HERO_SKU}")
    offer = frozen["offer"]
    assert offer["inventory_snapshot"] == offer["inventory"]
    assert offer["snapshot_hash"]
    payload = frozen["evidence_payload"]
    assert payload["source_type"] == "checkout_offer"
    assert payload["payload"]["sku"] == HERO_SKU
    text = frozen["rendered_text"]
    assert "12-month official manufacturer warranty" in text
    assert "India" in text
    assert "10-day returns" in text
    assert "Delivery in 2-4 days" in text


def test_freeze_offer_accepts_bare_sku_too():
    frozen = service.freeze_offer(HERO_SKU)
    assert frozen["offer"]["sku"] == HERO_SKU


def test_freeze_offer_unknown_raises():
    with pytest.raises(KeyError):
        service.freeze_offer("off_NOPE")


def test_rendered_text_variants_for_decoys():
    seller = service.freeze_offer("off_AST-HP-007")["rendered_text"]
    assert "seller warranty" in seller.lower()
    ae = service.freeze_offer("off_AST-HP-009")["rendered_text"]
    assert "UAE" in ae
    no_ret = service.freeze_offer("off_AST-PH-018")["rendered_text"]
    assert "Return policy not specified" in no_ret or "returns" in no_ret.lower()


# ------------------------------------------------------------------ seeding


def test_seed_catalog_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("DANTE_STORE_PATH", str(tmp_path / "store.json"))
    fresh_store = STORE.__class__(str(tmp_path / "store.json"))
    monkeypatch.setattr(service, "STORE", fresh_store)

    first = service.seed_catalog()
    assert first == len(load_catalog())
    assert fresh_store.count("offer") == first
    second = service.seed_catalog()
    assert second == 0, "second seed must be a no-op"
