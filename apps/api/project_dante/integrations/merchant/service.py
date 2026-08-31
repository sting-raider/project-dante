"""Merchant interface service — Aster Electronics (plan sections 7, 17, 26).

This is the frozen cross-agent import surface (docs/API_CONTRACT.md):

    search_catalog(query, category, max_price_paise, limit) -> list[dict]
    get_product(sku)                                        -> dict | None
    check_inventory(sku)                                    -> int
    freeze_offer(offer_id)                                  -> dict
    apply_fulfillment_event(contract_id, kind, scenario)    -> dict
    seed_catalog()                                          -> int

All fulfillment events and observed facts produced here are SYNTHETIC — every
record carries ``synthetic: true`` plus a ``scenario_id`` (invariant 17).
Fulfillment facts are stored in STORE as ``_type: fact`` records; events are
appended to the domain LOG for audit.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from project_dante.db.store import STORE
from project_dante.domain.events import append_event, new_id, now_iso
from project_dante.domain.hashing import sha256_hex
from project_dante.integrations.merchant.catalog_loader import load_catalog

MERCHANT_ID = "aster-electronics"

# ------------------------------------------------------------------ search


def _tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def _score_product(product: dict, tokens: list[str]) -> int:
    if not tokens:
        return 1
    score = 0
    title = product["title"].lower()
    brand = (product.get("brand") or "").lower()
    category = (product.get("category") or "").lower()
    attrs = " ".join(str(v) for v in (product.get("attributes") or {}).values()).lower()
    terms = str((product.get("terms") or {}).get("warranty_type") or "").lower()

    for tok in tokens:
        hit = False
        if tok in title:
            score += 4
            hit = True
        if tok == brand:
            score += 3
            hit = True
        elif tok in brand:
            score += 1
            hit = True
        if tok in category:
            score += 2
            hit = True
        if tok in attrs:
            score += 1
            hit = True
        if tok in terms:
            score += 1
            hit = True
        if not hit:
            # every token must appear somewhere; otherwise treat as no match
            return 0
    return score


def search_catalog(
    query: str | None = None,
    category: str | None = None,
    max_price_paise: int | None = None,
    limit: int = 50,
) -> list[dict]:
    """Keyword search over the Aster catalog.

    Scoring: token matches on title (x4), category (x2), brand (x3 exact),
    attributes/terms (x1). Requires every query token to match somewhere.
    Sorted by score desc, then unit_amount_paise asc.
    """
    catalog = load_catalog()
    tokens = _tokenize(query or "")
    cat_norm = category.strip().lower() if category else None

    scored: list[tuple[int, int, int]] = []
    for idx, product in enumerate(catalog):
        if cat_norm and product.get("category", "").lower() != cat_norm:
            continue
        if max_price_paise is not None and product["unit_amount_paise"] > max_price_paise:
            continue
        score = _score_product(product, tokens)
        if score <= 0:
            continue
        scored.append((-score, product["unit_amount_paise"], idx))

    scored.sort(key=lambda t: (t[0], t[1], t[2]))
    return [deepcopy(catalog[i]) for _, _, i in scored[: max(0, limit)]]


def get_product(sku: str) -> dict | None:
    """Product listing + its offer envelope(s)."""
    catalog = load_catalog()
    matches = [p for p in catalog if p["sku"] == sku]
    if not matches:
        return None
    product = deepcopy(matches[0])
    return {
        "product": product,
        "offers": [
            {
                "offer_id": f"off_{product['sku']}",
                "sku": product["sku"],
                "unit_amount_paise": product["unit_amount_paise"],
                "currency": product["currency"],
                "inventory": product["inventory"],
                "delivery_promise": deepcopy(product["delivery_promise"]),
                "terms": deepcopy(product["terms"]),
            }
        ],
    }


def check_inventory(sku: str) -> int:
    for product in load_catalog():
        if product["sku"] == sku:
            return int(product["inventory"])
    return 0


# ------------------------------------------------------------------ seeding


def seed_catalog() -> int:
    """Load fixture into STORE as `_type: offer` records (idempotent)."""
    count = 0
    for product in load_catalog():
        existing = STORE.get(f"off_{product['sku']}")
        if existing is not None:
            continue
        record = dict(product)
        record["_type"] = "offer"
        STORE.put(record)
        count += 1
    append_event(
        aggregate_type="catalog",
        aggregate_id=MERCHANT_ID,
        event_type="CATALOG_SEARCHED",
        payload={"seeded": True, "count": count},
        synthetic=False,
    )
    return count


def catalog_size() -> int:
    return len(load_catalog())


# ------------------------------------------------------------------ freeze


_WARRANTY_SENTENCE = {
    "manufacturer": "{months}-month official manufacturer warranty valid in {region}",
    "seller": "{months}-month seller warranty provided by Aster Electronics",
    "none": "no warranty included with this listing",
    "unknown": "warranty details not specified by the seller",
}

_REGION_NAMES = {"IN": "India", "AE": "the UAE", "US": "the United States"}


def _region_name(code: str | None) -> str:
    if not code:
        return "an unspecified region"
    return _REGION_NAMES.get(code.upper(), code.upper())


def render_listing_text(offer: dict) -> str:
    """Human-readable paragraph of what the buyer was promised (untrusted data)."""
    terms = offer.get("terms") or {}
    dp = offer.get("delivery_promise") or {}
    price_rs = offer["unit_amount_paise"] / 100

    wtype = terms.get("warranty_type") or "unknown"
    months = terms.get("warranty_duration_months")
    region_name = _region_name(terms.get("warranty_region"))
    sentence = _WARRANTY_SENTENCE[wtype]
    warranty_sentence = sentence.format(months=months, region=region_name)
    warranty_sentence += f". Warranty region: {region_name}."

    ret = terms.get("return_window_days")
    if isinstance(ret, int):
        return_sentence = f"{ret}-day returns from delivery date."
    else:
        return_sentence = "Return policy not specified."

    repl = terms.get("replacement_window_days")
    if isinstance(repl, int):
        replacement_sentence = f"{repl}-day replacement window for defective units."
    else:
        replacement_sentence = "Replacement policy not specified."

    dmin, dmax = dp.get("min_days"), dp.get("max_days")
    if dmin and dmax:
        delivery_sentence = (
            f"Delivery in {dmin}-{dmax} days via {dp.get('service') or 'standard shipping'}."
        )
    else:
        delivery_sentence = "Delivery timeline not specified."

    condition = terms.get("condition") or "new"
    return (
        f"{offer['title']} ({offer['sku']}) sold by Aster Electronics at "
        f"Rs {price_rs:,.2f}. Condition: {condition}. "
        f"{warranty_sentence} {return_sentence} {replacement_sentence} "
        f"{delivery_sentence}"
    )


def freeze_offer(offer_id: str) -> dict:
    """Snapshot an offer for a Dante contract.

    Returns {"offer", "evidence_payload", "rendered_text"}:
      - offer: full offer dict copy with inventory snapshot at freeze time
      - evidence_payload: raw structured JSON snapshot (hashed downstream)
      - rendered_text: human-readable listing paragraph
    """
    catalog = load_catalog()
    expected_offer_id = offer_id if offer_id.startswith("off_") else f"off_{offer_id}"
    sku = expected_offer_id.removeprefix("off_")

    offer = next((p for p in catalog if p["sku"] == sku), None)
    if offer is None or offer_id not in (f"off_{offer['sku']}",):
        # also allow direct lookup when ids diverge from off_<sku> convention
        offer = next((p for p in catalog if f"off_{p['sku']}" == expected_offer_id), None)
    if offer is None:
        raise KeyError(f"Unknown offer_id: {offer_id}")

    snapshot = deepcopy(offer)
    snapshot["inventory_snapshot"] = snapshot["inventory"]
    snapshot["frozen_at"] = now_iso()
    snapshot["snapshot_hash"] = sha256_hex(snapshot)

    evidence_payload = {
        "source_type": "checkout_offer",
        "raw_payload_ref": f"fixtures/catalog/aster_catalog.json#{offer['sku']}",
        "captured_at": snapshot["frozen_at"],
        "payload": deepcopy(snapshot),
    }

    append_event(
        aggregate_type="catalog",
        aggregate_id=offer["sku"],
        event_type="EVIDENCE_SNAPSHOT_CREATED",
        payload={"offer_id": offer_id, "snapshot_hash": snapshot["snapshot_hash"]},
    )

    return {
        "offer": snapshot,
        "evidence_payload": evidence_payload,
        "rendered_text": render_listing_text(offer),
    }


# ------------------------------------------------------------------ fulfillment sim


def _fact(
    contract_id: str,
    key: str,
    value: Any,
    source_artifact_id: str,
    scenario_id: str | None,
    line_item_id: str | None = None,
) -> dict:
    fact = {
        "id": new_id("obs"),
        "_type": "fact",
        "contract_id": contract_id,
        "key": key,
        "value": value,
        "source_artifact_id": source_artifact_id,
        "observed_at": now_iso(),
        "synthetic": True,
        "scenario_id": scenario_id,
    }
    if line_item_id is not None:
        fact["line_item_id"] = line_item_id
    return fact


def _contract_promises(contract_id: str) -> list[dict]:
    return [p for p in STORE.list("promise") if p.get("contract_id") == contract_id]


def _promise_value(
    promises: list[dict],
    key: str,
    default: Any = None,
    line_item_id: str | None = None,
) -> Any:
    fallback: Any = default
    for promise in promises:
        if promise.get("key") == key:
            if line_item_id is not None:
                if promise.get("line_item_id") == line_item_id:
                    return promise.get("value", default)
                continue
            if promise.get("line_item_id") is None:
                return promise.get("value", default)
            fallback = promise.get("value", fallback)
    return fallback


def _store_fact(fact: dict) -> dict:
    STORE.put(fact)
    append_event(
        aggregate_type="contract",
        aggregate_id=fact["contract_id"],
        event_type="OBSERVED_FACT_RECORDED",
        payload={
            "key": fact["key"],
            "value": fact["value"],
            "observed_fact_id": fact["id"],
            **(
                {"line_item_id": fact["line_item_id"]}
                if fact.get("line_item_id") is not None
                else {}
            ),
            "synthetic": True,
        },
        synthetic=True,
        scenario_id=fact.get("scenario_id"),
    )
    return fact


def _materialize_evidence(
    contract_id: str,
    source_type: str,
    artifact_id: str,
    scenario_id: str,
    payload: dict,
    line_item_id: str | None = None,
) -> None:
    """Create the REAL evidence artifact the facts/events reference.

    Rights eligibility matches on evidence.source_type, so synthetic
    shipment/delivery observations must be materialized as evidence records
    (trusted_level=synthetic), not just referenced by id.
    """
    try:
        from project_dante.domain.promises.pipeline import build_evidence

        rec = build_evidence(
            source_type=source_type,
            payload=payload,
            trusted_level="synthetic",
            synthetic=True,
            scenario_id=scenario_id,
            contract_id=contract_id,
            line_item_id=line_item_id,
        )
        rec["id"] = artifact_id
        STORE.put(rec)
    except Exception:  # noqa: BLE001 - demo sim must not crash on pipeline drift
        STORE.put(
            {
                "id": artifact_id,
                "_type": "evidence",
                "contract_id": contract_id,
                "line_item_id": line_item_id,
                "source_type": source_type,
                "raw_payload_ref": f"store://{artifact_id}",
                "sha256": "",
                "observed_at": now_iso(),
                "trusted_level": "synthetic",
                "synthetic": True,
                "scenario_id": scenario_id,
                "payload": payload,
            }
        )


def apply_fulfillment_event(
    contract_id: str,
    kind: str,
    scenario: str | None = None,
    line_item_id: str | None = None,
) -> dict:
    """Inject a synthetic fulfillment observation for a demo contract.

    kind: "ship" | "deliver" | "replacement_check".
    Facts are written to STORE (_type fact) and mirrored into the audit LOG;
    every record carries synthetic=true and a scenario_id.
    """
    scenario_id = f"scenario_{scenario}" if scenario else "scenario_manual"

    contract = STORE.get(contract_id) or {}
    lines = contract.get("line_items") or []
    line_ids = [
        str(line.get("id"))
        for line in lines
        if isinstance(line, dict) and line.get("id")
    ]
    if line_item_id is not None and line_item_id not in line_ids:
        raise ValueError(f"Unknown contract line item: {line_item_id}")
    target_line_ids: list[str | None] = []
    if line_item_id is not None:
        target_line_ids.append(line_item_id)
    elif line_ids:
        # A demo event without a line selector applies to every frozen line;
        # the facts are still written separately so verification cannot leak
        # one line's outcome into another.
        target_line_ids.extend(line_ids)
    else:
        target_line_ids.append(None)

    if kind == "ship":
        artifact_id = new_id("ev")
        _materialize_evidence(
            contract_id,
            "shipment_event",
            artifact_id,
            scenario_id,
            {"carrier": "SynthEx", "status": "shipped", "synthetic": True},
            line_item_id=line_item_id,
        )
        append_event(
            aggregate_type="contract",
            aggregate_id=contract_id,
            event_type="FULFILLMENT_SHIPPED",
            payload={
                "carrier": "SynthEx",
                "tracking_id": f"SYNTH-{new_id('trk').upper()}",
                "evidence_artifact_id": artifact_id,
                "synthetic": True,
            },
            synthetic=True,
            scenario_id=scenario_id,
        )
        shipment_facts = [
            _fact(
                contract_id,
                "shipment.status",
                "shipped",
                artifact_id,
                scenario_id,
                line_item_id,
            ),
            _fact(
                contract_id,
                "shipment.carrier",
                "SynthEx",
                artifact_id,
                scenario_id,
                line_item_id,
            ),
        ]
        return {
            "kind": "ship",
            "scenario": scenario,
            "facts": [_store_fact(f) for f in shipment_facts],
        }

    if kind == "replacement_check":
        available = scenario != "unavailable"
        artifact_id = new_id("ev")
        _materialize_evidence(
            contract_id,
            "merchant_api",
            artifact_id,
            scenario_id,
            {"query": "replacement.inventory", "available": available, "synthetic": True},
            line_item_id=line_item_id,
        )
        # Replacement availability is a line-level fact.  When the operator
        # checks the whole basket, emit one fact per frozen line so the
        # rights engine can unlock only the affected line's fallback without
        # leaking the result across the basket.  Legacy single-item
        # contracts still use the unscoped None target.
        replacement_facts = [
            _fact(
                contract_id,
                "replacement.available",
                available,
                artifact_id,
                scenario_id,
                target_id,
            )
            for target_id in target_line_ids
        ]
        return {
            "kind": "replacement_check",
            "scenario": scenario,
            "facts": [_store_fact(f) for f in replacement_facts],
        }

    if kind != "deliver":
        raise ValueError(f"Unsupported fulfillment kind: {kind}")

    artifact_id = new_id("ev")
    _materialize_evidence(
        contract_id,
        "delivery_event",
        artifact_id,
        scenario_id,
        {
            "scenario": scenario or "correct",
            "carrier": "SynthEx",
            "delay_days": 3 if scenario == "late" else 0,
            "synthetic": True,
        },
        line_item_id=line_item_id if len(target_line_ids) == 1 else None,
    )
    append_event(
        aggregate_type="contract",
        aggregate_id=contract_id,
        event_type="FULFILLMENT_DELIVERED",
        payload={
            "scenario": scenario or "correct",
            "carrier": "SynthEx",
            "evidence_artifact_id": artifact_id,
            "delay_days": 3 if scenario == "late" else 0,
            "synthetic": True,
        },
        synthetic=True,
        scenario_id=scenario_id,
    )

    today = datetime.now(UTC).date()
    base_delivery = today - timedelta(days=2)  # plausible transit time before "today"
    promises = _contract_promises(contract_id)
    line_by_id = {
        str(line.get("id")): line
        for line in lines
        if isinstance(line, dict) and line.get("id")
    }
    facts: list[dict] = []
    for target_id in target_line_ids:
        promised_warranty = _promise_value(
            promises, "warranty.type", "unknown", target_id
        )
        promised_region = _promise_value(promises, "product.region", "IN", target_id)
        promised_warranty_region = _promise_value(
            promises, "warranty.region", "IN", target_id
        )
        promised_condition = _promise_value(promises, "condition", "new", target_id)
        promised_by_date = _promise_value(
            promises, "delivery.promised_by_date", None, target_id
        )

        delivered_warranty = promised_warranty
        delivered_region = promised_region
        delivered_warranty_region = promised_warranty_region
        if scenario == "wrong_variant":
            delivered_warranty = "seller"
            delivered_region = "AE"
            delivered_warranty_region = "AE"

        if promised_by_date and scenario == "late":
            try:
                promised_date = datetime.fromisoformat(str(promised_by_date)[:10]).date()
                delivered_date = promised_date + timedelta(days=3)
            except ValueError:
                delivered_date = today + timedelta(days=3)
        else:
            delivered_date = base_delivery

        paid_amount = None
        if target_id is not None:
            paid_amount = (line_by_id.get(target_id) or {}).get("unit_amount_paise")
        if paid_amount is None:
            paid_amount = contract.get("amount_paise")
        if paid_amount is None:
            paid_amount = _promise_value(
                promises, "price.amount_paise", None, target_id
            )

        facts.extend(
            [
                _fact(
                    contract_id,
                    "warranty.type",
                    delivered_warranty,
                    artifact_id,
                    scenario_id,
                    target_id,
                ),
                _fact(
                    contract_id,
                    "warranty.region",
                    delivered_warranty_region,
                    artifact_id,
                    scenario_id,
                    target_id,
                ),
                _fact(
                    contract_id,
                    "product.region",
                    delivered_region,
                    artifact_id,
                    scenario_id,
                    target_id,
                ),
                _fact(
                    contract_id,
                    "condition",
                    promised_condition,
                    artifact_id,
                    scenario_id,
                    target_id,
                ),
                _fact(
                    contract_id,
                    "price.amount_paise",
                    paid_amount,
                    artifact_id,
                    scenario_id,
                    target_id,
                ),
                _fact(
                    contract_id,
                    "delivery.delivered_date",
                    delivered_date.isoformat(),
                    artifact_id,
                    scenario_id,
                    target_id,
                ),
            ]
        )
        if scenario == "late":
            facts.append(
                _fact(
                    contract_id,
                    "delivery.days_late",
                    3,
                    artifact_id,
                    scenario_id,
                    target_id,
                )
            )

    result: dict[str, Any] = {
        "kind": "deliver",
        "scenario": scenario or "correct",
        "facts": [_store_fact(f) for f in facts],
    }
    return result


# ------------------------------------------------------------------ analytics helpers


def catalog_analytics_base() -> dict:
    """Deterministic per-catalog metadata stats (honest simple math).

    Computed straight from the fixture; no invented numbers.
    """
    catalog = load_catalog()
    total = len(catalog)
    with_warranty_meta = sum(
        1 for p in catalog if (p.get("terms") or {}).get("warranty_type") not in (None, "unknown")
    )
    with_return_policy = sum(
        1 for p in catalog if isinstance((p.get("terms") or {}).get("return_window_days"), int)
    )
    return {
        "total_products": total,
        "warranty_metadata_coverage": round(with_warranty_meta / total, 4) if total else 0.0,
        "machine_readable_return_policy": round(with_return_policy / total, 4) if total else 0.0,
    }
