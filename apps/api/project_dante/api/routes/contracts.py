"""Contract routes (docs/API_CONTRACT.md — Contracts section, Agent D).

GET /api/contracts/{id}            -> {contract, promises, entitlements}
POST /api/contracts/{id}/authorize -> {contract}
GET  /api/contracts/{id}/timeline ?category= -> {events}
POST /api/contracts/{id}/verify    -> {breaches, status, satisfied}
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from project_dante.db.store import STORE
from project_dante.domain.events import LOG, append_event
from project_dante.domain.promises.pipeline import compute_contract_hash
from project_dante.domain.promises.verifier import evaluate_contract
from project_dante.domain.state_machine import validate_transition
from project_dante.domain.types import AuthorityEnvelope, DanteContract

router = APIRouter(prefix="/contracts", tags=["contracts"])

# Categories accepted by the timeline filter (plan §28 timeline columns).
_TIMELINE_CATEGORIES = {"Agent", "Money", "Merchant", "Fulfillment", "Policy", "Evidence", "System"}


def _get_contract_or_404(contract_id: str) -> dict[str, Any]:
    contract = STORE.get(contract_id)
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract {contract_id} not found")
    return contract


def _to_model(model_cls: type, record: dict[str, Any]) -> dict[str, Any]:
    """Rehydrate a stored record into a frozen model, dropping store-internal
    (`_type`) and non-schema fields the forbid-extras models would reject."""
    fields = set(model_cls.model_fields)
    return model_cls.model_validate(
        {k: v for k, v in record.items() if k in fields}
    ).model_dump()


@router.get("/{contract_id}")
async def get_contract(contract_id: str) -> dict[str, Any]:
    """Contract dossier: frozen promise set + any rights created against it."""
    contract = _get_contract_or_404(contract_id)
    promises = [p for p in STORE.list("promise") if p.get("contract_id") == contract_id]
    entitlements = [e for e in STORE.list("entitlement") if e.get("contract_id") == contract_id]
    return {
        "contract": _to_model(DanteContract, contract),
        "promises": promises,
        "entitlements": entitlements,
    }


@router.post("/{contract_id}/authorize")
async def authorize_contract(contract_id: str) -> dict[str, Any]:
    """Bind buyer authorization to the exact frozen transaction.

    The contract hash is RECOMPUTED NOW from the stored offer/promise-set
    hashes; the envelope records it so later drift (§33.3) invalidates the
    authorization. The amount must equal the frozen structured price.
    """
    contract = _get_contract_or_404(contract_id)

    if not contract.get("offer_hash") or not contract.get("promise_set_hash"):
        raise HTTPException(
            status_code=409,
            detail="Contract is missing offer_hash/promise_set_hash; re-freeze before authorizing",
        )

    # Consistency check: hashes on record must reproduce the contract hash.
    expected_hash = compute_contract_hash(contract["offer_hash"], contract["promise_set_hash"])
    if contract.get("contract_hash") and contract["contract_hash"] != expected_hash:
        raise HTTPException(
            status_code=409,
            detail="Stored contract_hash does not match recomputed hash — offer drift suspected",
        )

    # Amount must come from the FROZEN structured price promise, never from
    # a caller-supplied value.
    price_promises = [
        p
        for p in STORE.list("promise")
        if p.get("contract_id") == contract_id and p.get("key") == "price.amount_paise"
    ]
    amount_paise = next(
        (p["value"] for p in price_promises if isinstance(p.get("value"), int)), None
    )
    if amount_paise is None:
        raise HTTPException(status_code=409, detail="Cannot authorize: frozen price unknown")

    validate_transition(contract["status"], "AWAITING_BUYER_AUTH")

    now = datetime.now(UTC).isoformat()
    envelope = AuthorityEnvelope(
        max_amount_paise=int(amount_paise),
        authorized_at=now,
        authorized_by="demo-buyer",
        scope="single_purchase",
        contract_hash_at_authorization=expected_hash,
    )
    updated = STORE.update(
        contract_id,
        buyer_authority=envelope.model_dump(),
        status="AWAITING_BUYER_AUTH",
        contract_hash=expected_hash,
    )
    append_event(
        aggregate_type="contract",
        aggregate_id=contract_id,
        event_type="BUYER_AUTHORIZED",
        payload={
            "max_amount_paise": envelope.max_amount_paise,
            "contract_hash_at_authorization": expected_hash,
            "authorized_at": now,
        },
    )
    return {"contract": _to_model(DanteContract, updated or {})}


@router.get("/{contract_id}/timeline")
async def contract_timeline(
    contract_id: str,
    category: str | None = Query(default=None),
) -> dict[str, Any]:
    """Append-only event trace for one contract, oldest first."""
    _get_contract_or_404(contract_id)
    if category is not None and category not in _TIMELINE_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"category must be one of {sorted(_TIMELINE_CATEGORIES)}",
        )
    events = LOG.for_aggregate(contract_id)
    if category is not None:
        events = [e for e in events if e.get("category") == category]
    events.sort(key=lambda e: e.get("created_at") or "")
    return {"events": events}


@router.post("/{contract_id}/verify")
async def verify_contract(contract_id: str) -> dict[str, Any]:
    """Run deterministic promise verification against observed facts."""
    _get_contract_or_404(contract_id)
    result = evaluate_contract(contract_id)
    return {
        "breaches": [b.model_dump() for b in result["breaches"]],
        "status": result["status"],
        "satisfied": result["satisfied"],
        "status_target": result["status_target"],
        "unobserved_material_keys": result["unobserved_material_keys"],
        "checked_promise_count": result["checked_promise_count"],
    }
