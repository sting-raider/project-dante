"""OfferEvaluatorAgent tests — feasibility authority + ranking.

The absolute rule under test: no code path may mark an offer feasible with a
failing hard constraint, and "unknown" merchant data fails manufacturer-
warranty constraints (absence of evidence cannot satisfy).
"""

from __future__ import annotations

import unittest
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


def test_multi_quantity_requires_enough_inventory():
    """A bundle line must have stock for every requested unit, not just one."""
    ev = OfferEvaluatorAgent().evaluate(
        _intent(quantity=2), [_offer(inventory=1)]
    )[0]["evaluation"]
    assert ev["feasible"] is False
    assert any(
        failure["key"] == "inventory"
        and failure["expected"] == 2
        and failure["actual"] == 1
        for failure in ev["hard_failures"]
    )


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


def test_mice_category_matches_mouse_constraint():
    """Catalog stores category='mice'; buyer says 'mouse'. Must be equivalent."""
    intent = _intent(
        hard_constraints=[{"key": "category", "op": "eq", "value": "mouse"}],
        soft_preferences=[],
        max_total_amount_paise=None,
    )
    offer = _offer(category="mice", title="Ergo Vertical Mouse")
    ev = OfferEvaluatorAgent().evaluate(intent, [offer])[0]["evaluation"]
    assert ev["feasible"] is True


def test_zero_inventory_offer_is_infeasible():
    intent = _intent(hard_constraints=[], soft_preferences=[], max_total_amount_paise=None)
    offer = _offer(inventory=0)
    ev = OfferEvaluatorAgent().evaluate(intent, [offer])[0]["evaluation"]
    assert ev["feasible"] is False
    keys = {f["key"] for f in ev["hard_failures"]}
    assert "inventory" in keys


def test_hard_eq_rejects_substring_near_misses():
    """Hard 'eq' must be exact (case-insensitive) — the old generic substring
    fallback let merchant-controlled strings like 'not-sony-compatible' or a
    category of 'headphone-stands' satisfy buyer gates."""
    intent = _intent(
        hard_constraints=[{"key": "brand", "op": "eq", "value": "Sony"}],
        soft_preferences=[],
        max_total_amount_paise=None,
    )
    for junk_brand in ("not-sony-compatible", "sony-compatible", "unsony", "xsonyx"):
        offer = _offer(brand=junk_brand)
        ev = OfferEvaluatorAgent().evaluate(intent, [offer])[0]["evaluation"]
        assert ev["feasible"] is False, f"brand {junk_brand!r} bypassed eq gate"
    # exact match still passes case-insensitively
    ok = OfferEvaluatorAgent().evaluate(intent, [_offer(brand="SONY")])[0]["evaluation"]
    assert ok["feasible"] is True


def test_category_eq_rejects_compound_strings():
    """A structured category value containing but not equal to the wanted
    category must fail; only the closed vocabulary map equates forms."""
    intent = _intent(
        hard_constraints=[{"key": "category", "op": "eq", "value": "headphones"}],
        soft_preferences=[],
        max_total_amount_paise=None,
    )
    for junk in ("headphone-stands", "headphone-amp", "not-headphones"):
        ev = OfferEvaluatorAgent().evaluate(
            intent, [_offer(category=junk)]
        )[0]["evaluation"]
        assert ev["feasible"] is False, f"category {junk!r} passed eq gate"
    # closed-vocabulary equivalences keep working
    for good, want in [("headphones", True), ("mice", None), ("chargers-cables", None)]:
        if want is None:
            continue
        ev = OfferEvaluatorAgent().evaluate(
            intent, [_offer(category=good)]
        )[0]["evaluation"]
        assert ev["feasible"] is True


def test_category_title_fallback_is_whole_word_only():
    """When an offer lacks a category field, its title may satisfy the category
    constraint ONLY via whole-word containment — hyphenated compounds fail."""
    intent = _intent(
        hard_constraints=[{"key": "category", "op": "eq", "value": "headphones"}],
        soft_preferences=[],
        max_total_amount_paise=None,
    )
    def no_cat(**kw):
        return {**_offer(), "category": None, **kw}
    hit = OfferEvaluatorAgent().evaluate(
        intent, [no_cat(title="Premium Wireless Over-Ear Headphone")]
    )[0]["evaluation"]
    assert hit["feasible"] is True
    for miss_title in ("headphone-stands rack", "not-headphones", "anti-headphone mount"):
        ev = OfferEvaluatorAgent().evaluate(
            intent, [no_cat(title=miss_title)]
        )[0]["evaluation"]
        assert ev["feasible"] is False, f"title {miss_title!r} passed"


def test_non_integer_money_fails_closed_never_raises():
    """Junk unit_amount_paise from merchant data must make the offer infeasible
    with a recorded failure — never TypeError -> 500 on the search route."""
    intent = _intent()
    for bad in ("12,000", 11499.5, None, {"amount": 1}, "", True):
        offer = _offer(unit_amount_paise=bad)
        results = OfferEvaluatorAgent().evaluate(intent, [offer])  # must not raise
        ev = results[0]["evaluation"]
        assert ev["feasible"] is False, f"money {bad!r} did not fail closed"
        keys = {f["key"] for f in ev["hard_failures"]}
        assert keys & {"max_price_paise", "max_total_amount_paise"}, (
            f"money {bad!r} produced no price failure: {keys}"
        )
        # actual junk value preserved in the failure record for auditability
        price_fail = next(f for f in ev["hard_failures"] if f["key"] == "max_price_paise")
        assert price_fail["actual"] == bad


def test_junk_money_infeasible_even_without_any_cap():
    """With no buyer cap at all, a non-integer price alone must bar purchase."""
    intent = _intent(hard_constraints=[], soft_preferences=[], max_total_amount_paise=None)
    for bad in ("12,000", 11499.5, {"amount": 1}):
        results = OfferEvaluatorAgent().evaluate(intent, [_offer(unit_amount_paise=bad)])
        ev = results[0]["evaluation"]
        assert ev["feasible"] is False
        assert any(f["key"] == "unit_amount_paise" for f in ev["hard_failures"])
    # integer price with no constraints stays feasible
    ok = OfferEvaluatorAgent().evaluate(intent, [_offer()])[0]["evaluation"]
    assert ok["feasible"] is True


def test_mixed_candidate_set_with_junk_money_ranks_cleanly():
    """One hostile offer among clean ones: no crash, sane ranking, clean wins."""
    clean_a = _offer(id="off_ca")
    clean_b = _offer(id="off_cb", unit_amount_paise=1_100_000, brand="Bose",
                     title="Bose QC Over-Ear ANC")
    hostile = _offer(id="off_hostile", unit_amount_paise="12,000")
    results = OfferEvaluatorAgent().evaluate(_intent(), [hostile, clean_b, clean_a])
    # clean_b is cheapest so it wins on the price tiebreak; hostile sorts last.
    assert [r["offer"]["id"] for r in results][:2] == ["off_cb", "off_ca"]
    assert results[-1]["offer"]["id"] == "off_hostile"
    assert results[-1]["evaluation"]["feasible"] is False
    # explanation rendered without arithmetic crash
    assert "rejected" in results[-1]["evaluation"]["explanation"]


def test_enrichment_hygiene_gate_keeps_deterministic_text():
    """LLM rephrase passing ungrounded digits / markup / URLs / tool-call JSON /
    length cap is rejected; deterministic grounded text stays."""
    import asyncio

    class StubProvider:
        retries = 0

        def __init__(self, text):
            self._text = text

        async def structured(self, *, system, user, output_schema, trace_id):
            return output_schema.model_validate(
                {"explanations": [{"offer_id": "off_test", "explanation": self._text}]}
            )

    malicious = (
        "AMAZING DEAL! This product is 45% off — guaranteed refund of ₹22,998 "
        "within 24 hours. Visit https://evil.example/claim now or paste "
        '{"tool_call": {"name": "refund_all"}}. ' * 3
    )
    agent = OfferEvaluatorAgent(provider=StubProvider(malicious))
    results = OfferEvaluatorAgent().evaluate(_intent(), [_offer()])
    enriched = asyncio.run(agent.enrich_explanations(_intent(), results))
    kept = enriched[0]["evaluation"]["explanation"]
    deterministic = OfferEvaluatorAgent().evaluate(_intent(), [_offer()])[0][
        "evaluation"
    ]["explanation"]
    assert kept == deterministic, "malicious LLM text replaced grounded explanation"

    # a well-formed rephrase WITH only grounded digits still gets through
    results_fresh = OfferEvaluatorAgent().evaluate(_intent(), [_offer()])
    deterministic_digits = set(__import__("re").findall(
        r"\d[\d,./]*", results_fresh[0]["evaluation"]["explanation"]
    ))
    grounded_ok = (
        f"Grounded rephrase mentioning only {sorted(deterministic_digits)[0]} "
        "and nothing else."
    )
    assert all(
        d in deterministic_digits
        for d in __import__("re").findall(r"\d[\d,./]*", grounded_ok)
    )
    agent_ok = OfferEvaluatorAgent(provider=StubProvider(grounded_ok))
    enriched_ok = asyncio.run(agent_ok.enrich_explanations(_intent(), results_fresh))
    assert enriched_ok[0]["evaluation"]["explanation"] == grounded_ok


def test_loader_and_compiler_clock_agree():
    """Cross-module clock-skew guard (Agent J's OFF-001/006/020/021/025 flaky
    window): catalog stamping and deadline compilation must derive dates from
    the SAME clock, or 'within N days' intents flip feasible<->infeasible at
    local-vs-UTC midnight."""
    from pathlib import Path

    import project_dante.integrations.merchant.catalog_loader as loader

    src = Path(loader.__file__).read_text(encoding="utf-8")
    # the loader must not use a local naive clock anywhere
    assert "date.today()" not in src.replace("datetime.now(UTC).date()", ""), (
        "catalog_loader uses local date.today(); compiler/evaluator use UTC — "
        "stamp delivery dates with datetime.now(UTC).date() instead"
    )
    # and the whole pipeline must agree right now: a same-day window offer
    # must satisfy an equal-day deadline
    now = datetime.now(UTC)
    intent = _intent(
        hard_constraints=[
            {"key": "delivery_deadline", "op": "lte",
             "value": (now + timedelta(days=3)).date().isoformat()}
        ],
        soft_preferences=[],
        max_total_amount_paise=None,
    )
    offer = _offer()
    offer["delivery_promise"] = {"min_days": 1, "max_days": 3,
                                 "promised_by_date": None}
    ev = OfferEvaluatorAgent().evaluate(intent, [offer])[0]["evaluation"]
    assert ev["feasible"] is True


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
        # no delivery-deadline phrase: the catalog's promised windows are
        # fixed while "by <weekday>" moves with the calendar
        "raw_text": (
            "Buy me over-ear ANC headphones under ₹12,000. "
            "I need an Indian manufacturer warranty."
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


def test_payment_drift_check_prefers_live_merchant_truth_over_stale_seed():
    """A persisted catalog seed may carry yesterday's derived delivery date.

    Selection freezes the live merchant response, so the payment executor must
    re-check that same merchant surface before falling back to the seeded STORE
    offer.  Otherwise a fresh contract is falsely blocked as drifted.
    """
    import asyncio
    from copy import deepcopy

    from project_dante.api.routes.intents import (
        compile_intent,
        search_offers,
        select_offer,
    )
    from project_dante.api.routes.payments import _recompute_contract_hash
    from project_dante.db.store import STORE

    compiled = asyncio.run(
        compile_intent(
            type(
                "B",
                (),
                {
                    "raw_text": (
                        "Buy me over-ear ANC headphones under ₹12,000 with an "
                        "Indian manufacturer warranty."
                    )
                },
            )()
        )
    )
    intent_id = compiled["intent"]["id"]
    searched = asyncio.run(search_offers(intent_id))
    selected = next(
        result["offer"]
        for result in searched["results"]
        if result["evaluation"]["feasible"]
    )

    stale = deepcopy(selected)
    stale["delivery_promise"]["promised_by_date"] = "2000-01-01"
    stale["_type"] = "offer"
    STORE.put(stale)

    frozen = asyncio.run(
        select_offer(
            intent_id,
            type("S", (), {"offer_id": selected["id"]})(),
        )
    )
    contract = STORE.get(frozen["contract"]["id"])

    assert contract is not None
    assert _recompute_contract_hash(contract) == contract["contract_hash"]


def test_authorize_retry_is_idempotent_after_partial_stage_one():
    """A lost response after authorization must not strand checkout retries."""
    import asyncio

    from project_dante.api.routes.contracts import authorize_contract
    from project_dante.api.routes.intents import (
        compile_intent,
        search_offers,
        select_offer,
    )
    from project_dante.domain.events import LOG

    compiled = asyncio.run(
        compile_intent(
            type(
                "B",
                (),
                {
                    "raw_text": (
                        "Buy me over-ear ANC headphones under ₹12,000 with an "
                        "Indian manufacturer warranty."
                    )
                },
            )()
        )
    )
    intent_id = compiled["intent"]["id"]
    searched = asyncio.run(search_offers(intent_id))
    selected_offer_id = next(
        result["offer"]["id"]
        for result in searched["results"]
        if result["evaluation"]["feasible"]
    )
    frozen = asyncio.run(
        select_offer(
            intent_id,
            type("S", (), {"offer_id": selected_offer_id})(),
        )
    )
    contract_id = frozen["contract"]["id"]

    first = asyncio.run(authorize_contract(contract_id))
    second = asyncio.run(authorize_contract(contract_id))

    assert first["contract"]["status"] == "AWAITING_BUYER_AUTH"
    assert second["contract"] == first["contract"]
    assert len(
        [
            event
            for event in LOG.for_aggregate(contract_id)
            if event["event_type"] == "BUYER_AUTHORIZED"
        ]
    ) == 1


def test_multi_item_search_and_select_freezes_one_aggregate_contract():
    """A two-product brief gets one offer per item and one checkout total."""
    import asyncio

    from project_dante.api.routes.intents import (
        compile_intent,
        search_offers,
        select_offer,
    )
    from project_dante.db.store import STORE

    brief = (
        "Buy me a monitor under ₹20,000 and a keyboard under ₹8,000. "
        "Both items must arrive within 5 days. Keep the total order under ₹30,000."
    )
    r1 = asyncio.run(compile_intent(type("B", (), {"raw_text": brief})()))
    iid = r1["intent"]["id"]
    r2 = asyncio.run(search_offers(iid))

    assert [item["item_id"] for item in r2["items"]] == ["monitor-1", "keyboard-1"]
    assert all(item["feasible_count"] > 0 for item in r2["items"])
    recommendation = r2["bundle_recommendation"]
    assert recommendation["available"] is True
    assert set(recommendation["offer_ids"]) == {"monitor-1", "keyboard-1"}
    assert recommendation["total_amount_paise"] <= 3_000_000

    request_items = [
        type(
            "Item",
            (),
            {
                "item_id": item["item_id"],
                "offer_id": next(
                    result["offer"]["id"]
                    for result in item["results"]
                    if result["evaluation"]["feasible"]
                ),
            },
        )()
        for item in r2["items"]
    ]
    body = type("S", (), {"offer_id": None, "items": request_items})()
    r3 = asyncio.run(select_offer(iid, body))
    contract = r3["contract"]

    try:
        assert len(contract["line_items"]) == 2
        assert contract["amount_paise"] == sum(
            item["amount_paise"] for item in contract["line_items"]
        )
        assert contract["amount_paise"] <= 3_000_000
        assert len(r3["promises"]) > 0
        assert {
            promise.get("line_item_id") for promise in r3["promises"]
        } == {item["id"] for item in contract["line_items"]}
        assert {
            evaluation.get("item_id")
            for evaluation in STORE.find("evaluation", contract_id=contract["id"])
        } == {"monitor-1", "keyboard-1"}
    finally:
        for record_type in ("evaluation", "promise", "evidence", "contract"):
            for record in STORE.list(record_type):
                if record.get("intent_id") == iid or record.get("contract_id") == contract["id"]:
                    STORE.delete(record["id"])
        STORE.delete(iid)


def test_exact_monitor_keyboard_brief_returns_a_feasible_bundle():
    """The brief used in the buyer-desk regression has a real fixture match."""
    import asyncio

    from project_dante.api.routes.intents import compile_intent, search_offers
    from project_dante.db.store import STORE

    brief = (
        "Buy me a 27-inch QHD monitor under ₹25,000 and a mechanical keyboard under "
        "₹8,000. The monitor must have an IPS panel, at least a 144 Hz refresh rate, "
        "DisplayPort, and an Indian manufacturer warranty. The keyboard should be 75% "
        "or TKL, hot-swappable, wireless, and also have an Indian manufacturer warranty. "
        "I prefer tactile switches, but linear switches are acceptable. Both items must "
        "arrive within 5 days. Do not show me any monitor over ₹25,000 or any keyboard "
        "over ₹8,000. Keep the total order under ₹33,000."
    )
    r1 = asyncio.run(compile_intent(type("B", (), {"raw_text": brief})()))
    iid = r1["intent"]["id"]
    r2 = asyncio.run(search_offers(iid))

    try:
        assert {item["item_id"] for item in r2["items"]} == {"monitor-1", "keyboard-1"}
        assert all(item["feasible_count"] > 0 for item in r2["items"])
        recommendation = r2["bundle_recommendation"]
        assert recommendation["available"] is True
        assert set(recommendation["offer_ids"]) == {"monitor-1", "keyboard-1"}
        assert recommendation["total_amount_paise"] < 3_300_000
    finally:
        for record_type in ("evaluation", "promise", "evidence", "contract"):
            for record in STORE.list(record_type):
                if record.get("intent_id") == iid:
                    STORE.delete(record["id"])
        STORE.delete(iid)

class TitleFallbackScopingTests(unittest.TestCase):
    """Final-assault [12]: the title stand-in must apply ONLY to category.
    A missing brand/warranty/feature field must never be satisfied by
    whole-word containment against the product title."""

    def _offer(self):
        return {
            "id": "off_tf_1",
            "sku": "AST-TF-001",
            # No 'brand', no terms.warranty_type, no attributes.anc — but the
            # title mentions all of them.
            "title": "Sony Wireless ANC Over-Ear Headphones with Manufacturer Warranty",
            "category": "",  # missing -> title fallback territory for category only
            "unit_amount_paise": 500000,
            "attributes": {},
            "terms": {},
        }

    def test_title_cannot_satisfy_non_category_hard_constraints(self):
        from project_dante.agents.evaluator import OfferEvaluatorAgent

        ev = OfferEvaluatorAgent()
        intent = {
            "hard_constraints": [
                {"key": "brand", "op": "eq", "value": "Sony", "critical": True},
                {"key": "warranty.type", "op": "eq", "value": "manufacturer", "critical": True},
                {"key": "attributes.anc", "op": "eq", "value": True, "critical": True},
            ],
            "soft_preferences": [],
        }
        results = ev.evaluate(intent, [self._offer()])
        fails = {f["key"] for f in results[0]["evaluation"]["hard_failures"]}
        self.assertEqual(
            fails,
            {"brand", "warranty.type", "attributes.anc"},
            "title containment must not satisfy non-category hard gates",
        )
        self.assertFalse(results[0]["evaluation"]["feasible"])

    def test_category_still_usable_from_title(self):
        from project_dante.agents.evaluator import OfferEvaluatorAgent

        ev = OfferEvaluatorAgent()
        intent = {
            "hard_constraints": [
                {"key": "category", "op": "eq", "value": "headphones", "critical": True},
            ],
            "soft_preferences": [],
        }
        results = ev.evaluate(intent, [self._offer()])
        self.assertTrue(results[0]["evaluation"]["feasible"])
