"""Deterministic financial policy engine (master plan §15).

This module is the single authority that decides whether a typed
MoneyActionProposal may proceed, and it owns THE GATED PIPELINE that turns an
approved RemedyProposal into a real (or sandbox) Razorpay refund.

Design invariants (master plan §9, §15.2):
  - rules come from merchant-owned YAML (``policies/aster_electronics.yaml``);
  - nothing here consults an LLM; every rule is deterministic code;
  - every refund amount is integer paise, bounded by the captured amount;
  - refund reasons must appear in the merchant allow-list;
  - full refunds above the autonomous limit REQUIRE_APPROVAL;
  - every decision is persisted with the SHA-256 of the exact policy snapshot
    used, so it can be replayed/audited later;
  - the executor re-validates contract/amount/payment immediately before any
    Razorpay call and is idempotent per ``project-dante:{contract}:{remedy}:v1``
    idempotency key.

Note on ``agent.may_execute_money_action: false``: LLM agents may only PROPOSE.
``execute_remedy`` is deterministic server code — the one sanctioned executor —
which is exactly the separation the money-authority boundary requires.
"""

from __future__ import annotations

import os
from collections import deque
from functools import lru_cache
from typing import Any

import yaml

from project_dante.db.store import STORE
from project_dante.domain.events import append_event, new_id, now_iso
from project_dante.domain.hashing import sha256_hex

# ------------------------------------------------------------------ policy

_POLICY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "policies", "aster_electronics.yaml"
)

# Safe defaults mirroring the shipped merchant policy; used verbatim if the
# YAML file is missing/corrupt so the engine fails CLOSED and LOADED.
_DEFAULT_POLICY: dict[str, Any] = {
    "payment": {
        "require_user_confirmation": True,
        "max_order_amount_paise": 20_000_000,
    },
    "refund": {
        "full_refund": {
            "allowed_reasons": [
                "wrong_sku",
                "region_mismatch",
                "materially_not_as_described",
                "warranty_type_mismatch",
            ],
            "max_amount": "original_captured_amount",
            "require_human_approval_above_paise": 2_000_000,
        },
        "partial_refund": {
            "max_auto_amount_paise": 50_000,
            "allowed_reasons": ["delivery_sla_minor", "missing_low_value_accessory"],
        },
    },
    "agent": {
        "may_create_order": True,
        "may_propose_refund": True,
        "may_execute_money_action": False,
    },
}

# Policy identifiers cited in decisions (shown in the approval UI, plan §52).
P_PAYMENT_ORDER = "P-PAYMENT-01"  # order amount ceiling + user confirmation
P_AGENT_PERMS = "P-AGENT-01"  # what the agent may propose/create
P_REFUND_BOUNDS = "P-REFUND-02"  # 0 < amount <= original captured amount
P_REFUND_FULL = "P-REFUND-01"  # full-refund reason allow-list
P_REFUND_FULL_AUTO = "P-REFUND-03"  # autonomous full-refund threshold
P_REFUND_PARTIAL = "P-REFUND-04"  # partial-refund reasons + auto ceiling
P_GENERIC = "P-GENERIC-01"  # structural validity (type/currency/contract)


@lru_cache(maxsize=1)
def load_policy() -> dict[str, Any]:
    """Load the merchant money policy from YAML (cached). Falls back to the
    embedded safe defaults when the file is missing or unreadable."""
    try:
        with open(_POLICY_FILE, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict) and data:
            merged = {
                "payment": {**_DEFAULT_POLICY["payment"], **(data.get("payment") or {})},
                "refund": {
                    "full_refund": {
                        **_DEFAULT_POLICY["refund"]["full_refund"],
                        **((data.get("refund") or {}).get("full_refund") or {}),
                    },
                    "partial_refund": {
                        **_DEFAULT_POLICY["refund"]["partial_refund"],
                        **((data.get("refund") or {}).get("partial_refund") or {}),
                    },
                },
                "agent": {**_DEFAULT_POLICY["agent"], **(data.get("agent") or {})},
            }
            return merged
    except (OSError, yaml.YAMLError):
        pass
    return {**_DEFAULT_POLICY}


def policy_snapshot_hash() -> str:
    """Stable hash of the effective policy snapshot."""
    return sha256_hex(load_policy())


# Breach reason codes emitted upstream (verifier/demo) -> policy-level reasons.
# Unknown codes pass through lower-cased and are DENYed by the allow-list.
_REASON_ALIASES: dict[str, str] = {
    "WRONG_SKU": "wrong_sku",
    "SKU_MISMATCH": "wrong_sku",
    "WRONG_ITEM": "wrong_sku",
    "REGION_MISMATCH": "region_mismatch",
    "WARRANTY_REGION_MISMATCH": "region_mismatch",
    "WARRANTY_TYPE_MISMATCH": "warranty_type_mismatch",
    "MATERIAL_VARIANT_MISMATCH": "materially_not_as_described",
    "VARIANT_MISMATCH": "materially_not_as_described",
    "MATERIALLY_NOT_AS_DESCRIBED": "materially_not_as_described",
    "NOT_AS_DESCRIBED": "materially_not_as_described",
    "DELIVERY_SLA_MISS": "delivery_sla_minor",
    "DELIVERY_SLA_MINOR": "delivery_sla_minor",
    "LATE_DELIVERY": "delivery_sla_minor",
    "MISSING_ACCESSORY": "missing_low_value_accessory",
    "MISSING_LOW_VALUE_ACCESSORY": "missing_low_value_accessory",
}


def normalize_reason_code(raw: str | None) -> str:
    """Map an upstream breach reason code onto the policy reason vocabulary."""
    if not raw:
        return "unknown"
    return _REASON_ALIASES.get(str(raw).strip().upper(), str(raw).strip().lower())


def _fmt_inr(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"


# ------------------------------------------------------- decision plumbing


def _make_decision(
    *,
    decision: str,
    policy_ids: list[str],
    reason_codes: list[str],
    explanation: str,
) -> dict[str, Any]:
    return {
        "decision": decision,
        "policy_ids": policy_ids,
        "reason_codes": reason_codes,
        "explanation": explanation,
        "evaluated_at": now_iso(),
        "policy_snapshot_hash": policy_snapshot_hash(),
    }


def _captured_amount_paise(contract: dict[str, Any]) -> int | None:
    """Original captured amount for the contract, in paise.

    Prefers the contract's own ``amount_paise``; falls back to the stored
    Razorpay payment record when the contract field is absent.
    """
    amt = contract.get("amount_paise")
    if isinstance(amt, int) and amt > 0:
        return amt
    pay_id = contract.get("razorpay_payment_id")
    if pay_id:
        pay = STORE.get(pay_id) or STORE.find_one("razorpay_payment", payment_id=pay_id)
        if pay and isinstance(pay.get("amount_paise"), int):
            return pay["amount_paise"]
    return None


def _persist_and_link(
    proposal: dict[str, Any], decision: dict[str, Any], contract_id: str
) -> dict[str, Any]:
    """Persist the PolicyDecision, mirror status onto the money action, and
    append the POLICY_* audit events."""
    mid = proposal.get("id") or ""
    rec: dict[str, Any] = {
        "_type": "policy_decision",
        "id": new_id("pd"),
        "money_action_id": mid,
        "contract_id": contract_id,
        "remedy_proposal_id": proposal.get("remedy_proposal_id"),
        **decision,
    }
    STORE.put(rec)

    status_map = {"ALLOW": "allowed", "REQUIRE_APPROVAL": "approval_required", "DENY": "denied"}
    if mid and STORE.get(mid):
        STORE.update(mid, status=status_map[decision["decision"]])

    payload = {
        "decision": decision["decision"],
        "policy_ids": decision["policy_ids"],
        "reason_codes": decision["reason_codes"],
        "explanation": decision["explanation"],
        "money_action_id": mid,
        "amount_paise": proposal.get("amount_paise"),
        "action_type": proposal.get("type"),
        "policy_snapshot_hash": decision["policy_snapshot_hash"],
    }
    append_event(
        aggregate_type="money_action",
        aggregate_id=mid or contract_id,
        event_type="POLICY_DECIDED",
        payload=payload,
        correlation_id=contract_id,
        causation_id=proposal.get("remedy_proposal_id"),
    )
    if decision["decision"] == "ALLOW":
        append_event(
            aggregate_type="money_action",
            aggregate_id=mid or contract_id,
            event_type="POLICY_ALLOWED",
            payload=payload,
            correlation_id=contract_id,
            causation_id=proposal.get("remedy_proposal_id"),
        )
    elif decision["decision"] == "DENY":
        append_event(
            aggregate_type="money_action",
            aggregate_id=mid or contract_id,
            event_type="POLICY_DENIED",
            payload=payload,
            correlation_id=contract_id,
            causation_id=proposal.get("remedy_proposal_id"),
        )
    return rec


# ------------------------------------------------------------- evaluation


def evaluate_money_action(proposal: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a typed MoneyActionProposal against the merchant policy.

    Returns a PolicyDecision-shaped dict (see ``domain.types.PolicyDecision``).
    Side effects: persists a ``policy_decision`` record, appends
    POLICY_DECIDED + POLICY_ALLOWED/POLICY_DENIED events, and mirrors the
    outcome onto the stored money action's ``status`` when it exists.
    """
    if not isinstance(proposal, dict):
        proposal = proposal.model_dump()

    action_type = proposal.get("type")
    contract_id = proposal.get("contract_id") or ""

    # --- structural gates -------------------------------------------------
    if action_type not in {"create_order", "refund_full", "refund_partial"}:
        return _finish(proposal, contract_id, _make_decision(
            decision="DENY",
            policy_ids=[P_GENERIC],
            reason_codes=["UNKNOWN_ACTION_TYPE"],
            explanation=f"Action type {action_type!r} is not a recognized money action.",
        ))

    if proposal.get("currency", "INR") != "INR":
        return _finish(proposal, contract_id, _make_decision(
            decision="DENY",
            policy_ids=[P_GENERIC],
            reason_codes=["UNSUPPORTED_CURRENCY"],
            explanation="Only INR money actions are supported.",
        ))

    try:
        amount = int(proposal.get("amount_paise"))
    except (TypeError, ValueError):
        return _finish(proposal, contract_id, _make_decision(
            decision="DENY",
            policy_ids=[P_GENERIC],
            reason_codes=["INVALID_AMOUNT"],
            explanation="Money action amount is missing or not an integer paise value.",
        ))

    contract = STORE.get(contract_id) if contract_id else None
    if contract is None:
        return _finish(proposal, contract_id, _make_decision(
            decision="DENY",
            policy_ids=[P_GENERIC],
            reason_codes=["CONTRACT_NOT_FOUND"],
            explanation=f"Contract {contract_id or '<missing>'} does not exist.",
        ))

    policy = load_policy()

    # --- order creation ---------------------------------------------------
    if action_type == "create_order":
        if not policy["agent"]["may_create_order"]:
            return _finish(proposal, contract_id, _make_decision(
                decision="DENY",
                policy_ids=[P_AGENT_PERMS],
                reason_codes=["AGENT_MAY_NOT_CREATE_ORDER"],
                explanation="Merchant policy forbids the agent from creating orders.",
            ))
        ceiling = int(policy["payment"]["max_order_amount_paise"])
        if amount <= 0 or amount > ceiling:
            return _finish(proposal, contract_id, _make_decision(
                decision="DENY",
                policy_ids=[P_PAYMENT_ORDER],
                reason_codes=["ORDER_AMOUNT_OUT_OF_BOUNDS"],
                explanation=(
                    f"Order amount {_fmt_inr(max(amount, 0))} is outside the allowed "
                    f"range (0, {_fmt_inr(ceiling)}]."
                ),
            ))
        return _finish(proposal, contract_id, _make_decision(
            decision="ALLOW",
            policy_ids=[P_PAYMENT_ORDER],
            reason_codes=["WITHIN_POLICY_LIMITS"],
            explanation=(
                f"Order of {_fmt_inr(amount)} is within the "
                f"{_fmt_inr(ceiling)} payment ceiling."
            ),
        ))

    # --- refunds ------------------------------------------------------------
    if not policy["agent"]["may_propose_refund"]:
        return _finish(proposal, contract_id, _make_decision(
            decision="DENY",
            policy_ids=[P_AGENT_PERMS],
            reason_codes=["AGENT_MAY_NOT_PROPOSE_REFUND"],
            explanation="Merchant policy forbids agent-proposed refunds.",
        ))

    reason = normalize_reason_code(proposal.get("reason_code"))
    captured = _captured_amount_paise(contract)

    if amount <= 0:
        return _finish(proposal, contract_id, _make_decision(
            decision="DENY",
            policy_ids=[P_REFUND_BOUNDS],
            reason_codes=["NON_POSITIVE_AMOUNT"],
            explanation=f"Refund amount must be positive; got {amount} paise.",
        ))

    if captured is None:
        return _finish(proposal, contract_id, _make_decision(
            decision="DENY",
            policy_ids=[P_REFUND_BOUNDS],
            reason_codes=["NO_CAPTURED_AMOUNT"],
            explanation=(
                "No captured payment amount is on record for this contract; "
                "refunds cannot be bounded."
            ),
        ))

    if amount > captured:
        return _finish(proposal, contract_id, _make_decision(
            decision="DENY",
            policy_ids=[P_REFUND_BOUNDS],
            reason_codes=["AMOUNT_EXCEEDS_CAPTURED"],
            explanation=(
                f"Refund {_fmt_inr(amount)} exceeds the captured payment "
                f"{_fmt_inr(captured)}."
            ),
        ))

    if action_type == "refund_full":
        allowed: list[str] = list(policy["refund"]["full_refund"]["allowed_reasons"])
        if reason not in allowed:
            return _finish(proposal, contract_id, _make_decision(
                decision="DENY",
                policy_ids=[P_REFUND_FULL],
                reason_codes=["REFUND_REASON_NOT_ALLOWED", reason],
                explanation=(
                    f"Full refunds are not autonomously allowed for reason "
                    f"{reason!r}. Allowed reasons: {', '.join(allowed)}."
                ),
            ))
        threshold = int(policy["refund"]["full_refund"]["require_human_approval_above_paise"])
        if amount > threshold:
            return _finish(proposal, contract_id, _make_decision(
                decision="REQUIRE_APPROVAL",
                policy_ids=[P_REFUND_FULL_AUTO],
                reason_codes=["FULL_REFUND_ABOVE_HUMAN_APPROVAL_THRESHOLD", reason],
                explanation=(
                    f"Full refund of {_fmt_inr(amount)} exceeds the "
                    f"{_fmt_inr(threshold)} autonomous limit; human approval required."
                ),
            ))
        return _finish(proposal, contract_id, _make_decision(
            decision="ALLOW",
            policy_ids=[P_REFUND_FULL, P_REFUND_BOUNDS, P_REFUND_FULL_AUTO],
            reason_codes=["WITHIN_POLICY_LIMITS", reason],
            explanation=(
                f"AUTO-APPROVED BY POLICY {P_REFUND_FULL_AUTO}: full refund of "
                f"{_fmt_inr(amount)} for {reason} is within the autonomous limit "
                f"of {_fmt_inr(threshold)} and does not exceed the captured "
                f"payment {_fmt_inr(captured)}."
            ),
        ))

    # action_type == "refund_partial"
    partial = policy["refund"]["partial_refund"]
    allowed_p: list[str] = list(partial["allowed_reasons"])
    if reason not in allowed_p:
        return _finish(proposal, contract_id, _make_decision(
            decision="DENY",
            policy_ids=[P_REFUND_PARTIAL],
            reason_codes=["REFUND_REASON_NOT_ALLOWED", reason],
            explanation=(
                f"Partial refunds are not allowed for reason {reason!r}. "
                f"Allowed reasons: {', '.join(allowed_p)}."
            ),
        ))
    auto_max = int(partial["max_auto_amount_paise"])
    if amount > auto_max:
        return _finish(proposal, contract_id, _make_decision(
            decision="REQUIRE_APPROVAL",
            policy_ids=[P_REFUND_PARTIAL],
            reason_codes=["PARTIAL_REFUND_ABOVE_AUTO_LIMIT", reason],
            explanation=(
                f"Partial refund of {_fmt_inr(amount)} exceeds the "
                f"{_fmt_inr(auto_max)} autonomous partial-refund limit; "
                f"human approval required."
            ),
        ))
    return _finish(proposal, contract_id, _make_decision(
        decision="ALLOW",
        policy_ids=[P_REFUND_PARTIAL, P_REFUND_BOUNDS],
        reason_codes=["WITHIN_POLICY_LIMITS", reason],
        explanation=(
            f"Partial refund of {_fmt_inr(amount)} for {reason} is within the "
            f"{_fmt_inr(auto_max)} autonomous limit."
        ),
    ))


def _finish(
    proposal: dict[str, Any], contract_id: str, decision: dict[str, Any]
) -> dict[str, Any]:
    _persist_and_link(proposal, decision, contract_id)
    return decision


# ------------------------------------------------- money-action construction

_MONEY_TYPE_BY_REMEDY = {
    "refund_full": "refund_full",
    "refund_partial": "refund_partial",
    "delivery_compensation": "refund_partial",
}


def _first_breach(contract_id: str) -> dict[str, Any] | None:
    breaches = STORE.find("breach", contract_id=contract_id)
    return breaches[0] if breaches else None


def build_money_action_for_remedy(proposal_id: str) -> dict[str, Any]:
    """Build (or reuse) the MoneyActionProposal for a RemedyProposal.

    Reuses an existing non-executed money action with the same idempotency key
    so repeated /policy + /execute calls converge on one record. Raises
    KeyError when the remedy or contract is missing, and ValueError when the
    remedy type carries no money action (e.g. replacement).
    """
    prop = STORE.get(proposal_id)
    if not prop or prop.get("_type") != "remedy":
        raise KeyError(f"remedy proposal {proposal_id} not found")
    contract = STORE.get(prop.get("contract_id") or "")
    if contract is None:
        raise KeyError(f"contract {prop.get('contract_id')} not found")

    action_type = _MONEY_TYPE_BY_REMEDY.get(prop.get("remedy_type") or "")
    if action_type is None:
        raise ValueError(
            f"remedy type {prop.get('remedy_type')!r} carries no money action"
        )

    breach = (
        STORE.get(prop.get("breach_id") or "") if prop.get("breach_id") else None
    ) or _first_breach(contract["id"])
    reason = normalize_reason_code(breach.get("reason_code")) if breach else prop.get("remedy_type")

    amount = prop.get("amount_paise") or contract.get("amount_paise") or 0
    amount = int(amount)

    evidence_ids = list(prop.get("evidence_ids") or [])
    if not evidence_ids and breach:
        evidence_ids = [eid for eid in (breach.get("observed_fact_id"), breach.get("promise_id")) if eid]

    explanation = (
        f"{action_type.replace('_', ' ').title()} of {_fmt_inr(amount)} on contract "
        f"{contract.get('display_code') or contract['id']}: delivered unit did not "
        f"match the frozen promises ({breach.get('reason_code') if breach else 'unspecified'}); "
        f"{len(evidence_ids)} evidence artifact(s) attached."
    )

    fields: dict[str, Any] = {
        "type": action_type,
        "amount_paise": amount,
        "currency": "INR",
        "razorpay_payment_id": contract.get("razorpay_payment_id"),
        "razorpay_order_id": contract.get("razorpay_order_id"),
        "contract_id": contract["id"],
        "remedy_proposal_id": prop["id"],
        "reason_code": reason,
        "human_explanation": explanation,
        "evidence_ids": evidence_ids,
        "policy_snapshot_hash": policy_snapshot_hash(),
        "created_at": now_iso(),
    }

    idem = f"project-dante:{contract['id']}:{prop['id']}:v1"
    existing = STORE.find_one("money_action", idempotency_key=idem)
    if existing and existing.get("status") == "executed":
        return existing  # caller short-circuits: already refunded
    if existing:
        return STORE.update(existing["id"], status="proposed", **fields) or existing

    ma = {
        "_type": "money_action",
        "id": new_id("ma"),
        "idempotency_key": idem,
        "status": "proposed",
        "result_ref": None,
        **fields,
    }
    return STORE.put(ma)


# ------------------------------------------------------- state transitions


def _walk_path(current: str, target: str) -> list[str]:
    """BFS the frozen state machine for the shortest legal transition path."""
    from project_dante.domain.state_machine import TRANSITIONS

    if current == target:
        return []
    q: deque[list[str]] = deque([[current]])
    seen = {current}
    while q:
        path = q.popleft()
        for nxt in TRANSITIONS.get(path[-1], set()):
            npath = path + [nxt]
            if nxt == target:
                return npath[1:]
            if nxt not in seen:
                seen.add(nxt)
                q.append(npath)
    from project_dante.domain.state_machine import InvalidTransition

    raise InvalidTransition(current, target)


def _transition_contract(contract_id: str, target: str) -> list[str]:
    """Move the contract to ``target`` via legal intermediate states."""
    from project_dante.domain.state_machine import validate_transition

    contract = STORE.get(contract_id)
    if contract is None:
        raise KeyError(f"contract {contract_id} not found")
    steps = _walk_path(contract.get("status") or "DRAFT", target)
    cur = contract.get("status")
    for step in steps:
        validate_transition(cur, step)
        STORE.update(contract_id, status=step)
        cur = step
    if len(steps) > 1:
        append_event(
            aggregate_type="contract",
            aggregate_id=contract_id,
            event_type="STATE_RECONCILED",
            payload={"walked_path": steps, "final_status": target},
        )
    return steps


# ----------------------------------------------------------- razorpay glue


def _razorpay_service() -> Any | None:
    """Late-binding import of Agent B's razorpay service; None => sandbox stub.

    Imported at call time so the real integration is picked up as soon as it
    lands, without a deploy-order dependency between agents.
    """
    try:
        from project_dante.integrations.razorpay import service as svc

        if hasattr(svc, "create_refund"):
            return svc
    except Exception:  # noqa: BLE001 — module genuinely absent in sandbox runs
        pass
    return None


def rzp_mode() -> str:
    svc = _razorpay_service()
    if svc is not None and hasattr(svc, "mode"):
        try:
            return str(svc.mode())
        except Exception:  # noqa: BLE001
            return "sandbox"
    return "sandbox"


def _create_refund(payment_id: str, amount_paise: int, idempotency_key: str, notes: dict | None) -> dict:
    """Call the real Razorpay refund API, or the local sandbox stub.

    The stub creates a genuine ``razorpay_refund`` store record (prefix rzr_)
    honestly marked ``sandbox: true`` — never presented as a live call.
    """
    svc = _razorpay_service()
    if svc is not None:
        return dict(
            svc.create_refund(
                payment_id,
                amount_paise=amount_paise,
                idempotency_key=idempotency_key,
                notes=notes or {},
            )
        )
    rid = new_id("rzr")
    rec = {
        "_type": "razorpay_refund",
        "id": rid,
        "payment_id": payment_id,
        "amount_paise": int(amount_paise),
        "currency": "INR",
        "status": "processed",
        "speed": "normal",
        "sandbox": True,
        "mode": "sandbox",
        "idempotency_key": idempotency_key,
        "notes": notes or {},
        "created_at": now_iso(),
    }
    return STORE.put(rec)


# --------------------------------------------------------- gated execution

_BREACH_FAMILY_STATUSES = {
    "BREACH_DETECTED",
    "REMEDY_PLANNING",
    "AWAITING_REMEDY_APPROVAL",
    "REMEDY_EXECUTING",
}


def _executor_final_check(ma: dict[str, Any], decision: dict[str, Any]) -> tuple[bool, str]:
    """THE FINAL EXECUTOR CHECK — run immediately before the Razorpay call.

    Re-reads the contract from the store and re-validates everything the
    earlier policy decision relied on (plan §15.2). Anything drifted => no call.
    """
    if decision.get("decision") != "ALLOW":
        return False, f"policy decision is {decision.get('decision')}, not ALLOW"

    contract = STORE.get(ma["contract_id"])
    if contract is None:
        return False, "contract vanished between evaluation and execution"

    if contract.get("status") not in _BREACH_FAMILY_STATUSES:
        return False, f"contract status {contract.get('status')!r} is not executable"

    if not contract.get("razorpay_payment_id"):
        return False, "contract has no captured razorpay_payment_id"

    if ma.get("razorpay_payment_id") != contract.get("razorpay_payment_id"):
        return False, "target payment changed after the policy decision"

    amount = int(ma.get("amount_paise") or 0)
    if amount <= 0:
        return False, f"amount {amount} is not positive"
    captured = _captured_amount_paise(contract)
    if captured is None or amount > captured:
        return False, f"amount {amount} exceeds captured amount {captured}"

    if ma.get("policy_snapshot_hash") and ma["policy_snapshot_hash"] != policy_snapshot_hash():
        return False, "merchant policy changed after the money action was built"
    return True, ""


def execute_remedy(proposal_id: str) -> dict[str, Any]:
    """THE GATED PIPELINE: RemedyProposal -> policy -> final check -> refund.

    Returns ``{decision, money_action, refund, executed}``. Idempotent: a
    repeated call for an already-executed remedy returns the original result
    without a second Razorpay call.
    """
    prop = STORE.get(proposal_id)
    if not prop or prop.get("_type") != "remedy":
        raise KeyError(f"remedy proposal {proposal_id} not found")

    if prop.get("remedy_type") not in _MONEY_TYPE_BY_REMEDY:
        return {
            "decision": None,
            "money_action": None,
            "refund": None,
            "executed": False,
            "note": (
                f"remedy type {prop.get('remedy_type')!r} executes via the merchant "
                f"API, not Razorpay; P0 executes refunds only. Record the attempt "
                f"(fact replacement.attempted) and re-plan."
            ),
        }

    # ---- idempotency short-circuit --------------------------------------
    contract_stub = STORE.get(prop.get("contract_id") or "")
    if contract_stub is None:
        raise KeyError(f"contract {prop.get('contract_id')} not found")
    idem = f"project-dante:{contract_stub['id']}:{prop['id']}:v1"
    existing = STORE.find_one("money_action", idempotency_key=idem)
    if existing and existing.get("status") == "executed":
        refund = STORE.get(existing.get("result_ref") or "") or {
            "id": existing.get("result_ref")
        }
        decision_rec = STORE.find_one("policy_decision", money_action_id=existing["id"])
        return {
            "decision": (
                {k: v for k, v in decision_rec.items() if not k.startswith("_")}
                if decision_rec
                else None
            ),
            "money_action": existing,
            "refund": refund,
            "executed": True,
        }

    # ---- build + evaluate -------------------------------------------------
    ma = build_money_action_for_remedy(proposal_id)
    decision = evaluate_money_action(ma)

    if decision["decision"] == "DENY":
        # Contract deliberately left in the breached family; the denial is
        # fully audited (POLICY_DENIED) and the proposal marked denied.
        return {
            "decision": decision,
            "money_action": STORE.get(ma["id"]),
            "refund": None,
            "executed": False,
        }

    if decision["decision"] == "REQUIRE_APPROVAL":
        _transition_contract(ma["contract_id"], "AWAITING_REMEDY_APPROVAL")
        return {
            "decision": decision,
            "money_action": STORE.get(ma["id"]),
            "refund": None,
            "executed": False,
        }

    executed_ma, refund, err = _execute_allowed(ma, decision)
    return {
        "decision": decision,
        "money_action": executed_ma,
        "refund": refund,
        "executed": err is None,
        **({"error": err} if err else {}),
    }


def approve_remedy(proposal_id: str) -> dict[str, Any]:
    """Human approval path: AWAITING_REMEDY_APPROVAL -> REMEDY_EXECUTING -> run."""
    prop = STORE.get(proposal_id)
    if not prop or prop.get("_type") != "remedy":
        raise KeyError(f"remedy proposal {proposal_id} not found")

    idem = f"project-dante:{prop.get('contract_id')}:{prop['id']}:v1"
    existing = STORE.find_one("money_action", idempotency_key=idem)
    if existing and existing.get("status") == "executed":
        return {"money_action": existing}

    ma = existing or build_money_action_for_remedy(proposal_id)
    if ma.get("status") == "denied":
        raise ValueError("a denied money action cannot be approved")

    decision = evaluate_money_action(ma)
    if decision["decision"] == "DENY":
        raise ValueError(
            f"policy denied this remedy: {decision['explanation']}"
        )

    # Approval is the explicit human gate; walk into REMEDY_EXECUTING.
    _transition_contract(ma["contract_id"], "REMEDY_EXECUTING")
    executed_ma, refund, err = _execute_allowed(ma, decision)
    if err:
        raise ValueError(f"execution failed final check: {err}")
    return {"money_action": executed_ma, "refund": refund}


def _execute_allowed(
    ma: dict[str, Any], decision: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None, str | None]:
    """Execute an ALLOW-decided money action: final check -> refund -> REMEDIATED.

    Returns (updated_money_action, refund_record, error_message_or_None).
    """
    contract_id = ma["contract_id"]

    # ---- FINAL EXECUTOR CHECK (immediately before the call, plan §15.2) ---
    ok, why = _executor_final_check(ma, decision)
    if not ok:
        updated = STORE.update(ma["id"], status="failed")
        append_event(
            aggregate_type="money_action",
            aggregate_id=ma["id"],
            event_type="REFUND_FAILED",
            payload={"reason": why, "stage": "final_executor_check"},
            correlation_id=contract_id,
            causation_id=ma.get("remedy_proposal_id"),
        )
        # Recover to the breached family; no money moved.
        try:
            _transition_contract(contract_id, "BREACH_DETECTED")
        except Exception:  # noqa: BLE001 — already in a breached state
            pass
        return updated or ma, None, why

    # Move to a pre-execution state closest to where we are.
    contract = STORE.get(contract_id) or {}
    pre_target = (
        "REMEDY_EXECUTING"
        if contract.get("status") == "AWAITING_REMEDY_APPROVAL"
        else "REMEDY_PLANNING"
    )
    _transition_contract(contract_id, pre_target)
    STORE.update(ma["id"], status="executing")

    append_event(
        aggregate_type="money_action",
        aggregate_id=ma["id"],
        event_type="REFUND_REQUESTED",
        payload={
            "payment_id": ma.get("razorpay_payment_id"),
            "amount_paise": ma["amount_paise"],
            "currency": "INR",
            "idempotency_key": ma["idempotency_key"],
            "reason_code": ma.get("reason_code"),
            "mode": rzp_mode(),
        },
        correlation_id=contract_id,
        causation_id=ma.get("remedy_proposal_id"),
    )

    try:
        refund = _create_refund(
            payment_id=ma["razorpay_payment_id"],
            amount_paise=int(ma["amount_paise"]),
            idempotency_key=ma["idempotency_key"],
            notes={
                "contract_id": contract_id,
                "remedy_proposal_id": ma.get("remedy_proposal_id") or "",
                "reason_code": ma.get("reason_code") or "",
                "source": "project-dante",
            },
        )
    except Exception as exc:  # noqa: BLE001 — network/API failures fail safe
        updated = STORE.update(ma["id"], status="failed")
        append_event(
            aggregate_type="money_action",
            aggregate_id=ma["id"],
            event_type="REFUND_FAILED",
            payload={"reason": str(exc)[:500], "stage": "razorpay_call"},
            correlation_id=contract_id,
            causation_id=ma.get("remedy_proposal_id"),
        )
        try:
            _transition_contract(contract_id, "BREACH_DETECTED")
        except Exception:  # noqa: BLE001
            pass
        return updated or ma, None, str(exc)

    refund_id = refund.get("id") or refund.get("refund_id") or ""
    updated = STORE.update(
        ma["id"], status="executed", result_ref=refund_id, executed_at=now_iso()
    ) or ma

    append_event(
        aggregate_type="money_action",
        aggregate_id=ma["id"],
        event_type="REFUND_PROCESSED",
        payload={
            "refund_id": refund_id,
            "amount_paise": ma["amount_paise"],
            "payment_id": ma.get("razorpay_payment_id"),
            "sandbox": bool(refund.get("sandbox")),
            "mode": rzp_mode(),
        },
        correlation_id=contract_id,
        causation_id=ma.get("remedy_proposal_id"),
    )

    _transition_contract(contract_id, "REMEDIATED")
    append_event(
        aggregate_type="contract",
        aggregate_id=contract_id,
        event_type="CONTRACT_REMEDIATED",
        payload={
            "refund_id": refund_id,
            "money_action_id": ma["id"],
            "remedy_proposal_id": ma.get("remedy_proposal_id"),
            "amount_paise": ma["amount_paise"],
        },
        correlation_id=contract_id,
        causation_id=ma.get("remedy_proposal_id"),
    )

    # Close out the originating entitlement, best-effort.
    prop = STORE.get(ma.get("remedy_proposal_id") or "")
    ent_id = (prop or {}).get("entitlement_id")
    if ent_id and STORE.get(ent_id):
        STORE.update(ent_id, status="consumed")

    return updated, refund, None
