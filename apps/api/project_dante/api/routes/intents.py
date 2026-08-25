"""Buyer intent routes (Agent C) — compile, search+evaluate, select-offer.

Per docs/API_CONTRACT.md. select-offer refuses any offer whose stored
evaluation is infeasible: a hard-constraint violation can NEVER be selected.
Promise freezing uses Agent D's pipeline when present and falls back to a
minimal inline freeze so the demo works before D lands.
"""

from __future__ import annotations

import random
import re
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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


class SelectOfferBody(BaseModel):
    offer_id: str


# ---------------------------------------------------------------- helpers


def _intent_or_404(intent_id: str) -> dict[str, Any]:
    rec = STORE.get(intent_id)
    if not rec or rec.get("_type") != "intent":
        raise HTTPException(status_code=404, detail=f"intent {intent_id} not found")
    return rec


async def _fetch_offers(intent: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Merchant service first; STORE offers as fallback."""
    try:
        from project_dante.integrations.merchant.service import search_catalog

        filters = _merchant_filters(intent)
        results = search_catalog(
            query=_keyword_query(intent),
            category=filters.get("category"),
            max_price_paise=filters.get("max_price_paise"),
            limit=25,
        )
        if results:
            return list(results), "merchant"
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 — merchant outage must not kill intent flow
        pass
    offers = [r for r in STORE.list("offer") if r.get("id")]
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
            filters["category"] = value
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


@router.post("/{intent_id}/select-offer")
async def select_offer(intent_id: str, body: SelectOfferBody) -> dict[str, Any]:
    intent = _intent_or_404(intent_id)
    offer = _resolve_offer(body.offer_id)
    if not offer:
        raise HTTPException(status_code=404, detail=f"offer {body.offer_id} not found")

    evaluation = STORE.find_one("evaluation", intent_id=intent_id, offer_id=body.offer_id)
    if evaluation is None:
        raise HTTPException(
            status_code=409,
            detail="offer has no stored evaluation for this intent — run /search first",
        )
    failures = evaluation.get("hard_failures") or []
    if evaluation.get("feasible") is not True or failures:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "offer violates hard constraints and cannot be selected",
                "hard_failures": failures,
            },
        )

    contract_id = f"con_{random.randint(10**11, 10**12 - 1)}"  # 12-digit numeric id
    display_code = f"COV-{random.randint(1000, 9999)}"

    # Stamp the selection-time constraint snapshot onto the evaluation record so
    # Agent D's verifier can floor severity on critical-constraint mismatches
    # (their _evaluation_floor matches by contract_id).
    evaluation["contract_id"] = contract_id
    if not evaluation.get("constraints"):
        evaluation["constraints"] = [
            c for c in (intent.get("hard_constraints") or []) if c.get("critical", True)
        ]
    STORE.update(evaluation["id"], **{
        "contract_id": contract_id,
        "constraints": evaluation["constraints"],
    })

    contract = DanteContract(
        id=contract_id,
        display_code=display_code,
        intent_id=intent_id,
        offer_id=body.offer_id,
        offer_hash=sha256_hex(offer),
        amount_paise=offer.get("unit_amount_paise"),
        status="OFFER_SELECTED",
        created_at=now_iso(),
        sandbox_mode=True,
    )
    validate_transition("INTENT_READY", "OFFER_SELECTED")

    promises: list[dict[str, Any]] = []
    evidence_ids: list[dict[str, Any]] = []
    promise_set_hash: str | None = None
    frozen_via = "inline-fallback"

    try:
        from project_dante.domain.promises.pipeline import freeze_promise_set

        frozen = freeze_promise_set(offer, intent)
        promises = frozen.get("promises", [])
        evidence_ids = frozen.get("evidence_ids", [])
        promise_set_hash = frozen.get("promise_set_hash")
        frozen_via = "pipeline"
    except ImportError:
        # Minimal inline freeze: hash the offer snapshot; promise extraction
        # lands with Agent D's route wiring (see docs/handoffs/agents.md).
        promise_set_hash = sha256_hex({"offer": offer, "intent": intent})
        promises = []
    except Exception:  # noqa: BLE001 — freeze failure must not lose the selection
        promise_set_hash = sha256_hex({"offer": offer, "intent": intent})

    record = contract.model_dump()
    record["_type"] = "contract"
    record["promise_set_hash"] = promise_set_hash
    record["promise_ids"] = [p.get("id") for p in promises if isinstance(p, dict)]
    record["status"] = "CONTRACT_FROZEN"
    record["frozen_at"] = now_iso()
    validate_transition("OFFER_SELECTED", "CONTRACT_FROZEN")
    STORE.put(record)

    append_event(
        aggregate_type="contract",
        aggregate_id=contract_id,
        event_type="OFFER_SELECTED",
        payload={"offer_id": body.offer_id, "amount_paise": contract.amount_paise},
        correlation_id=intent_id,
        trace_id=intent_id,
    )
    append_event(
        aggregate_type="contract",
        aggregate_id=contract_id,
        event_type="CONTRACT_CREATED",
        payload={"display_code": display_code},
        correlation_id=intent_id,
        trace_id=intent_id,
    )

    evidence = []
    for eid in evidence_ids:
        rec = STORE.get(eid) if isinstance(eid, str) else None
        if rec:
            evidence.append(rec)

    return {
        "contract": record,
        "promises": promises,
        "evidence": evidence,
        "_freeze_via": frozen_via,
    }
