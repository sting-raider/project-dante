"""Buyer intent routes (Agent C) — compile, search+evaluate, select-offer.

Per docs/API_CONTRACT.md. select-offer refuses any offer whose stored
evaluation is infeasible: a hard-constraint violation can NEVER be selected.
Promise freezing uses Agent D's pipeline when present and falls back to a
minimal inline freeze so the demo works before D lands.
"""

from __future__ import annotations

import contextlib
import random
import re
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from project_dante.agents.compiler import get_compiler
from project_dante.agents.evaluator import get_evaluator
from project_dante.db.store import STORE
from project_dante.domain.events import append_event, now_iso
from project_dante.domain.hashing import sha256_hex
from project_dante.domain.state_machine import validate_transition
from project_dante.domain.types import DanteContract

router = APIRouter(prefix="/intents", tags=["intents"])


class CompileBody(BaseModel):
    raw_text: str


class SelectItemBody(BaseModel):
    item_id: str
    offer_id: str


class SelectOfferBody(BaseModel):
    # Legacy single-item callers continue to send {offer_id}.
    offer_id: str | None = None
    # Multi-item callers send every selected line as {item_id, offer_id}.
    items: list[SelectItemBody] = Field(default_factory=list)


# ---------------------------------------------------------------- helpers


def _intent_or_404(intent_id: str) -> dict[str, Any]:
    rec = STORE.get(intent_id)
    if not rec or rec.get("_type") != "intent":
        raise HTTPException(status_code=404, detail=f"intent {intent_id} not found")
    return rec


_MERCHANT_CATEGORIES = {
    "headphones": "headphones",
    "earbuds": "headphones",
    "router": "routers",
    "routers": "routers",
    "laptop": "laptops",
    "laptops": "laptops",
    "charger": "chargers-cables",
    "cable": "chargers-cables",
    "keyboard": "keyboards",
    "keyboards": "keyboards",
    "mouse": "mice",
    "mice": "mice",
    "monitor": "monitors",
    "monitors": "monitors",
    "phone": "phones",
    "phones": "phones",
    "desk": "desks",
    "desks": "desks",
    "chair": "chairs",
    "chairs": "chairs",
    "table": "tables",
    "tables": "tables",
    "cabinet": "cabinets",
    "cabinets": "cabinets",
    "shelf": "shelves",
    "shelves": "shelves",
    "lamp": "lamps",
    "lamps": "lamps",
    "sofa": "sofas",
    "sofas": "sofas",
}


def _item_intent(parent: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """Overlay one item envelope onto the legacy evaluator intent shape."""
    return {
        **parent,
        "hard_constraints": item.get("hard_constraints") or [],
        "soft_preferences": item.get("soft_preferences") or [],
        # The evaluator's existing absolute-cap check is per candidate.  For a
        # line item it must use that line's cap, never the combined order cap.
        "max_total_amount_paise": item.get("max_price_paise"),
        "quantity": item.get("quantity", 1),
        "_item_search": True,
        "_item_id": item.get("id"),
    }


async def _fetch_offers(
    intent: dict[str, Any], *, broad: bool = False
) -> tuple[list[dict[str, Any]], str]:
    """Merchant service first; STORE offers as fallback."""
    try:
        from project_dante.integrations.merchant.service import search_catalog

        filters = _merchant_filters(intent)
        results = search_catalog(
            # Item envelopes are already narrowed by category and line cap;
            # retrieving the full category lets the deterministic evaluator
            # enforce every structured feature without keyword false negatives.
            query=None if broad else _keyword_query(intent),
            category=filters.get("category"),
            max_price_paise=filters.get("max_price_paise"),
            limit=250 if broad else 25,
        )
        if results:
            return list(results), "merchant"
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 — merchant outage must not kill intent flow
        pass
    offers = [r for r in STORE.list("offer") if r.get("id")]
    filters = _merchant_filters(intent)
    if filters.get("category"):
        category = str(filters["category"])
        offers = [
            r
            for r in offers
            if str(r.get("category") or "").lower() == category.lower()
        ]
    if filters.get("max_price_paise") is not None:
        offers = [
            r
            for r in offers
            if isinstance(r.get("unit_amount_paise"), int)
            and r["unit_amount_paise"] <= filters["max_price_paise"]
        ]
    return offers, "store"


def _resolve_offer(offer_id: str) -> dict[str, Any] | None:
    """Look an offer up by id: merchant catalog first, then STORE records."""
    try:
        from project_dante.integrations.merchant.service import search_catalog

        # The catalog is small; a filtered scan resolves the exact offer.
        for cand in search_catalog(query=None, limit=500):
            cid = cand.get("id") or f"off_{cand.get('sku')}"
            if cid == offer_id or f"off_{cand.get('sku')}" == offer_id:
                return cand
    except ImportError:
        pass
    except KeyError:
        pass
    rec = STORE.get(offer_id)
    if rec and rec.get("_type") in ("offer", None):
        return rec
    return None


def _merchant_filters(intent: dict[str, Any]) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    for c in intent.get("hard_constraints") or []:
        key, value = c.get("key"), c.get("value")
        if key == "category":
            filters["category"] = _MERCHANT_CATEGORIES.get(str(value).lower(), value)
        elif key == "max_price_paise":
            filters["max_price_paise"] = value
    return filters


def _keyword_query(intent: dict[str, Any]) -> str:
    """Distill prose into catalog keywords (merchant search requires every
    token to match). Attributes/brands become tokens; constraint plumbing
    words are dropped."""
    parts: list[str] = []
    for c in intent.get("hard_constraints") or []:
        key, value = c.get("key"), c.get("value")
        if key == "attributes.form_factor" and isinstance(value, str):
            parts.append(value.replace("-", " "))
        elif key in ("variant.color",) and isinstance(value, str):
            parts.append(value)
    for p in intent.get("soft_preferences") or []:
        if p.get("key") == "brand" and isinstance(p.get("value"), str):
            parts.append(str(p["value"]))
    if not parts:
        # fall back to the raw text minus obvious non-catalog filler
        filler = re.compile(
            r"\b(buy|me|need|want|please|under|below|over|less|than|with|and|for|"
            r"the|a|an|must|they|i|do|not|spend|by|arrive|within|days?|tomorrow|"
            r"warranty|indian|manufacturer|seller)\b",
            re.IGNORECASE,
        )
        words = [w for w in filler.sub(" ", intent.get("raw_text", "")).split()
                 if len(w) > 2 and not any(ch.isdigit() for ch in w)]
        return " ".join(words[:6])
    return " ".join(parts)


def _soft_total(evaluation: dict[str, Any]) -> float:
    """Comparable deterministic score used only to rank already-feasible rows."""
    scores = evaluation.get("soft_scores") or []
    total = 0.0
    for score in scores:
        try:
            total += float(score.get("weight", 0)) * float(score.get("score", 0))
        except (AttributeError, TypeError, ValueError):
            continue
    return round(total, 6)


def _recommend_bundle(
    groups: list[dict[str, Any]], max_total_amount_paise: Any
) -> dict[str, Any]:
    """Pick one hard-feasible offer per line under the parent spend cap.

    This is intentionally deterministic.  The evaluator remains the authority
    for feasibility; this function only ranks rows that already passed.  A
    Pareto frontier keeps the search bounded as the number of requested lines
    grows: a state with both a higher spend and a lower score can never win a
    later combination.
    """
    feasible_groups: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for group in groups:
        feasible = [
            row
            for row in group.get("results", [])
            if (row.get("evaluation") or {}).get("feasible") is True
            and not (row.get("evaluation") or {}).get("hard_failures")
        ]
        if not feasible:
            return {
                "available": False,
                "engine": "deterministic",
                "offer_ids": {},
                "total_amount_paise": None,
                "score": None,
                "reason": (
                    f"No complete bundle: {group.get('label') or group.get('item_id')} "
                    "has no offer satisfying every hard constraint."
                ),
            }
        feasible_groups.append((group, feasible))

    cap = (
        max_total_amount_paise
        if isinstance(max_total_amount_paise, int)
        and not isinstance(max_total_amount_paise, bool)
        and max_total_amount_paise > 0
        else None
    )

    # A state stores only public identifiers and a score; offer payloads never
    # enter the recommendation metadata returned to the client.
    states: list[dict[str, Any]] = [
        {"total": 0, "score": 0.0, "offer_ids": {}}
    ]
    for group, candidates in feasible_groups:
        quantity = max(1, int(group.get("quantity") or 1))
        expanded: list[dict[str, Any]] = []
        for state in states:
            for row in candidates:
                offer = row.get("offer") or {}
                unit = offer.get("unit_amount_paise")
                if isinstance(unit, bool) or not isinstance(unit, int) or unit <= 0:
                    continue
                total = int(state["total"]) + unit * quantity
                if cap is not None and total > cap:
                    continue
                offer_ids = dict(state["offer_ids"])
                offer_ids[str(group["item_id"])] = str(offer.get("id"))
                expanded.append(
                    {
                        "total": total,
                        "score": round(
                            float(state["score"])
                            + _soft_total(row.get("evaluation") or {}),
                            6,
                        ),
                        "offer_ids": offer_ids,
                    }
                )

        # Deduplicate exact totals using score, then retain a Pareto frontier.
        best_by_total: dict[int, dict[str, Any]] = {}
        for state in expanded:
            total = int(state["total"])
            current = best_by_total.get(total)
            if current is None or (
                float(state["score"]),
                tuple(sorted(state["offer_ids"].items())),
            ) > (
                float(current["score"]),
                tuple(sorted(current["offer_ids"].items())),
            ):
                best_by_total[total] = state

        frontier: list[dict[str, Any]] = []
        best_score = float("-inf")
        for state in sorted(best_by_total.values(), key=lambda s: int(s["total"])):
            score = float(state["score"])
            if score > best_score:
                frontier.append(state)
                best_score = score
        # A pathological merchant catalog can expose thousands of distinct
        # prices.  Keep the best deterministic states while preserving both
        # the cheapest and highest-score endpoints.
        if len(frontier) > 2048:
            frontier = sorted(
                frontier,
                key=lambda s: (
                    -float(s["score"]),
                    int(s["total"]),
                    tuple(sorted(s["offer_ids"].items())),
                ),
            )[:2048]
            frontier.sort(key=lambda s: int(s["total"]))
        states = frontier

    if not states:
        return {
            "available": False,
            "engine": "deterministic",
            "offer_ids": {},
            "total_amount_paise": None,
            "score": None,
            "reason": "No combination of feasible line items fits the total budget.",
        }

    selected = min(
        states,
        key=lambda s: (
            -float(s["score"]),
            int(s["total"]),
            tuple(sorted(s["offer_ids"].items())),
        ),
    )
    return {
        "available": True,
        "engine": "deterministic",
        "offer_ids": selected["offer_ids"],
        "total_amount_paise": int(selected["total"]),
        "score": float(selected["score"]),
        "reason": (
            "Best hard-feasible combination under the buyer's total budget. "
            "You can edit each line before freezing."
        ),
    }


# ---------------------------------------------------------------- routes


@router.post("/compile")
async def compile_intent(body: CompileBody) -> dict[str, Any]:
    if not body.raw_text.strip():
        raise HTTPException(status_code=422, detail="raw_text must be non-empty")
    compiler = get_compiler()
    intent = await compiler.compile(body.raw_text)
    return {"intent": intent.model_dump(mode="json"), "engine": _engine_of(compiler.name)}


def _engine_of(agent_name: str) -> str:
    # The agent logs its own engine to STORE; read the latest run for it.
    runs = [r for r in STORE.list("agent_run") if r.get("agent_name") == agent_name]
    return runs[-1].get("engine", "rules") if runs else "rules"


@router.post("/{intent_id}/search")
async def search_offers(intent_id: str) -> dict[str, Any]:
    intent = _intent_or_404(intent_id)
    evaluator = get_evaluator()
    intent_items = intent.get("items") or []

    if intent_items:
        grouped: list[dict[str, Any]] = []
        flattened: list[dict[str, Any]] = []
        sources: list[str] = []
        candidate_count = 0
        for item in intent_items:
            item_intent = _item_intent(intent, item)
            offers, source = await _fetch_offers(item_intent, broad=True)
            sources.append(source)
            candidate_count += len(offers)
            evaluated = evaluator.evaluate(item_intent, offers)
            for i, result in enumerate(evaluated):
                STORE.put(
                    {
                        "id": f"{intent_id}_{item['id']}_eval_{i}",
                        "_type": "evaluation",
                        "intent_id": intent_id,
                        "item_id": item["id"],
                        "offer_id": result["offer"].get("id"),
                        "constraints": item.get("hard_constraints") or [],
                        **result["evaluation"],
                    }
                )
            enriched = await evaluator.enrich_explanations(item_intent, evaluated)
            item_results = [
                {
                    "item_id": item["id"],
                    "offer": result["offer"],
                    "evaluation": {
                        k: v
                        for k, v in result["evaluation"].items()
                        if k != "soft_total"
                    },
                }
                for result in enriched
            ]
            grouped.append(
                {
                    "item_id": item["id"],
                    "label": item.get("label") or item["id"],
                    "max_price_paise": item.get("max_price_paise"),
                    "quantity": item.get("quantity", 1),
                    "results": item_results,
                    "feasible_count": sum(
                        1 for result in item_results if result["evaluation"]["feasible"]
                    ),
                }
            )
            flattened.extend(item_results)

        recommendation = _recommend_bundle(
            grouped, intent.get("max_total_amount_paise")
        )
        recommended_ids = recommendation.get("offer_ids") or {}
        for group in grouped:
            group["recommended_offer_id"] = recommended_ids.get(group["item_id"])

        append_event(
            aggregate_type="intent",
            aggregate_id=intent_id,
            event_type="CATALOG_SEARCHED",
            payload={
                "source": "+".join(sorted(set(sources))) or "merchant",
                "candidates": candidate_count,
                "items": len(intent_items),
            },
            correlation_id=intent_id,
            trace_id=intent_id,
        )
        engine = "llm" if getattr(evaluator, "provider", None) is not None else "rules"
        return {
            "intent": intent,
            "items": grouped,
            "bundle_recommendation": recommendation,
            # Keep a flat projection for older clients and demo telemetry.
            "results": flattened,
            "engine": engine,
        }

    offers, source = await _fetch_offers(intent)
    results = evaluator.evaluate(intent, offers)

    for i, r in enumerate(results):
        summary = {
            "id": f"{intent_id}_eval_{i}",
            "_type": "evaluation",
            "intent_id": intent_id,
            "offer_id": r["offer"].get("id"),
            **r["evaluation"],
        }
        STORE.put(summary)

    append_event(
        aggregate_type="intent",
        aggregate_id=intent_id,
        event_type="CATALOG_SEARCHED",
        payload={"source": source, "candidates": len(offers)},
        correlation_id=intent_id,
        trace_id=intent_id,
    )

    enriched = await evaluator.enrich_explanations(intent, results)
    engine = "llm" if getattr(evaluator, "provider", None) is not None else "rules"
    return {
        "intent": intent,
        "results": [
            {"offer": r["offer"], "evaluation": {
                k: v for k, v in r["evaluation"].items() if k != "soft_total"
            }}
            for r in enriched
        ],
        "engine": engine,
    }


def _selection_requests(
    intent: dict[str, Any], body: SelectOfferBody
) -> list[tuple[dict[str, Any] | None, dict[str, str]]]:
    """Normalize legacy one-off selection and the multi-line request shape."""
    expected_items = intent.get("items") or []
    raw_items = getattr(body, "items", None) or []
    requests = [
        {
            "item_id": str(getattr(item, "item_id", "")),
            "offer_id": str(getattr(item, "offer_id", "")),
        }
        for item in raw_items
    ]
    if expected_items:
        expected_by_id = {
            str(item.get("id")): item
            for item in expected_items
            if isinstance(item, dict) and item.get("id")
        }
        request_ids = {item["item_id"] for item in requests}
        if len(requests) != len(request_ids) or request_ids != set(expected_by_id):
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "select one feasible offer for every requested item",
                    "expected_item_ids": sorted(expected_by_id),
                    "received_item_ids": sorted(request_ids),
                },
            )
        return [(expected_by_id[item["item_id"]], item) for item in requests]

    legacy_offer_id = getattr(body, "offer_id", None)
    if requests:
        if len(requests) != 1 or not requests[0]["offer_id"]:
            raise HTTPException(status_code=409, detail="single-item selection is malformed")
        return [(None, requests[0])]
    if isinstance(legacy_offer_id, str) and legacy_offer_id.strip():
        return [(None, {"item_id": "", "offer_id": legacy_offer_id})]
    raise HTTPException(status_code=422, detail="offer_id or items is required")


def _aggregate_promise_set_hash(promises: list[dict[str, Any]]) -> str:
    from project_dante.domain.hashing import canonical_json

    pairs = sorted(
        (
            str(p.get("key")),
            canonical_json(
                p.get("normalized_value")
                if p.get("normalized_value") is not None
                else p.get("value")
            ).decode(),
        )
        for p in promises
    )
    return sha256_hex([list(pair) for pair in pairs])


def _stable_offer(offer: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in offer.items()
        if key not in {"expires_at", "inventory", "_type"}
    }


@router.post("/{intent_id}/select-offer")
async def select_offer(intent_id: str, body: SelectOfferBody) -> dict[str, Any]:
    intent = _intent_or_404(intent_id)
    selections = _selection_requests(intent, body)
    resolved: list[dict[str, Any]] = []

    for item, request in selections:
        offer_id = request["offer_id"]
        offer = _resolve_offer(offer_id)
        if not offer:
            raise HTTPException(status_code=404, detail=f"offer {offer_id} not found")
        evaluations = [
            evaluation
            for evaluation in STORE.list("evaluation")
            if evaluation.get("intent_id") == intent_id
            and evaluation.get("offer_id") == offer_id
            and (
                item is None
                or evaluation.get("item_id") == item.get("id")
            )
        ]
        evaluation = evaluations[0] if evaluations else None
        if evaluation is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"offer {offer_id} has no stored evaluation for this intent/item "
                    "— run /search first"
                ),
            )
        failures = evaluation.get("hard_failures") or []
        if evaluation.get("feasible") is not True or failures:
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "offer violates hard constraints and cannot be selected",
                    "item_id": item.get("id") if item else None,
                    "hard_failures": failures,
                },
            )
        quantity = int(item.get("quantity", 1)) if item else 1
        unit_amount = offer.get("unit_amount_paise")
        if (
            isinstance(unit_amount, bool)
            or not isinstance(unit_amount, int)
            or unit_amount <= 0
            or quantity <= 0
        ):
            raise HTTPException(status_code=409, detail=f"offer {offer_id} has invalid price")
        inventory = offer.get("inventory")
        if quantity > 1 and (
            isinstance(inventory, bool)
            or not isinstance(inventory, (int, float))
            or inventory < quantity
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "selected line exceeds available inventory",
                    "item_id": item.get("id") if item else None,
                    "offer_id": offer_id,
                    "requested_quantity": quantity,
                    "available_inventory": inventory,
                },
            )
        resolved.append(
            {
                "item": item,
                "request": request,
                "offer": offer,
                "evaluation": evaluation,
                "quantity": quantity,
                "amount_paise": unit_amount * quantity,
            }
        )

    total_amount = sum(int(row["amount_paise"]) for row in resolved)
    max_total = intent.get("max_total_amount_paise")
    if isinstance(max_total, int) and total_amount > max_total:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "selected bundle exceeds the buyer total cap",
                "max_total_amount_paise": max_total,
                "selected_amount_paise": total_amount,
            },
        )

    contract_id = f"con_{random.randint(10**11, 10**12 - 1)}"  # 12-digit numeric id
    display_code = f"COV-{random.randint(1000, 9999)}"
    validate_transition("INTENT_READY", "OFFER_SELECTED")

    promises: list[dict[str, Any]] = []
    evidence_ids: list[str] = []
    frozen_rows: list[dict[str, Any]] = []
    frozen_via = "pipeline"
    bind_to_contract: Callable[..., int] | None = None
    freeze_promise_set: Callable[..., dict[str, Any]] | None = None
    try:
        from project_dante.domain.promises.pipeline import bind_to_contract, freeze_promise_set
    except ImportError:
        frozen_via = "inline-fallback"

    for row in resolved:
        item = row["item"]
        item_id = str(item.get("id")) if item else "single"
        line_item_id = f"li_{contract_id}_{re.sub(r'[^A-Za-z0-9_-]', '_', item_id)}"
        freeze_intent = _item_intent(intent, item) if item else intent
        frozen: dict[str, Any] = {}
        if freeze_promise_set is not None:
            try:
                frozen = freeze_promise_set(
                    row["offer"], freeze_intent, line_item_id=line_item_id
                )
            except Exception:  # noqa: BLE001 — freeze failure keeps selection safe
                frozen_via = "inline-fallback"
        if not frozen:
            frozen = {
                "offer_hash": sha256_hex(_stable_offer(row["offer"])),
                "promise_set_hash": sha256_hex(
                    {"offer": row["offer"], "intent": freeze_intent}
                ),
                "promises": [],
                "evidence_ids": [],
            }
        row["line_item_id"] = line_item_id
        row["frozen"] = frozen
        row["offer_hash"] = frozen.get("offer_hash") or sha256_hex(
            _stable_offer(row["offer"])
        )
        row_promises = [
            promise for promise in frozen.get("promises", []) if isinstance(promise, dict)
        ]
        for promise in row_promises:
            promise.setdefault("line_item_id", line_item_id)
        row["promise_ids"] = [
            promise["id"]
            for promise in row_promises
            if isinstance(promise.get("id"), str)
        ]
        promises.extend(row_promises)
        raw_evidence_ids = frozen.get("evidence_ids", [])
        if isinstance(raw_evidence_ids, list):
            evidence_ids.extend(
                evidence_id
                for evidence_id in raw_evidence_ids
                if isinstance(evidence_id, str)
            )
        frozen_rows.append(row)

        evaluation = row["evaluation"]
        constraints = evaluation.get("constraints")
        if not isinstance(constraints, list) or not constraints:
            raw_constraints = (
                item.get("hard_constraints") if item else intent.get("hard_constraints")
            )
            if not isinstance(raw_constraints, list):
                raw_constraints = []
            constraints = [
                constraint
                for constraint in raw_constraints
                if isinstance(constraint, dict) and constraint.get("critical", True)
            ]
        STORE.update(
            evaluation["id"],
            contract_id=contract_id,
            constraints=constraints,
        )

    line_items = [
        {
            "id": row["line_item_id"],
            "intent_item_id": row["item"].get("id") if row["item"] else None,
            "offer_id": row["offer"]["id"],
            "sku": row["offer"].get("sku") or row["offer"]["id"],
            "title": row["offer"].get("title") or row["offer"]["id"],
            "quantity": row["quantity"],
            "unit_amount_paise": row["offer"]["unit_amount_paise"],
            "amount_paise": row["amount_paise"],
            "offer_hash": row["offer_hash"],
            "promise_ids": row["promise_ids"],
        }
        for row in frozen_rows
    ]
    if len(frozen_rows) == 1:
        offer_hash = frozen_rows[0]["offer_hash"]
        promise_set_hash = frozen_rows[0]["frozen"].get("promise_set_hash")
        if not promise_set_hash:
            promise_set_hash = _aggregate_promise_set_hash(promises)
    else:
        offer_hash = sha256_hex([row["offer_hash"] for row in frozen_rows])
        promise_set_hash = _aggregate_promise_set_hash(promises)

    # Preserve the gateway posture on the frozen contract so the UI can expose
    # the correct next step after refresh.  Selection itself makes no money
    # call; if settings cannot be read, the safe default is sandbox and the
    # payment-order route will still re-evaluate the gateway independently.
    try:
        from project_dante.integrations.razorpay import service as razorpay_service

        sandbox_mode = razorpay_service.mode() == "sandbox"
    except Exception:  # noqa: BLE001 — inability to read posture fails safe
        sandbox_mode = True

    contract = DanteContract(
        id=contract_id,
        display_code=display_code,
        intent_id=intent_id,
        offer_id=frozen_rows[0]["offer"]["id"],
        line_items=line_items,
        offer_hash=offer_hash,
        promise_set_hash=promise_set_hash,
        amount_paise=total_amount,
        status="OFFER_SELECTED",
        created_at=now_iso(),
        sandbox_mode=sandbox_mode,
    )
    record = contract.model_dump(mode="json")
    record["_type"] = "contract"
    if offer_hash and promise_set_hash:
        from project_dante.domain.promises.pipeline import compute_contract_hash

        record["contract_hash"] = compute_contract_hash(offer_hash, promise_set_hash)
    record["promise_ids"] = [
        promise["id"] for promise in promises if isinstance(promise.get("id"), str)
    ]
    record["status"] = "CONTRACT_FROZEN"
    record["frozen_at"] = now_iso()
    validate_transition("OFFER_SELECTED", "CONTRACT_FROZEN")
    STORE.put(record)

    # Bind every successfully frozen row even if a later row had to use the
    # safe inline fallback.  Skipping the bind for the whole bundle would
    # orphan the earlier line's promises/evidence from verifier lookups.
    if bind_to_contract is not None and (promises or evidence_ids):
        with contextlib.suppress(Exception):
            bind_to_contract(
                contract_id,
                promise_ids=[
                    promise["id"]
                    for promise in promises
                    if isinstance(promise.get("id"), str)
                ],
                evidence_ids=list(dict.fromkeys(evidence_ids)),
            )

    offer_ids = [row["offer"]["id"] for row in frozen_rows]
    append_event(
        aggregate_type="contract",
        aggregate_id=contract_id,
        event_type="OFFER_SELECTED",
        payload={
            "offer_id": offer_ids[0],
            "offer_ids": offer_ids,
            "amount_paise": total_amount,
        },
        correlation_id=intent_id,
        trace_id=intent_id,
    )
    append_event(
        aggregate_type="contract",
        aggregate_id=contract_id,
        event_type="CONTRACT_CREATED",
        payload={"display_code": display_code, "line_items": len(line_items)},
        correlation_id=intent_id,
        trace_id=intent_id,
    )

    evidence: list[dict[str, Any]] = []
    for eid in dict.fromkeys(evidence_ids):
        rec = STORE.get(eid)
        if rec:
            evidence.append(rec)

    return {
        "contract": record,
        "promises": promises,
        "evidence": evidence,
        "_freeze_via": frozen_via,
    }
