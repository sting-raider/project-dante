"""Machine-readable merchant surface (plan §10).

    GET  /api/merchant/profile                      -> capability profile
    POST /api/merchant/offers/freeze                -> frozen offer snapshot
    GET  /api/merchant/orders/{contract_id}/status  -> fulfillment status

These endpoints let an AI buyer transact against EXPLICIT machine-readable
statements — capabilities, structured offers, honest order state — instead of
scraping human-facing pages. The freeze endpoint delegates to the existing
frozen service (`freeze_offer`); the status endpoint projects only stored
facts/events, never invented state.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from project_dante.integrations.merchant import profile, service

router = APIRouter(prefix="/merchant", tags=["merchant"])


@router.get("/profile")
def get_profile() -> dict[str, Any]:
    """What this merchant can commit to, measured — not asserted."""
    return profile.build_merchant_profile()


@router.post("/offers/freeze")
def freeze_offer(body: dict = Body(default={})) -> dict[str, Any]:
    """Snapshot an offer for a Dante contract.

    Body: {"offer_id": "off_<SKU>"} (a bare SKU is accepted too).
    422 on a missing offer_id, 404 on an unknown one.
    """
    offer_id = (body or {}).get("offer_id")
    if not isinstance(offer_id, str) or not offer_id.strip():
        raise HTTPException(status_code=422, detail="body must include offer_id (string)")
    try:
        return service.freeze_offer(offer_id.strip())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown offer_id: {offer_id}") from exc


@router.get("/orders/{contract_id}/status")
def order_status(contract_id: str) -> dict[str, Any]:
    """Fulfillment stage derived from stored facts/events for the contract."""
    try:
        return profile.order_status(contract_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
