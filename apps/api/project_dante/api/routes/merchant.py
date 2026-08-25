"""Merchant-facing API surface (docs/API_CONTRACT.md — Agent F).

    GET /api/merchant/catalog/search
    GET /api/merchant/products/{sku}
    GET /api/merchant/analytics

Analytics is honest simple math over the committed catalog plus whatever
intent evaluations are present in STORE — no invented numbers.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from project_dante.db.store import STORE
from project_dante.integrations.merchant import service

router = APIRouter(prefix="/merchant", tags=["merchant"])


@router.get("/catalog/search")
def search(
    q: str | None = Query(default=None),
    category: str | None = Query(default=None),
    max_price_paise: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    results = service.search_catalog(
        query=q,
        category=category,
        max_price_paise=max_price_paise,
        limit=limit,
    )
    return {"results": results, "count": len(results)}


@router.get("/products/{sku}")
def product(sku: str) -> dict:
    found = service.get_product(sku)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Unknown SKU: {sku}")
    return found


@router.get("/analytics")
def analytics() -> dict:
    base = service.catalog_analytics_base()

    # AI-transactable rate + blocker distribution from stored intent evaluations.
    # Evaluations are written by Agent C's intent search pipeline as
    # `_type: evaluation` records with {feasible, hard_failures: [{key,...}]}.
    evaluations = STORE.list("evaluation")
    blocker_distribution: dict[str, int] = {}
    feasible_count = 0
    for ev in evaluations:
        if ev.get("feasible"):
            feasible_count += 1
        else:
            for failure in ev.get("hard_failures") or []:
                key = str(failure.get("key") or "unknown")
                blocker_distribution[key] = blocker_distribution.get(key, 0) + 1

    total_evals = len(evaluations)
    transactable_rate = round(feasible_count / total_evals, 4) if total_evals else 0.0

    return {
        **base,
        "evaluated_intents": total_evals,
        "ai_transactable_rate": transactable_rate,
        "blocker_distribution": blocker_distribution,
    }
