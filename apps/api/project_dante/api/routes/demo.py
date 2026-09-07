"""Demo simulation control surface (plan section 26) — Agent F.

Every endpoint here is gated on settings.DEMO_MODE and returns 403 when the
flag is off. Fulfillment events are SYNTHETIC; Razorpay actions elsewhere in
the app remain real test-mode. Every response carries synthetic markers.

Two operating postures for the state-changing endpoints
(ship / deliver / replacement-unavailable / reset):

- sandbox (no real Razorpay keys configured): DEMO_MODE=true is enough —
  the pure-sandbox walkthrough, nothing real anywhere in the loop.
- live-test-mode (real rzp_test_* keys configured): synthetic fulfillment
  facts can steer the rights chain toward REAL refunds, so the hybrid path
  (real payment + synthetic fulfillment steps) is explicit and
  operator-gated: requests must carry ``X-Demo-Operator-Token`` matching
  settings.demo_operator_token AND demo_mode must be true. An empty
  configured token in this mode keeps the endpoints LOCKED.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Body, Header, HTTPException

from project_dante.db.store import STORE
from project_dante.domain.events import LOG
from project_dante.integrations.merchant import service
from project_dante.settings import Settings, get_settings

router = APIRouter(prefix="/demo", tags=["demo"])

_OPERATOR_HEADER = "x-demo-operator-token"


def _operator_gate(s: Settings, token: str | None) -> None:
    """Shared posture logic; raises 403 unless the request may proceed."""
    if not s.demo_mode:
        raise HTTPException(
            status_code=403,
            detail="Demo endpoints disabled: DEMO_MODE is off.",
        )
    if s.razorpay_mode == "live-test-mode":
        configured = (s.demo_operator_token or "").strip()
        presented = token.strip() if isinstance(token, str) else ""
        # Review finding: with real Razorpay test keys configured, an
        # unauthenticated synthetic fulfillment fact could steer the
        # rights/policy chain into issuing REAL refunds. The hybrid path is
        # therefore explicit and operator-gated — fail closed when no
        # operator token has been provisioned.
        if not configured or not presented or not hmac.compare_digest(
            presented, configured
        ):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Demo simulation endpoints are locked while real "
                    "Razorpay Test Mode keys are configured: send a valid "
                    "X-Demo-Operator-Token header, set DEMO_MODE=false, or "
                    "remove the API keys."
                ),
            )


def _require_demo_mode(operator_token: str | None = None) -> None:
    """Gate one state-changing request; ``operator_token`` comes from the
    X-Demo-Operator-Token request header (declared per-endpoint below)."""
    # Resolve settings at request time so a cache refresh/reload cannot leave
    # the state-changing gate using a stale token or demo posture snapshot.
    _operator_gate(get_settings(), operator_token)


@router.get("/status")
def status() -> dict:
    """What is possible right now, for the UI to explain itself."""
    s = get_settings()
    live_test = s.razorpay_mode == "live-test-mode"
    return {
        "demo_mode": s.demo_mode,
        "razorpay_mode": s.razorpay_mode,
        "operator_token_required": bool(live_test and s.demo_mode),
        "operator_token_configured": bool((s.demo_operator_token or "").strip()),
    }


@router.post("/reset")
def reset(
    x_demo_operator_token: str | None = Header(default=None),
) -> dict:
    _require_demo_mode(x_demo_operator_token)
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
def ship(
    contract_id: str,
    x_demo_operator_token: str | None = Header(default=None),
) -> dict:
    _require_demo_mode(x_demo_operator_token)
    if STORE.get(contract_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown contract: {contract_id}")
    result = service.apply_fulfillment_event(contract_id, "ship")
    return {**result, "synthetic": True}


@router.post("/contracts/{contract_id}/deliver")
def deliver(
    contract_id: str,
    body: dict = Body(default={}),
    x_demo_operator_token: str | None = Header(default=None),
) -> dict:
    """Deliver with a scenario, then run Agent D's verifier when available.

    scenario: correct | wrong_variant | late
    """
    _require_demo_mode(x_demo_operator_token)
    if STORE.get(contract_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown contract: {contract_id}")

    scenario = (body or {}).get("scenario", "correct")
    line_item_id = (body or {}).get("line_item_id")
    if scenario not in ("correct", "wrong_variant", "late"):
        raise HTTPException(status_code=422, detail=f"Unknown scenario: {scenario}")

    try:
        result = service.apply_fulfillment_event(
            contract_id,
            "deliver",
            scenario=scenario,
            line_item_id=line_item_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    breaches: list[dict] = []
    contract_status: str | None = None
    verification_error: str | None = None
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
def replacement_unavailable(
    contract_id: str,
    body: dict = Body(default={}),
    x_demo_operator_token: str | None = Header(default=None),
) -> dict:
    _require_demo_mode(x_demo_operator_token)
    if STORE.get(contract_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown contract: {contract_id}")
    line_item_id = (body or {}).get("line_item_id")
    result = service.apply_fulfillment_event(
        contract_id,
        "replacement_check",
        scenario="unavailable",
        line_item_id=line_item_id,
    )
    return {**result, "observed_facts": result["facts"], "synthetic": True}
