"""OfferEvaluatorAgent tests — feasibility authority + ranking.

The absolute rule under test: no code path may mark an offer feasible with a
failing hard constraint, and "unknown" merchant data fails manufacturer-
warranty constraints (absence of evidence cannot satisfy).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from project_dante.agents.evaluator import OfferEvaluatorAgent

NOW = datetime.now(UTC)
THURSDAY = NOW + timedelta(days=((3 - NOW.weekday()) % 7) or 7)


def _offer(**kw):
    base = {
        "id": "off_test",
        "merchant_id": "aster-electronics",
        "sku": "SKU-TEST",
        "title": "Aster ANC Pro Over-Ear Headphones",
        "unit_amount_paise": 1_149_900,
        "inventory": 5,
        "category": "headphones",
        "brand": "Sony",
        "attributes": {"form_factor": "over-ear", "anc": True},
        "delivery_promise": {
            "min_days": 2,
            "max_days": 3,
            "promised_by_date": THURSDAY.date().isoformat(),
            "service": "BlueDart",
        },
        "terms": {
            "warranty_type": "manufacturer",
            "warranty_duration_months": 12,
            "warranty_region": "IN",
            "return_window_days": 7,
            "condition": "new",
            "region": "IN",
        },
    }
    base.update(kw)
    return base


def _intent(**kw):
    base = {
        "id": "int_test",
        "raw_text": "hero query",
        "hard_constraints": [
            {"key": "category", "op": "eq", "value": "headphones", "critical": True},
            {"key": "max_price_paise", "op": "lte", "value": 1_200_000, "critical": True},
            {"key": "attributes.form_factor", "op": "eq", "value": "over-ear", "critical": True},
            {"key": "attributes.anc", "op": "eq", "value": True, "critical": True},
            {"key": "warranty.type", "op": "eq", "value": "manufacturer", "critical": True},
            {"key": "warranty.region", "op": "eq", "value": "IN", "critical": True},
            {
                "key": "delivery_deadline",
                "op": "lte",
                "value": THURSDAY.date().isoformat(),
                "critical": True,
            },
        ],
        "soft_preferences": [{"key": "brand", "weight": 0.6, "value": "Sony"}],
        "max_total_amount_paise": 1_200_000,
    }
    base.update(kw)
    return base


def test_feasible_offer_passes():
    results = OfferEvaluatorAgent().evaluate(_intent(), [_offer()])
    assert results[0]["evaluation"]["feasible"] is True
    assert results[0]["evaluation"]["hard_failures"] == []


def test_unknown_warranty_fails_manufacturer_constraint():
    offer = _offer(terms={**_offer()["terms"], "warranty_type": "unknown",
                          "warranty_region": None})
    results = OfferEvaluatorAgent().evaluate(_intent(), [offer])
    ev = results[0]["evaluation"]
    assert ev["feasible"] is False
    keys = {f["key"] for f in ev["hard_failures"]}
    assert "warranty.type" in keys and "warranty.region" in keys


def test_missing_warranty_field_fails_not_passes():
    """Absent structured terms must fail the constraint, never default to pass."""
    offer = _offer()
    offer.pop("terms")
    results = OfferEvaluatorAgent().evaluate(_intent(), [offer])
    assert results[0]["evaluation"]["feasible"] is False


def test_expensive_offer_fails_price_cap():
    results = OfferEvaluatorAgent().evaluate(_intent(), [_offer(unit_amount_paise=1_300_000)])
    ev = results[0]["evaluation"]
    assert ev["feasible"] is False
    keys = {f["key"] for f in ev["hard_failures"]}
    assert "max_price_paise" in keys or "max_total_amount_paise" in keys


def test_wrong_category_and_form_factor_fail():
    offer = _offer(category="earbuds", attributes={"form_factor": "earbuds", "anc": True})
    ev = OfferEvaluatorAgent().evaluate(_intent(), [offer])[0]["evaluation"]
    assert ev["feasible"] is False


def test_seller_warranty_fails_manufacturer_requirement():
    offer = _offer(terms={**_offer()["terms"], "warranty_type": "seller"})
    ev = OfferEvaluatorAgent().evaluate(_intent(), [offer])[0]["evaluation"]
    assert ev["feasible"] is False
    assert any(f["key"] == "warranty.type" for f in ev["hard_failures"])


def test_late_delivery_fails_deadline():
    offer = _offer(delivery_promise={
        **_offer()["delivery_promise"],
        "promised_by_date": (THURSDAY + timedelta(days=3)).date().isoformat(),
    })
    ev = OfferEvaluatorAgent().evaluate(_intent(), [offer])[0]["evaluation"]
    assert ev["feasible"] is False
    assert any(f["key"] == "delivery_deadline" for f in ev["hard_failures"])


def test_max_days_within_deadline_passes_when_no_promised_date():
    offer = _offer()
    offer["delivery_promise"].pop("promised_by_date")
    # Window ends exactly on the deadline regardless of today's weekday.
    offer["delivery_promise"]["max_days"] = (THURSDAY.date() - NOW.date()).days
    results = OfferEvaluatorAgent().evaluate(_intent(), [offer])
    assert results[0]["evaluation"]["feasible"] is True


def test_ranking_prefers_better_soft_score_then_cheaper():
    good = _offer(id="off_good", brand="Sony")  # preferred brand + cheaper
    pricier = _offer(id="off_pricy", brand="Bose", unit_amount_paise=1_190_000,
                     title="Bose QC Over-Ear ANC")
    results = OfferEvaluatorAgent().evaluate(_intent(), [pricier, good])
    feasible = [r for r in results if r["evaluation"]["feasible"]]
    assert len(feasible) == 2
    # Sony brand preference should rank off_good first despite both feasible.
    assert results[0]["offer"]["id"] == "off_good"
    assert results[0]["rank"] == 1


def test_infeasible_sorted_after_feasible():
    ok = _offer(id="off_ok")
    # cheap but ANC flag absent -> fails hard constraint
    bad = _offer(id="off_bad", unit_amount_paise=99_00)
    bad["attributes"] = {"form_factor": "over-ear"}
    results = OfferEvaluatorAgent().evaluate(_intent(), [bad, ok])
    assert results[0]["offer"]["id"] == "off_ok"
    assert results[-1]["evaluation"]["feasible"] is False


def test_contradictory_intent_yields_zero_feasible():
    """Compiler records contradictions; evaluator finds no feasible offers."""
    intent = _intent(hard_constraints=_intent()["hard_constraints"]
                     + [{"key": "category", "op": "eq", "value": "laptop", "critical": True}])
    results = OfferEvaluatorAgent().evaluate(intent, [_offer()])
    assert all(r["evaluation"]["feasible"] is False for r in results)


def test_hard_failures_shape_matches_contract():
    results = OfferEvaluatorAgent().evaluate(
        _intent(hard_constraints=[{"key": "attributes.anc", "op": "eq", "value": True}]),
        [_offer(attributes={"form_factor": "over-ear"})],
    )
    f = results[0]["evaluation"]["hard_failures"][0]
    assert set(f.keys()) >= {"key", "op", "expected", "actual"}
    assert f["expected"] is True and f["actual"] is not True


def test_soft_scores_present_with_notes():
    results = OfferEvaluatorAgent().evaluate(_intent(), [_offer()])
    scores = results[0]["evaluation"]["soft_scores"]
    assert scores and all("key" in s and "weight" in s and "score" in s for s in scores)


# ---------------------------------------------------------------- routes


def test_select_offer_rejects_infeasible_409():
    import asyncio

    from fastapi import HTTPException
    from project_dante.api.routes.intents import _resolve_offer, select_offer
    from project_dante.db.store import STORE

    intent = {
        "id": "int_route_test",
        "_type": "intent",
        "raw_text": "test",
        "hard_constraints": [
            {"key": "attributes.anc", "op": "eq", "value": True, "critical": True},
        ],
        "soft_preferences": [],
        "max_total_amount_paise": None,
    }
    STORE.put(dict(intent))
    offer_id = "off_infeasible_test"
    STORE.put(
        {
            "id": offer_id,
            "_type": "offer",
            "sku": "SKU-INFEAS",
            "title": "No ANC Headphones",
            "unit_amount_paise": 500_000,
            "attributes": {"form_factor": "over-ear", "anc": False},
            "terms": {"warranty_type": "unknown"},
        }
    )
    STORE.put(
        {
            "id": f"{intent['id']}_eval_0",
            "_type": "evaluation",
            "intent_id": intent["id"],
            "offer_id": offer_id,
            "feasible": False,
            "hard_failures": [
                {"key": "attributes.anc", "op": "eq", "expected": True, "actual": False}
            ],
        }
    )

    async def run():
        return await select_offer(
            intent["id"], type("S", (), {"offer_id": offer_id})()
        )

    try:
        asyncio.run(run())
        raise AssertionError("infeasible selection must raise HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 409
        detail = exc.detail
        assert "hard constraint" in str(detail)

    # cleanup so other suites sharing STORE are not polluted
    STORE.delete(intent["id"])
    STORE.delete(offer_id)
    STORE.delete(f"{intent['id']}_eval_0")

    _ = _resolve_offer  # exercised indirectly by route smoke tests


def test_select_offer_requires_prior_search():
    import asyncio

    from fastapi import HTTPException
    from project_dante.api.routes.intents import select_offer
    from project_dante.db.store import STORE

    STORE.put({"id": "int_nosearch", "_type": "intent", "raw_text": "x",
               "hard_constraints": [], "soft_preferences": []})
    STORE.put({"id": "off_nosearch", "_type": "offer", "sku": "S", "title": "T",
               "unit_amount_paise": 1, "attributes": {}, "terms": {}})

    async def run():
        return await select_offer("int_nosearch", type("S", (), {"offer_id": "off_nosearch"})())

    try:
        asyncio.run(run())
        raise AssertionError("select without prior search must raise HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 409
    finally:
        STORE.delete("int_nosearch")
        STORE.delete("off_nosearch")


def test_select_stamps_evaluation_for_verifier_floor():
    """Agent D's _evaluation_floor matches evaluations by contract_id and reads
    the stamped constraint snapshot; verify the stamping survives a real
    select-offer against the merchant catalog."""
    import asyncio

    from project_dante.api.routes.intents import (
        compile_intent,
        search_offers,
        select_offer,
    )
    from project_dante.db.store import STORE
    from project_dante.domain.promises.pipeline import CONSTRAINT_TO_PROMISE

    r1 = asyncio.run(compile_intent(type("B", (), {
        "raw_text": (
            "Buy me over-ear ANC headphones under ₹12,000. I need an Indian "
            "manufacturer warranty, they must arrive by Thursday."
        )
    })()))
    iid = r1["intent"]["id"]
    r2 = asyncio.run(search_offers(iid))
    feasible = [x for x in r2["results"] if x["evaluation"]["feasible"]]
    assert feasible, "hero query must produce at least one feasible offer"
    best = feasible[0]["offer"]["id"]

    r3 = asyncio.run(select_offer(iid, type("S", (), {"offer_id": best})()))
    cid = r3["contract"]["id"]

    ev = STORE.find_one("evaluation", contract_id=cid)
    assert ev is not None, "selected evaluation must be stamped with contract_id"
    keys = {c["key"] for c in ev.get("constraints") or []}
    # critical snapshot uses frozen intent keys verbatim
    assert "max_price_paise" in keys and "warranty.type" in keys
    # every stamped key D knows about maps to a promise key (their map has a
    # known gap for attributes.* — asserted here so the gap can't silently grow)
    unmapped = {k for k in keys if not CONSTRAINT_TO_PROMISE.get(k)}
    assert unmapped <= {"attributes.anc", "attributes.form_factor"}

    # cleanup shared-store pollution
    for rec in STORE.find("evaluation", contract_id=cid):
        STORE.delete(rec["id"])
    STORE.delete(cid)
    STORE.delete(iid)
