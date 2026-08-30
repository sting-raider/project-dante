"""Rights & remedies routes (Agent E) — per docs/API_CONTRACT.md:

GET  /api/contracts/{id}/rights    -> {graph, entitlements}
GET  /api/contracts/{id}/breaches  -> {breaches}
GET  /api/contracts/{id}/remedies  -> {proposals}   # auto-plan when breach exists

POST /api/remedies/{proposal_id}/policy   -> {decision, money_action}
POST /api/remedies/{proposal_id}/approve  -> {money_action}
POST /api/remedies/{proposal_id}/execute  -> {money_action, refund|null, decision}
"""

from __future__ import annotations

import contextlib
import hmac
from typing import Any

from fastapi import APIRouter, Header, HTTPException

from project_dante.db.store import STORE
from project_dante.domain.remedies.planner import get_proposals, plan_remedies
from project_dante.domain.rights.engine import (
    build_rights_graph,
    evaluate_eligibility,
    get_breaches,
)
from project_dante.settings import get_settings

router = APIRouter(tags=["rights"])


def _require_human_approval_operator(token: str | None) -> None:
    """Authenticate the operator who is allowed to approve money actions.

    The domain approval gate still verifies the policy decision, amount, and
    drift. This header gate supplies the missing request-level identity check;
    an unconfigured token fails closed even in the offline sandbox.
    """
    configured = (get_settings().demo_operator_token or "").strip()
    presented = token.strip() if isinstance(token, str) else ""
    if not configured:
        raise HTTPException(
            status_code=503,
            detail="human approval is not configured",
        )
    if not presented or not hmac.compare_digest(presented, configured):
        raise HTTPException(
            status_code=403,
            detail="human approval requires a valid X-Demo-Operator-Token",
        )


def _strip(rec: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop private store keys (_type etc. stay — frontend uses _type badges;
    only internal None-noise is trimmed). Kept simple: return as-is."""
    return rec


@router.get("/contracts/{contract_id}/rights")
async def contract_rights(contract_id: str) -> dict[str, Any]:
    if STORE.get(contract_id) is None:
        raise HTTPException(status_code=404, detail=f"contract {contract_id} not found")
    entitlements = evaluate_eligibility(contract_id)
    graph = build_rights_graph(contract_id)
    return {"graph": graph, "entitlements": entitlements}


@router.get("/contracts/{contract_id}/breaches")
async def contract_breaches(contract_id: str) -> dict[str, Any]:
    if STORE.get(contract_id) is None:
        raise HTTPException(status_code=404, detail=f"contract {contract_id} not found")
    return {"breaches": get_breaches(contract_id)}


@router.get("/contracts/{contract_id}/remedies")
async def contract_remedies(contract_id: str) -> dict[str, Any]:
    if STORE.get(contract_id) is None:
        raise HTTPException(status_code=404, detail=f"contract {contract_id} not found")
    proposals = get_proposals(contract_id)
    if not proposals and get_breaches(contract_id):
        result = plan_remedies(contract_id)
        proposals = result["proposals"]
    return {"proposals": proposals}


# ------------------------------------------------------- money-action flow


def _load_proposal(proposal_id: str) -> dict[str, Any]:
    prop = STORE.get(proposal_id)
    if not prop or prop.get("_type") != "remedy":
        raise HTTPException(status_code=404, detail=f"remedy proposal {proposal_id} not found")
    return prop


@router.post("/remedies/{proposal_id}/policy")
async def remedy_policy(proposal_id: str) -> dict[str, Any]:
    """Evaluate policy for a remedy proposal; returns decision + money action.

    Idempotent-friendly: reuses the existing money action for this proposal
    (same idempotency key); an already-executed one short-circuits.
    """
    from project_dante.domain.money.policy import (
        build_money_action_for_remedy,
        evaluate_money_action,
    )

    _load_proposal(proposal_id)

    existing = STORE.find_one(
        "money_action",
        idempotency_key=f"project-dante:{STORE.get(proposal_id)['contract_id']}:{proposal_id}:v1",
    )
    if existing and existing.get("status") == "executed":
        decision_rec = STORE.find_one("policy_decision", money_action_id=existing["id"])
        return {
            "decision": (
                {k: v for k, v in decision_rec.items()} if decision_rec else None
            ),
            "money_action": existing,
        }

    try:
        ma = build_money_action_for_remedy(proposal_id)
        decision = evaluate_money_action(ma)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # REQUIRE_APPROVAL moves the contract into AWAITING_REMEDY_APPROVAL here,
    # mirroring execute_remedy's approval branch so /policy alone is coherent.
    if decision["decision"] == "REQUIRE_APPROVAL":
        from project_dante.domain.money.policy import _transition_contract

        with contextlib.suppress(Exception):  # already there / non-blocking
            _transition_contract(ma["contract_id"], "AWAITING_REMEDY_APPROVAL")

    return {
        "decision": decision,
        "money_action": STORE.get(ma["id"]),
    }


@router.post("/remedies/{proposal_id}/execute")
async def remedy_execute(proposal_id: str) -> dict[str, Any]:
    """THE GATED PIPELINE end-to-end: evaluate -> final check -> refund."""
    from project_dante.domain.money.policy import execute_remedy

    _load_proposal(proposal_id)
    try:
        result = execute_remedy(proposal_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "money_action": result.get("money_action"),
        "refund": result.get("refund"),
        "decision": result.get("decision"),
        "executed": bool(result.get("executed")),
        **({"note": result["note"]} if result.get("note") else {}),
        **({"error": result["error"]} if result.get("error") else {}),
    }


@router.post("/remedies/{proposal_id}/approve")
async def remedy_approve(
    proposal_id: str,
    x_demo_operator_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Human approval path for REQUIRE_APPROVAL decisions, then executes."""
    from project_dante.domain.money.policy import approve_remedy

    _require_human_approval_operator(x_demo_operator_token)
    _load_proposal(proposal_id)
    try:
        result = approve_remedy(proposal_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"money_action": result.get("money_action"), "refund": result.get("refund")}
