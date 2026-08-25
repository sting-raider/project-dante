"""Demo simulation control surface (plan section 26) — Agent F.

Every endpoint here is gated on settings.DEMO_MODE and returns 403 when the
flag is off. Fulfillment events are SYNTHETIC; Razorpay actions elsewhere in
the app remain real test-mode. Every response carries synthetic markers.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from project_dante.db.store import STORE
from project_dante.domain.events import LOG
from project_dante.integrations.merchant import service
from project_dante.settings import get_settings

router = APIRouter(prefix="/demo", tags=["demo"])
settings = get_settings()


def _require_demo_mode() -> None:
    if not settings.demo_mode:
        raise HTTPException(
            status_code=403,
            detail="Demo endpoints disabled: DEMO_MODE is off.",
        )
    # Review finding: with real Razorpay keys configured, unauthenticated
    # synthetic fulfillment facts could steer the rights/policy chain into
    # issuing REAL refunds. State-changing demo endpoints therefore refuse
    # to run in live-test mode — the same guard /demo/razorpay/simulate-event
    # already enforced.
    if settings.razorpay_live_test_mode:
        raise HTTPException(
            status_code=403,
            detail=(
                "Demo simulation endpoints are disabled while Razorpay live "
                "Test Mode keys are configured (synthetic data must never "
                "drive real money actions). Set DEMO_MODE=false or remove "
                "the API keys."
            ),
        )


@router.post("/reset")
def reset() -> dict:
    _require_demo_mode()
    removed = STORE.reset()
    log_removed = LOG.reset()
    products = service.seed_catalog()
    return {
        "reset": True,
        "records_removed": removed,
        "events_removed": log_removed,
        "products": products,
        "synthetic": True,
    }


@router.post("/contracts/{contract_id}/ship")
def ship(contract_id: str) -> dict:
    _require_demo_mode()
    if STORE.get(contract_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown contract: {contract_id}")
    result = service.apply_fulfillment_event(contract_id, "ship")
    return {**result, "synthetic": True}


@router.post("/contracts/{contract_id}/deliver")
def deliver(contract_id: str, body: dict = Body(default={})) -> dict:
    """Deliver with a scenario, then run Agent D's verifier when available.

    scenario: correct | wrong_variant | late
    """
    _require_demo_mode()
    if STORE.get(contract_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown contract: {contract_id}")

    scenario = (body or {}).get("scenario", "correct")
    if scenario not in ("correct", "wrong_variant", "late"):
        raise HTTPException(status_code=422, detail=f"Unknown scenario: {scenario}")

    result = service.apply_fulfillment_event(contract_id, "deliver", scenario=scenario)

    breaches: list[dict] = []
    contract_status: str | None = None
    try:
        from project_dante.domain.promises.verifier import evaluate_contract
    except ImportError:
        verification_error = "verifier module not available"
    else:
        try:
            verdict = evaluate_contract(contract_id)
            breaches = verdict.get("breaches", [])
            contract_status = verdict.get("status")
            verification_error = None
        except Exception as exc:  # verifier present but contract not verifiable yet
            breaches, verification_error = [], f"verification failed: {exc}"

    return {
        **result,
        "observed_facts": result["facts"],
        "breaches": breaches,
        "status": contract_status,
        "verification_error": verification_error,
        "synthetic": True,
    }


@router.post("/contracts/{contract_id}/replacement-unavailable")
def replacement_unavailable(contract_id: str) -> dict:
    _require_demo_mode()
    if STORE.get(contract_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown contract: {contract_id}")
    result = service.apply_fulfillment_event(
        contract_id, "replacement_check", scenario="unavailable"
    )
    return {**result, "observed_facts": result["facts"], "synthetic": True}
