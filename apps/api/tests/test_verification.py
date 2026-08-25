"""Tests for deterministic promise verification (Agent D).

Covers: SATISFIED path, material variant breach (hero story), late-delivery
severity split, missing-observation neutrality, idempotent double-verify,
and contract state transitions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from project_dante.db.store import STORE
from project_dante.domain.promises.pipeline import bind_to_contract, freeze_promise_set
from project_dante.domain.promises.verifier import evaluate_contract

from tests.test_promises import _intent, _offer


def _mk_contract(
    status: str = "DELIVERED",
    offer: dict[str, Any] | None = None,
    intent: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Freeze a promise set and create a matching contract record."""
    result = freeze_promise_set(offer or _offer(), intent or _intent())
    cid = f"con_test_{abs(hash(result['promise_set_hash'])) % 10**8}"
    STORE.put(
        {
            "_type": "contract",
            "id": cid,
            "intent_id": (intent or _intent())["id"],
            "offer_id": (offer or _offer())["id"],
            "promise_ids": result["promise_ids"],
            "offer_hash": result["offer_hash"],
            "promise_set_hash": result["promise_set_hash"],
            "contract_hash": result["contract_hash"],
            "amount_paise": 1149900,
            "status": status,
        }
    )
    bind_to_contract(cid, result["promise_ids"], result["evidence_ids"])
    return cid, result


def _fact(cid: str, key: str, value: Any, at: str | None = None) -> str:
    fid = f"obs_{key.replace('.', '_')}_{abs(hash((cid, key, str(value)))) % 10**8}"
    STORE.put(
        {
            "_type": "fact",
            "id": fid,
            "contract_id": cid,
            "key": key,
            "value": value,
            "observed_at": at or datetime.now(UTC).isoformat(),
            "synthetic": True,
            "scenario_id": "test",
        }
    )
    return fid


# ---------------------------------------------------------------- satisfied


def test_correct_facts_yield_satisfied_no_breaches():
    cid, frozen = _mk_contract()
    # One fact per material promise key.
    _fact(cid, "warranty.type", "manufacturer")
    _fact(cid, "warranty.region", "IN")
    _fact(cid, "product.region", "India")  # alias must normalize to IN
    _fact(cid, "condition", "new")
    _fact(cid, "price.amount_paise", 1149900)
    _fact(cid, "delivery.delivered_date", "2026-08-27T20:00:00+00:00")

    res = evaluate_contract(cid)
    assert res["satisfied"] is True
    assert res["breaches"] == []
    assert res["status_target"] == "SATISFIED"
    assert res["status"] == "SATISFIED"
    assert res["unobserved_material_keys"] == []


def test_region_alias_fact_satisfies():
    cid, _ = _mk_contract()
    for k, v in [
        ("warranty.type", "Manufacturer"),
        ("warranty.region", "India"),
        ("product.region", "IND"),
        ("condition", "new"),
        ("price.amount_paise", 1149900),
        ("delivery.delivered_date", "2026-08-27"),
    ]:
        _fact(cid, k, v)
    res = evaluate_contract(cid)
    assert res["satisfied"] is True


# ---------------------------------------------------------------- hero breach


def test_wrong_warranty_region_and_type_material_breach():
    """Hero story: promised manufacturer/IN warranty, delivered seller/AE unit."""
    cid, _ = _mk_contract()
    _fact(cid, "warranty.type", "seller")
    _fact(cid, "warranty.region", "UAE")
    _fact(cid, "product.region", "AE")
    _fact(cid, "condition", "new")
    _fact(cid, "price.amount_paise", 1149900)
    _fact(cid, "delivery.delivered_date", "2026-08-27T18:00:00+00:00")

    res = evaluate_contract(cid)
    assert res["satisfied"] is False
    assert res["status_target"] == "BREACH_DETECTED"
    assert res["status"] == "BREACH_DETECTED"

    by_key = {}
    for b in res["breaches"]:
        promise = STORE.get(b.promise_id)
        by_key[promise["key"]] = b

    for key in ("warranty.type", "warranty.region", "product.region"):
        b = by_key[key]
        assert b.severity == "material", key
        assert b.reason_code == "MATERIAL_VARIANT_MISMATCH", key

    # Condition matched (both critical in fixture intent): no condition breach.
    assert all(b.reason_code != "CONDITION_MISMATCH" for b in res["breaches"])
    # Delivery was on time: no SLA breach.
    assert all(b.reason_code != "DELIVERY_SLA_MISS" for b in res["breaches"])

    # Breach records persisted + events emitted.
    stored = [b for b in STORE.list("breach") if b["contract_id"] == cid]
    assert len(stored) >= 3
    events = [
        e
        for e in __import__("project_dante.domain.events", fromlist=["LOG"]).LOG.all()
        if e["aggregate_id"] == cid and e["event_type"] == "PROMISE_BREACH_DETECTED"
    ]
    assert len(events) >= 3


def test_condition_mismatch_critical():
    cid, _ = _mk_contract()
    _fact(cid, "warranty.type", "manufacturer")
    _fact(cid, "warranty.region", "IN")
    _fact(cid, "product.region", "IN")
    _fact(cid, "condition", "refurbished")
    _fact(cid, "price.amount_paise", 1149900)
    _fact(cid, "delivery.delivered_date", "2026-08-26")

    res = evaluate_contract(cid)
    cond_breaches = [b for b in res["breaches"] if b.reason_code == "CONDITION_MISMATCH"]
    assert len(cond_breaches) == 1
    assert cond_breaches[0].severity == "critical"


# ---------------------------------------------------------------- delivery


def test_late_2h_is_minor():
    cid, _ = _mk_contract()  # promised_by 2026-08-27T21:00Z
    for k, v in [
        ("warranty.type", "manufacturer"),
        ("warranty.region", "IN"),
        ("product.region", "IN"),
        ("condition", "new"),
        ("price.amount_paise", 1149900),
        ("delivery.delivered_date", "2026-08-27T23:00:00+00:00"),  # +2h
    ]:
        _fact(cid, k, v)
    res = evaluate_contract(cid)
    sla = [b for b in res["breaches"] if b.reason_code == "DELIVERY_SLA_MISS"]
    assert len(sla) == 1
    assert sla[0].severity == "minor"


def test_late_3days_is_material():
    cid, _ = _mk_contract()
    for k, v in [
        ("warranty.type", "manufacturer"),
        ("warranty.region", "IN"),
        ("product.region", "IN"),
        ("condition", "new"),
        ("price.amount_paise", 1149900),
        ("delivery.delivered_date", "2026-08-30T21:00:00+00:00"),  # +72h
    ]:
        _fact(cid, k, v)
    res = evaluate_contract(cid)
    sla = [b for b in res["breaches"] if b.reason_code == "DELIVERY_SLA_MISS"]
    assert len(sla) == 1
    assert sla[0].severity == "material"


def test_delivery_on_promised_day_counts_on_time_for_date_only_deadline():
    """Promised 'by Thursday' => any time that day is on time."""
    offer = _offer(
        delivery_promise={
            "min_days": 2,
            "max_days": 3,
            "promised_by_date": "2026-08-27",
            "service": "standard",
        }
    )
    cid, _ = _mk_contract(offer=offer)
    for k, v in [
        ("warranty.type", "manufacturer"),
        ("warranty.region", "IN"),
        ("product.region", "IN"),
        ("condition", "new"),
        ("price.amount_paise", 1149900),
        ("delivery.delivered_date", "2026-08-27T09:15:00+00:00"),
    ]:
        _fact(cid, k, v)
    res = evaluate_contract(cid)
    assert res["satisfied"] is True


# ---------------------------------------------------------------- missing obs


def test_missing_observation_neither_satisfied_nor_breached():
    cid, _ = _mk_contract(status="PAID")
    _fact(cid, "warranty.type", "manufacturer")
    # no region / condition / price / delivery observations yet

    res = evaluate_contract(cid)
    assert res["satisfied"] is False
    assert res["breaches"] == []
    assert res["status_target"] == "INCONCLUSIVE"
    # category is material but unobservable — excluded from the check
    assert set(res["unobserved_material_keys"]) == {
        "warranty.region",
        "product.region",
        "condition",
        "price.amount_paise",
        "delivery.promised_by_date",
    }


def test_latest_fact_wins_when_multiple_observed():
    cid, _ = _mk_contract()
    _fact(cid, "warranty.type", "seller", at="2026-08-28T10:00:00+00:00")
    _fact(cid, "warranty.type", "manufacturer", at="2026-08-29T10:00:00+00:00")
    _fact(cid, "warranty.region", "India")
    _fact(cid, "product.region", "IN")
    _fact(cid, "condition", "new")
    _fact(cid, "price.amount_paise", 1149900)
    _fact(cid, "delivery.delivered_date", "2026-08-27")

    res = evaluate_contract(cid)
    wt = [
        b
        for b in res["breaches"]
        if b.reason_code == "MATERIAL_VARIANT_MISMATCH"
        and STORE.get(b.promise_id)["key"] == "warranty.type"
    ]
    assert wt == []  # latest observation says manufacturer => matches
    assert res["satisfied"] is True  # region alias 'India' also normalized to IN


# ---------------------------------------------------------------- idempotency


def test_double_verify_no_duplicate_breaches_or_events():
    from project_dante.domain.events import LOG

    cid, _ = _mk_contract()
    _fact(cid, "warranty.type", "seller")
    _fact(cid, "warranty.region", "AE")
    _fact(cid, "product.region", "AE")
    _fact(cid, "condition", "new")
    _fact(cid, "price.amount_paise", 1149900)
    _fact(cid, "delivery.delivered_date", "2026-08-27T18:00:00+00:00")

    r1 = evaluate_contract(cid)
    n1 = len(r1["breaches"])
    ev_count_1 = sum(1 for e in LOG.all() if e["event_type"] == "PROMISE_BREACH_DETECTED")

    r2 = evaluate_contract(cid)
    n2 = len(r2["breaches"])
    ev_count_2 = sum(1 for e in LOG.all() if e["event_type"] == "PROMISE_BREACH_DETECTED")

    assert n1 == n2 > 0  # same breach set returned, nothing duplicated
    assert ev_count_1 == ev_count_2
    stored = [b for b in STORE.list("breach") if b["contract_id"] == cid]
    assert len(stored) == n1


# ------------------------------------------------- selection-time calibration


def test_evaluation_record_floors_minor_to_material():
    """Agent C ask: a stored _type=evaluation with critical constraints floors
    mismatch severity at material even when the default mapping says minor."""
    cid, _ = _mk_contract()
    STORE.put(
        {
            "_type": "evaluation",
            "id": "eval_1",
            "contract_id": cid,
            "intent_id": "int_1",
            "offer_id": "off_hero_01",
            "feasible": True,
            "hard_failures": [],
            "constraints": [
                {"key": "returns.window_days", "op": "gte", "value": 7, "critical": True},
            ],
        }
    )
    for k, v in [
        ("warranty.type", "manufacturer"),
        ("warranty.region", "IN"),
        ("product.region", "IN"),
        ("condition", "new"),
        ("price.amount_paise", 1149900),
        ("delivery.delivered_date", "2026-08-27T23:00:00+00:00"),  # +2h => minor by default
    ]:
        _fact(cid, k, v)

    res = evaluate_contract(cid)
    sla = [b for b in res["breaches"] if b.reason_code == "DELIVERY_SLA_MISS"]
    # Delivery is NOT in the critical-constraint map: stays minor.
    assert len(sla) == 1 and sla[0].severity == "minor"


def test_evaluation_floor_applies_to_constraint_backed_keys():
    """A key backed by a critical selection constraint (e.g. condition via the
    constraint map) escalates past minor on mismatch."""
    offer = _offer(
        terms={
            "warranty_type": "manufacturer",
            "warranty_duration_months": 12,
            "warranty_region": "IN",
            "condition": "new",
            "region": "IN",
            "return_window_days": 7,
        }
    )
    intent = {
        "id": "int_cond",
        "raw_text": "new condition only",
        "hard_constraints": [
            {"key": "condition", "op": "eq", "value": "new", "critical": True},
        ],
    }
    cid, _ = _mk_contract(offer=offer, intent=intent)
    STORE.put(
        {
            "_type": "evaluation",
            "id": "eval_2",
            "contract_id": cid,
            "intent_id": "int_cond",
            "offer_id": "off_hero_01",
            "feasible": True,
            "hard_failures": [],
            "constraints": [{"key": "condition", "op": "eq", "value": "new", "critical": True}],
        }
    )
    for k, v in [
        ("warranty.type", "manufacturer"),
        ("warranty.region", "IN"),
        ("product.region", "IN"),
        ("price.amount_paise", 1149900),
        ("delivery.delivered_date", "2026-08-27"),
    ]:
        _fact(cid, k, v)
    _fact(cid, "condition", "used")  # mismatch; already critical by default

    res = evaluate_contract(cid)
    cond = [b for b in res["breaches"] if b.reason_code == "CONDITION_MISMATCH"]
    assert len(cond) == 1 and cond[0].severity == "critical"

    # And a minor-mapped key WITH a selection floor escalates: price mismatch
    # against a contract whose evaluation had max_price as critical.
    cid2, _ = _mk_contract()
    STORE.put(
        {
            "_type": "evaluation",
            "id": "eval_3",
            "contract_id": cid2,
            "constraints": [
                {"key": "max_price_paise", "op": "lte", "value": 1200000, "critical": True},
            ],
            "hard_failures": [],
        }
    )
    for k, v in [
        ("warranty.type", "manufacturer"),
        ("warranty.region", "IN"),
        ("product.region", "IN"),
        ("condition", "new"),
        ("delivery.delivered_date", "2026-08-27"),
        ("price.amount_paise", 2500000),  # charged way above frozen price
    ]:
        _fact(cid2, k, v)
    res2 = evaluate_contract(cid2)
    price = [b for b in res2["breaches"] if STORE.get(b.promise_id)["key"] == "price.amount_paise"]
    assert len(price) == 1 and price[0].severity == "material"  # floored from minor


def test_sla_breach_exempt_from_severity_floor():
    """Agent C finding 2 policy decision: a critical delivery_deadline floors
    value mismatches but NOT DELIVERY_SLA_MISS — the <=24h-minor rule is the
    documented compensation policy and must survive constraint backing."""
    intent = {
        "id": "int_dl",
        "raw_text": "arrives by Thursday",
        "hard_constraints": [
            {"key": "delivery_deadline", "op": "lte", "value": "2026-08-27", "critical": True},
        ],
    }
    cid, _ = _mk_contract(intent=intent)
    STORE.put(
        {
            "_type": "evaluation",
            "id": "eval_dl",
            "contract_id": cid,
            "constraints": [
                {"key": "delivery_deadline", "op": "lte", "value": "2026-08-27", "critical": True},
            ],
            "hard_failures": [],
        }
    )
    for k, v in [
        ("warranty.type", "manufacturer"),
        ("warranty.region", "IN"),
        ("product.region", "IN"),
        ("condition", "new"),
        ("price.amount_paise", 1149900),
        ("delivery.delivered_date", "2026-08-27T23:00:00+00:00"),  # +2h late
    ]:
        _fact(cid, k, v)

    res = evaluate_contract(cid)
    sla = [b for b in res["breaches"] if b.reason_code == "DELIVERY_SLA_MISS"]
    assert len(sla) == 1
    assert sla[0].severity == "minor"  # NOT floored despite critical deadline

    # But beyond 24h the SLA breach is material on its own terms:
    cid2, _ = _mk_contract(
        intent=intent,
        offer=_offer(id="off_hero_dl2", sku="AST-HP-A17B"),  # distinct id/hash
    )
    STORE.put(
        {
            "_type": "evaluation",
            "id": "eval_dl2",
            "contract_id": cid2,
            "constraints": [
                {"key": "delivery_deadline", "op": "lte", "value": "2026-08-27", "critical": True},
            ],
            "hard_failures": [],
        }
    )
    for k, v in [
        ("warranty.type", "manufacturer"),
        ("warranty.region", "IN"),
        ("product.region", "IN"),
        ("condition", "new"),
        ("price.amount_paise", 1149900),
        ("delivery.delivered_date", "2026-08-30T21:00:00+00:00"),  # +72h
    ]:
        _fact(cid2, k, v)
    res2 = evaluate_contract(cid2)
    sla2 = [b for b in res2["breaches"] if b.reason_code == "DELIVERY_SLA_MISS"]
    assert len(sla2) == 1 and sla2[0].severity == "material"


# ---------------------------------------------------------------- transitions


def test_transition_from_verifying_and_idempotent_status():
    cid, _ = _mk_contract(status="VERIFYING")
    for k, v in [
        ("warranty.type", "manufacturer"),
        ("warranty.region", "IN"),
        ("product.region", "IN"),
        ("condition", "new"),
        ("price.amount_paise", 1149900),
        ("delivery.delivered_date", "2026-08-27"),
    ]:
        _fact(cid, k, v)
    res1 = evaluate_contract(cid)
    assert res1["status"] == "SATISFIED"
    res2 = evaluate_contract(cid)  # already SATISFIED (terminal): stays put
    assert res2["status"] == "SATISFIED"
    assert res2["status_target"] == "SATISFIED"


def test_unknown_contract_raises():
    import pytest

    with pytest.raises(LookupError):
        evaluate_contract("con_missing")


def test_unverified_text_claims_not_verified_against_facts():
    """Untrusted claims are not part of the material check — a contradicting
    fact about an unverified claim cannot create breaches."""
    cid, _ = _mk_contract()

    # Inject the unverified extra claim manually into the store.
    extra_pr = {
        "_type": "promise",
        "id": "pr_extra_unverified",
        "contract_id": cid,
        "key": "warranty.duration_months",
        "value": 3,
        "normalized_value": 3,
        "verification_status": "unverified",
        "material_to_intent": False,
    }
    STORE.put(extra_pr)

    for k, v in [
        ("warranty.type", "manufacturer"),
        ("warranty.region", "IN"),
        ("product.region", "IN"),
        ("condition", "new"),
        ("price.amount_paise", 1149900),
        ("delivery.delivered_date", "2026-08-27"),
    ]:
        _fact(cid, k, v)

    res = evaluate_contract(cid)
    assert res["satisfied"] is True  # unverified claim ignored entirely
