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

import contextlib
import os
from collections import deque
from functools import lru_cache
from typing import Any

import yaml

from project_dante.db.store import STORE
from project_dante.domain.events import append_event, new_id, now_iso
from project_dante.domain.hashing import sha256_hex
from project_dante.domain.line_items import (
    contract_line_ids,
    line_item_amount_paise,
    record_line_id,
    records_for_scope,
)

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
P_SAFETY = "P-SAFETY-01"  # replay safety: mandatory idempotency key


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


def _refund_totals(
    payment_id: str | None,
) -> tuple[int, dict[str, int]]:
    """Total paise already refunded against a payment (0 when unknown).

    Reconciles both the gateway payment projection and the append-only local
    refund ledger. Either source may be ahead after a timeout or an
    out-of-band dashboard refund, so the larger aggregate total is binding.
    The second return value contains only explicitly line-attributed refunds;
    an unlinked dashboard refund is intentionally financial-only.
    """
    if not payment_id:
        return 0, {}
    pay = STORE.get(payment_id) or STORE.find_one("razorpay_payment", payment_id=payment_id)
    payment_total = 0
    if pay and isinstance(pay.get("amount_refunded"), int) and pay["amount_refunded"] >= 0:
        payment_total = pay["amount_refunded"]
    ledger_total = 0
    line_totals: dict[str, int] = {}
    seen: set[str] = set()
    for r in STORE.find("razorpay_refund", payment_id=payment_id):
        rid = str(r.get("id") or "")
        if rid and rid in seen:
            continue
        if rid:
            seen.add(rid)
        amt = r.get("amount")
        if not isinstance(amt, int):
            amt = r.get("amount_paise")
        if not isinstance(amt, int) or isinstance(amt, bool) or amt < 0:
            continue
        ledger_total += amt
        line_item_id = record_line_id(r)
        if line_item_id is None:
            notes = r.get("notes")
            if isinstance(notes, dict):
                note_line = notes.get("line_item_id")
                line_item_id = note_line if isinstance(note_line, str) and note_line else None
        if line_item_id is not None:
            line_totals[line_item_id] = line_totals.get(line_item_id, 0) + amt
    return max(payment_total, ledger_total), line_totals


def _refunded_so_far_paise(
    payment_id: str | None, line_item_id: str | None = None
) -> int:
    """Return aggregate or explicitly attributed line refund totals."""
    total, by_line = _refund_totals(payment_id)
    return total if line_item_id is None else by_line.get(line_item_id, 0)


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
        # Approval-gate binding fields: /approve may only execute when a
        # REQUIRE_APPROVAL decision exists for THIS exact action+amount.
        "idempotency_key": proposal.get("idempotency_key"),
        "amount_paise": proposal.get("amount_paise"),
        "line_item_id": proposal.get("line_item_id"),
        "affected_breach_ids": proposal.get("affected_breach_ids") or [],
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
        "line_item_id": proposal.get("line_item_id"),
        "affected_breach_ids": proposal.get("affected_breach_ids") or [],
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
    # Replay-safety gate (plan §9 invariant 5): every money action MUST carry
    # an idempotency key. Checked first — without it no external effect can
    # ever be made replay-safe, whatever the rest of the proposal says.
    idem_key = proposal.get("idempotency_key")
    if not isinstance(idem_key, str) or not idem_key.strip():
        return _finish(proposal, contract_id, _make_decision(
            decision="DENY",
            policy_ids=[P_SAFETY],
            reason_codes=["MISSING_IDEMPOTENCY_KEY"],
            explanation=(
                "Money action is missing a non-empty idempotency_key; replay "
                "safety (plan §9.5) requires every money action to carry one, "
                "so this proposal is denied before any other check."
            ),
        ))

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

    # Strict money typing (plan §19: never coerce malformed financial values).
    # Strings, floats, bools, and anything non-int are rejected as-is — a
    # float "rupee" amount must never silently truncate into paise.
    raw_amount = proposal.get("amount_paise")
    if isinstance(raw_amount, bool) or not isinstance(raw_amount, int):
        return _finish(proposal, contract_id, _make_decision(
            decision="DENY",
            policy_ids=[P_GENERIC],
            reason_codes=["INVALID_AMOUNT_TYPE"],
            explanation=(
                "Money action amount_paise must be an integer (paise); got "
                f"{type(raw_amount).__name__}. Malformed financial values are "
                f"never coerced."
            ),
        ))
    amount = raw_amount

    contract = STORE.get(contract_id) if contract_id else None
    if contract is None:
        return _finish(proposal, contract_id, _make_decision(
            decision="DENY",
            policy_ids=[P_GENERIC],
            reason_codes=["CONTRACT_NOT_FOUND"],
            explanation=f"Contract {contract_id or '<missing>'} does not exist.",
        ))

    line_item_id = proposal.get("line_item_id")
    if line_item_id is not None and (
        not isinstance(line_item_id, str)
        or not line_item_id
        or line_item_id not in contract_line_ids(contract)
    ):
        return _finish(proposal, contract_id, _make_decision(
            decision="DENY",
            policy_ids=[P_GENERIC],
            reason_codes=["INVALID_LINE_ITEM"],
            explanation=(
                "A scoped money action must name an existing frozen contract "
                "line item; no line-level ceiling can be inferred from the "
                "basket total."
            ),
        ))
    line_ceiling = (
        line_item_amount_paise(contract, line_item_id)
        if line_item_id is not None
        else None
    )
    if line_item_id is not None and line_ceiling is None:
        return _finish(proposal, contract_id, _make_decision(
            decision="DENY",
            policy_ids=[P_REFUND_BOUNDS],
            reason_codes=["LINE_CEILING_UNAVAILABLE"],
            explanation=(
                f"Frozen line {line_item_id} has no positive amount_paise; "
                "the basket total cannot be used as a refund ceiling."
            ),
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

    line_refunded = 0
    if line_item_id is not None:
        line_refunded = _refunded_so_far_paise(
            contract.get("razorpay_payment_id"), line_item_id
        )
        assert line_ceiling is not None
        line_remaining = line_ceiling - line_refunded
        if amount > line_ceiling:
            return _finish(proposal, contract_id, _make_decision(
                decision="DENY",
                policy_ids=[P_REFUND_BOUNDS],
                reason_codes=["AMOUNT_EXCEEDS_LINE_CEILING"],
                explanation=(
                    f"Scoped refund {_fmt_inr(amount)} exceeds frozen line "
                    f"{line_item_id} ceiling {_fmt_inr(line_ceiling)}."
                ),
            ))
        if line_remaining <= 0 or amount > line_remaining:
            return _finish(proposal, contract_id, _make_decision(
                decision="DENY",
                policy_ids=[P_REFUND_BOUNDS],
                reason_codes=["LINE_REFUND_BALANCE_EXCEEDED"],
                explanation=(
                    f"Line {line_item_id} has only {_fmt_inr(max(line_remaining, 0))} "
                    f"of refundable balance after {_fmt_inr(line_refunded)} already "
                    "attributed refunds."
                ),
            ))

    if action_type == "refund_full":
        if line_item_id is not None:
            return _finish(proposal, contract_id, _make_decision(
                decision="DENY",
                policy_ids=[P_REFUND_BOUNDS],
                reason_codes=["LINE_SCOPED_REFUND_MUST_USE_PARTIAL"],
                explanation=(
                    "A complete refund for one basket line is a payment-level "
                    "refund_partial action with explicit line scope."
                ),
            ))
        # K-01 (case-closure fraud): a "full" refund below the captured amount
        # would close the case while under-refunding the buyer, silently
        # bypassing the partial-refund reason list and its auto cap. A full
        # refund IS the captured amount — anything less is a partial refund
        # and must travel through that typed path instead.
        if amount != captured:
            return _finish(proposal, contract_id, _make_decision(
                decision="DENY",
                policy_ids=[P_REFUND_FULL],
                reason_codes=["FULL_REFUND_AMOUNT_MISMATCH"],
                explanation=(
                    f"A full refund must be exactly the captured amount "
                    f"({_fmt_inr(captured)}); got {_fmt_inr(amount)}. For a "
                    f"smaller compensation use refund_partial with an allowed "
                    f"partial-refund reason."
                ),
            ))
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
    allowed_full: list[str] = list(policy["refund"]["full_refund"]["allowed_reasons"])

    # A full refund of one basket line deliberately travels as a payment-level
    # partial refund.  Its amount and reason still receive the full-refund
    # policy treatment, while the line ceiling prevents basket-wide refunds.
    if line_item_id is not None and reason in allowed_full:
        assert line_ceiling is not None
        if amount != line_ceiling:
            return _finish(proposal, contract_id, _make_decision(
                decision="DENY",
                policy_ids=[P_REFUND_FULL, P_REFUND_BOUNDS],
                reason_codes=["FULL_LINE_REFUND_AMOUNT_MISMATCH", reason],
                explanation=(
                    f"A full refund for line {line_item_id} must equal its "
                    f"frozen amount {_fmt_inr(line_ceiling)}; smaller amounts "
                    "must use an allowed partial-refund reason."
                ),
            ))
        threshold = int(policy["refund"]["full_refund"]["require_human_approval_above_paise"])
        if amount > threshold:
            return _finish(proposal, contract_id, _make_decision(
                decision="REQUIRE_APPROVAL",
                policy_ids=[P_REFUND_FULL_AUTO],
                reason_codes=["FULL_LINE_REFUND_ABOVE_HUMAN_APPROVAL_THRESHOLD", reason],
                explanation=(
                    f"Full refund of line {line_item_id} for {_fmt_inr(amount)} "
                    f"exceeds the {_fmt_inr(threshold)} autonomous limit; human "
                    "approval is required."
                ),
            ))
        return _finish(proposal, contract_id, _make_decision(
            decision="ALLOW",
            policy_ids=[P_REFUND_FULL, P_REFUND_BOUNDS, P_REFUND_FULL_AUTO],
            reason_codes=["WITHIN_POLICY_LIMITS", reason],
            explanation=(
                f"AUTO-APPROVED BY POLICY {P_REFUND_FULL_AUTO}: full refund of "
                f"line {line_item_id} for {_fmt_inr(amount)} is within the "
                f"autonomous limit of {_fmt_inr(threshold)}."
            ),
        ))

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


def _first_breach(
    contract_id: str, line_item_id: str | None = None
) -> dict[str, Any] | None:
    breaches = records_for_scope(
        STORE.find("breach", contract_id=contract_id), line_item_id
    )
    return breaches[0] if breaches else None


def _require_executable_remedy(prop: dict[str, Any]) -> None:
    """Fail closed unless ``prop`` is the planner's active top choice.

    The planner persists rejected and ranked-lower siblings so the buyer can
    inspect why they lost. Those records are not alternate authorization
    tokens: allowing a caller to submit one directly would let it bypass the
    deterministic remedy choice and, for a refund sibling, potentially move
    money. Directly seeded single proposals remain valid for isolated domain
    use when no ranking metadata exists.
    """
    rejected_reason = prop.get("rejected_reason")
    if rejected_reason:
        raise ValueError(
            f"remedy proposal is not executable: rejected_reason={rejected_reason}"
        )

    rank = prop.get("rank")
    if rank is not None and rank != 1:
        raise ValueError("only the rank-1 remedy proposal is executable")

    contract_id = prop.get("contract_id")
    line_item_id = record_line_id(prop)
    siblings = STORE.find("remedy", contract_id=contract_id) if contract_id else []
    siblings = [s for s in siblings if record_line_id(s) == line_item_id]
    active = [s for s in siblings if not s.get("rejected_reason")]
    rank_one = [s for s in active if s.get("rank") == 1]
    if rank_one and rank_one[0].get("id") != prop.get("id"):
        raise ValueError("only the selected rank-1 remedy proposal is executable")
    if not rank_one and len(active) > 1:
        raise ValueError("remedy choice is ambiguous; re-plan before execution")


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
    _require_executable_remedy(prop)
    contract = STORE.get(prop.get("contract_id") or "")
    if contract is None:
        raise KeyError(f"contract {prop.get('contract_id')} not found")

    action_type = _MONEY_TYPE_BY_REMEDY.get(prop.get("remedy_type") or "")
    if action_type is None:
        raise ValueError(
            f"remedy type {prop.get('remedy_type')!r} carries no money action"
        )

    prop_line_item_id = record_line_id(prop)
    line_item_id = prop_line_item_id
    breach = (
        STORE.get(prop.get("breach_id") or "") if prop.get("breach_id") else None
    ) or _first_breach(contract["id"], prop_line_item_id)
    if breach is not None and breach.get("contract_id") != contract["id"]:
        raise ValueError("remedy breach belongs to a different contract")
    breach_line_item_id = record_line_id(breach or {})
    if line_item_id is not None and breach_line_item_id not in {None, line_item_id}:
        raise ValueError("remedy line_item_id does not match its breach")
    if line_item_id is None and breach_line_item_id is not None:
        line_item_id = breach_line_item_id
    if line_item_id is not None and line_item_id not in contract_line_ids(contract):
        raise ValueError("remedy names a line item that is not frozen on the contract")

    # A complete refund of one line is intentionally a payment-level partial
    # refund. The proposal keeps its semantic refund_full type for the rights
    # graph, while the money action tells Razorpay only the line amount.
    if prop.get("remedy_type") == "refund_full" and line_item_id is not None:
        action_type = "refund_partial"
    reason = normalize_reason_code(breach.get("reason_code")) if breach else prop.get("remedy_type")

    amount = prop.get("amount_paise")
    if amount is None:
        amount = line_item_amount_paise(contract, line_item_id) or 0
    if not isinstance(amount, int) or isinstance(amount, bool):
        # Keep malformed values visible to the policy engine instead of
        # coercing them into money.
        amount = amount

    evidence_ids = list(prop.get("evidence_ids") or [])
    if not evidence_ids and breach:
        evidence_ids = [
            eid for eid in (breach.get("observed_fact_id"), breach.get("promise_id")) if eid
        ]

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
        "line_item_id": line_item_id,
        "affected_breach_ids": list(
            prop.get("affected_breach_ids")
            or ([breach["id"]] if breach and breach.get("id") else [])
        ),
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
        # Reuse preserves the originally built fields (reason, amount, target
        # payment): mutating them post-hoc would undermine the audit trail and
        # any prior evaluation bound to this exact proposal.
        return STORE.update(existing["id"], status="proposed") or existing

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


# Remedy execution legitimately walks the breach->remedy family only. A
# general BFS over the whole machine would happily route an unauthorized
# contract through PAYMENT_PENDING/PAID to reach REMEDIATED — teleporting
# unpaid purchases through the payment spine (review finding).
_REMEDY_WALK_STATES = {
    "BREACH_DETECTED",
    "REMEDY_PLANNING",
    "AWAITING_REMEDY_APPROVAL",
    "REMEDY_EXECUTING",
}


def _walk_path(current: str, target: str) -> list[str]:
    """Shortest legal path RESTRICTED to the remedy lifecycle subgraph.

    The walker never crosses payment-spine states (PAID and its legal
    predecessors). If current/target live outside the remedy family, the
    caller must use plain validate_transition instead.
    """
    from project_dante.domain.state_machine import TRANSITIONS, InvalidTransition

    if current == target:
        return []
    allowed = _REMEDY_WALK_STATES | {target} if target == "REMEDIATED" else _REMEDY_WALK_STATES
    if current not in allowed or target not in allowed:
        raise InvalidTransition(current, target)
    q: deque[list[str]] = deque([[current]])
    seen = {current}
    while q:
        path = q.popleft()
        for nxt in TRANSITIONS.get(path[-1], set()):
            if nxt not in allowed:
                continue
            npath = path + [nxt]
            if nxt == target:
                return npath[1:]
            if nxt not in seen:
                seen.add(nxt)
                q.append(npath)
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


def _create_refund(
    payment_id: str, amount_paise: int, idempotency_key: str, notes: dict | None
) -> dict:
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


def _actionable_breach(breach: dict[str, Any]) -> bool:
    return bool(
        breach.get("reason_code") in {
            "WRONG_SKU",
            "SKU_MISMATCH",
            "REGION_MISMATCH",
            "WARRANTY_REGION_MISMATCH",
            "WARRANTY_TYPE_MISMATCH",
            "VARIANT_MISMATCH",
            "MATERIAL_VARIANT_MISMATCH",
            "MATERIALLY_NOT_AS_DESCRIBED",
            "NOT_AS_DESCRIBED",
            "DELIVERY_SLA_MISS",
            "DELIVERY_SLA_MINOR",
            "LATE_DELIVERY",
        }
        or breach.get("severity") in {"material", "critical"}
    )


def _resolved_breach_ids(contract_id: str) -> set[str]:
    """Return breach ids covered by an executed local money action."""
    resolved: set[str] = set()
    for action in STORE.find("money_action", contract_id=contract_id):
        if action.get("status") != "executed":
            continue
        refs = action.get("affected_breach_ids") or []
        if not isinstance(refs, list):
            refs = []
        if not refs:
            remedy = STORE.get(action.get("remedy_proposal_id") or "") or {}
            refs = remedy.get("affected_breach_ids") or []
            if not refs and remedy.get("breach_id"):
                refs = [remedy["breach_id"]]
        resolved.update(str(ref) for ref in refs if isinstance(ref, str) and ref)
    return resolved


def _all_actionable_breaches_resolved(contract_id: str) -> bool:
    """Check resolution across every breached line, not just one proposal."""
    contract = STORE.get(contract_id)
    if contract is None:
        return False
    payment_id = contract.get("razorpay_payment_id")
    refunded_total, refunded_by_line = _refund_totals(payment_id)
    # A refund webhook may be reconciled by the frozen order before the
    # capture webhook binds its payment id.  In that window the contract's
    # persisted financial totals are the only trustworthy aggregate source.
    persisted_total = contract.get("refunded_amount_paise")
    if isinstance(persisted_total, int) and persisted_total >= 0:
        refunded_total = max(refunded_total, persisted_total)
    persisted_by_line = contract.get("refunded_line_amounts_paise")
    if isinstance(persisted_by_line, dict):
        for line_item_id, amount in persisted_by_line.items():
            if isinstance(line_item_id, str) and isinstance(amount, int) and amount >= 0:
                refunded_by_line[line_item_id] = max(
                    refunded_by_line.get(line_item_id, 0), amount
                )
    breaches = [
        breach
        for breach in STORE.find("breach", contract_id=contract_id)
        if _actionable_breach(breach)
    ]
    if not breaches:
        # Preserve the historical out-of-band full-refund reconciliation for
        # a contract whose breach records have not arrived yet.
        contract_ceiling = line_item_amount_paise(contract, None)
        return contract_ceiling is not None and refunded_total >= contract_ceiling

    resolved_ids = _resolved_breach_ids(contract_id)
    contract_ceiling = line_item_amount_paise(contract, None)
    for breach in breaches:
        breach_id = breach.get("id")
        if isinstance(breach_id, str) and breach_id in resolved_ids:
            continue
        line_item_id = record_line_id(breach)
        if line_item_id is None:
            if contract_ceiling is not None and refunded_total >= contract_ceiling:
                continue
            return False
        line_ceiling = line_item_amount_paise(contract, line_item_id)
        if (
            line_ceiling is not None
            and refunded_by_line.get(line_item_id, 0) >= line_ceiling
        ):
            continue
        return False
    return True


def reconcile_contract_lifecycle(contract_id: str) -> bool:
    """Close only when all actionable breaches have been resolved.

    Returns whether the contract is now fully remediated. This is shared by
    the local executor and refund webhook reconciliation, including out-of-
    band dashboard refunds.
    """
    contract = STORE.get(contract_id)
    if contract is None:
        return False
    resolved = _all_actionable_breaches_resolved(contract_id)
    current = str(contract.get("status") or "")
    if resolved and current in _BREACH_FAMILY_STATUSES:
        _transition_contract(contract_id, "REMEDIATED")
    elif not resolved and current in _BREACH_FAMILY_STATUSES and current != "BREACH_DETECTED":
        _transition_contract(contract_id, "BREACH_DETECTED")
    return resolved


def _executor_structural_check(ma: dict[str, Any]) -> tuple[bool, str]:
    """Final amount, scope, breach, payment, and policy validation.

    This check intentionally reads every source again immediately before the
    gateway call.  The earlier policy decision is not an authorization token
    for a different line, amount, payment, or breach.
    """
    idem = ma.get("idempotency_key")
    if not isinstance(idem, str) or not idem.strip():
        return False, "money action has no non-empty idempotency_key (replay safety)"
    contract = STORE.get(ma["contract_id"])
    if contract is None:
        return False, "contract vanished between evaluation and execution"
    raw_line_item_id = ma.get("line_item_id")
    if raw_line_item_id is not None and (
        not isinstance(raw_line_item_id, str) or not raw_line_item_id
    ):
        return False, "money action has an invalid line_item_id"
    line_item_id = record_line_id(ma)
    if line_item_id is not None and line_item_id not in contract_line_ids(contract):
        return False, "money action line_item_id is not on the frozen contract"

    remedy = STORE.get(ma.get("remedy_proposal_id") or "")
    if remedy is not None:
        if remedy.get("contract_id") != contract["id"]:
            return False, "remedy proposal belongs to a different contract"
        if record_line_id(remedy) != line_item_id:
            return False, "money action line scope drifted from its remedy"
    affected_breach_ids = list(
        ma.get("affected_breach_ids")
        or (remedy or {}).get("affected_breach_ids")
        or (
            [remedy.get("breach_id")]
            if remedy and remedy.get("breach_id")
            else []
        )
    )
    if not affected_breach_ids and line_item_id is None:
        # Compatibility for pre-line-scoping single-item money actions.
        affected_breach_ids = [
            breach["id"]
            for breach in records_for_scope(
                STORE.find("breach", contract_id=contract["id"]), None
            )
            if breach.get("id")
        ][:1]
    scoped_breaches = [
        breach
        for breach in STORE.find("breach", contract_id=contract["id"])
        if breach.get("id") in affected_breach_ids
        and record_line_id(breach) == line_item_id
    ]
    if not scoped_breaches:
        return False, "money action has no current breach in its exact line scope"
    if len(scoped_breaches) != len(set(affected_breach_ids)):
        return False, "money action references a missing or cross-line breach"
    if not contract.get("razorpay_payment_id"):
        return False, "contract has no captured razorpay_payment_id"
    if ma.get("razorpay_payment_id") != contract.get("razorpay_payment_id"):
        return False, "target payment changed after the policy decision"
    amount = ma.get("amount_paise")
    if isinstance(amount, bool) or not isinstance(amount, int):
        return False, f"amount type {type(amount).__name__} is not integer paise"
    if amount <= 0:
        return False, f"amount {amount} is not positive"
    captured = _captured_amount_paise(contract)
    if captured is None:
        return False, "no captured amount on record for this contract"
    if amount > captured:
        return False, f"amount {amount} exceeds captured amount {captured}"
    line_ceiling = (
        line_item_amount_paise(contract, line_item_id)
        if line_item_id is not None
        else None
    )
    if line_item_id is not None:
        if line_ceiling is None:
            return False, "frozen line has no positive amount_paise ceiling"
        if amount > line_ceiling:
            return False, (
                f"amount {amount} exceeds frozen line {line_item_id} ceiling "
                f"{line_ceiling}"
            )
        allowed_full = set(load_policy()["refund"]["full_refund"]["allowed_reasons"])
        reason = normalize_reason_code(ma.get("reason_code"))
        if ma.get("type") == "refund_full":
            return False, "line-scoped full refunds must use refund_partial"
        if reason in allowed_full and amount != line_ceiling:
            return False, (
                f"full refund reason for line {line_item_id} must equal its "
                f"frozen ceiling {line_ceiling}"
            )
    # Refund-stacking guard (review finding): bound every refund by the
    # REMAINING refundable balance — prior refunds on this payment count
    # against the ceiling, so full+partial stacks can never exceed captured.
    refunded, refunded_by_line = _refund_totals(ma.get("razorpay_payment_id"))
    remaining = captured - refunded
    if remaining <= 0:
        return False, (
            f"payment already fully refunded ({refunded}/{captured} paise); "
            "no refundable balance remains"
        )
    if amount > remaining:
        return False, (
            f"amount {amount} exceeds remaining refundable balance {remaining} "
            f"(captured {captured}, already refunded {refunded})"
        )
    if line_item_id is not None:
        line_refunded = refunded_by_line.get(line_item_id, 0)
        line_remaining = line_ceiling - line_refunded  # type: ignore[operator]
        if line_remaining <= 0 or amount > line_remaining:
            return False, (
                f"amount {amount} exceeds remaining frozen line balance "
                f"{line_remaining} after {line_refunded} already attributed"
            )
    # K-01 executor mirror: a full refund must still be FULL at call time —
    # a downward tamper after evaluation must not close the case short.
    if ma.get("type") == "refund_full" and amount != captured:
        return False, (
            f"full refund amount {amount} != captured amount {captured}; "
            f"downward drift is not permitted on a full refund"
        )
    if ma.get("policy_snapshot_hash") and ma["policy_snapshot_hash"] != policy_snapshot_hash():
        return False, "merchant policy changed after the money action was built"
    return True, ""


def _executor_final_check(ma: dict[str, Any], decision: dict[str, Any]) -> tuple[bool, str]:
    """THE FINAL EXECUTOR CHECK — run immediately before the Razorpay call.

    Re-reads the contract from the store and re-validates everything the
    earlier policy decision relied on (plan §15.2). Anything drifted => no call.
    """
    if decision.get("decision") != "ALLOW":
        return False, f"policy decision is {decision.get('decision')}, not ALLOW"

    return _executor_structural_check(ma)


def execute_remedy(proposal_id: str) -> dict[str, Any]:
    """THE GATED PIPELINE: RemedyProposal -> policy -> final check -> refund.

    Returns ``{decision, money_action, refund, executed}``. Idempotent: a
    repeated call for an already-executed remedy returns the original result
    without a second Razorpay call.
    """
    prop = STORE.get(proposal_id)
    if not prop or prop.get("_type") != "remedy":
        raise KeyError(f"remedy proposal {proposal_id} not found")
    _require_executable_remedy(prop)

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
    _require_executable_remedy(prop)

    idem = f"project-dante:{prop.get('contract_id')}:{prop['id']}:v1"
    existing = STORE.find_one("money_action", idempotency_key=idem)
    if existing and existing.get("status") == "executed":
        return {"money_action": existing}

    ma = existing or build_money_action_for_remedy(proposal_id)
    if ma.get("status") == "denied":
        raise ValueError("a denied money action cannot be approved")

    # APPROVAL GATE (review finding: fabricated HUMAN_APPROVED): a human may
    # only approve a money action for which the policy engine actually
    # returned REQUIRE_APPROVAL. The recorded decision must match this
    # proposal's idempotency key, carry decision=REQUIRE_APPROVAL, and bind
    # the SAME amount being executed. Without that record, /approve is a
    # no-op — callers must go through evaluate_money_action first.
    decisions = [
        d
        for d in STORE.find("policy_decision", idempotency_key=idem)
        if d.get("decision") == "REQUIRE_APPROVAL"
    ]
    if not decisions:
        raise ValueError(
            "no pending REQUIRE_APPROVAL policy decision for this proposal; "
            "run POST /api/remedies/{id}/policy first"
        )
    # The LATEST recorded verdict for this action is authoritative: a forged,
    # stale, or superseded approval must never authorize execution.
    latest = max(
        STORE.find("policy_decision", idempotency_key=idem),
        key=lambda d: d.get("evaluated_at") or "",
    )
    if latest.get("decision") != "REQUIRE_APPROVAL" or latest["id"] not in {
        d["id"] for d in decisions
    }:
        raise ValueError(
            "the latest policy verdict for this action is no longer "
            "REQUIRE_APPROVAL; re-evaluate and request approval again"
        )
    if int(latest.get("amount_paise") or -1) != int(ma.get("amount_paise") or -2):
        raise ValueError(
            "recorded approval decision amount does not match the proposed "
            "amount; re-evaluate policy and request approval again"
        )

    # Re-evaluate policy NOW: if policy state changed since REQUIRE_APPROVAL
    # was issued (contract mutated, threshold config edited), the stale
    # approval must not execute.
    recheck = evaluate_money_action({**ma, "idempotency_key": idem})
    if recheck["decision"] == "DENY":
        raise ValueError(
            f"policy now denies this action ({','.join(recheck['reason_codes'])}); "
            "approval voided"
        )

    contract = STORE.get(ma["contract_id"])
    if contract is None:
        raise KeyError(f"contract {ma['contract_id']} not found")
    if not contract.get("razorpay_payment_id"):
        raise ValueError("contract has no captured razorpay_payment_id")

    from project_dante.domain.state_machine import validate_transition

    ok, why = _executor_structural_check(ma)
    if not ok:
        STORE.update(ma["id"], status="failed")
        raise ValueError(f"execution failed structural check: {why}")

    # Approval is the explicit human gate; walk into REMEDY_EXECUTING.
    validate_transition(contract["status"], "REMEDY_EXECUTING")
    _transition_contract(ma["contract_id"], "REMEDY_EXECUTING")

    # Approved execution: pass an explicit ALLOW verdict into the pipeline so
    # the final check gates on structure/drift only — the human already gated
    # the amount threshold by approving. The decision dict still carries the
    # original policy citation for the audit trail.
    approved_decision = {
        "decision": "ALLOW",
        "policy_ids": ["P-REFUND-03"],
        "reason_codes": ["HUMAN_APPROVED"],
        "explanation": "Executed after explicit human approval of the threshold-gated refund.",
        "evaluated_at": now_iso(),
        "policy_snapshot_hash": ma.get("policy_snapshot_hash") or policy_snapshot_hash(),
    }
    executed_ma, refund, err = _execute_allowed(ma, approved_decision)
    if err:
        raise ValueError(f"execution failed final check: {err}")
    return {"money_action": executed_ma, "refund": refund}


def _execute_allowed(
    ma: dict[str, Any], decision: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None, str | None]:
    """Execute an ALLOW-decided money action with line-aware reconciliation.

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
        with contextlib.suppress(Exception):  # already in a breached state
            _transition_contract(contract_id, "BREACH_DETECTED")
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

    refund_notes = {
        "contract_id": contract_id,
        "remedy_proposal_id": ma.get("remedy_proposal_id") or "",
        "reason_code": ma.get("reason_code") or "",
        "source": "project-dante",
    }
    if record_line_id(ma) is not None:
        refund_notes["line_item_id"] = record_line_id(ma) or ""
    if isinstance(ma.get("affected_breach_ids"), list):
        refund_notes["affected_breach_ids"] = ",".join(
            str(value) for value in ma["affected_breach_ids"] if value
        )

    try:
        refund = _create_refund(
            payment_id=ma["razorpay_payment_id"],
            amount_paise=int(ma["amount_paise"]),
            idempotency_key=ma["idempotency_key"],
            notes=refund_notes,
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
        with contextlib.suppress(Exception):
            _transition_contract(contract_id, "BREACH_DETECTED")
        return updated or ma, None, str(exc)

    refund = dict(refund)
    refund_id = refund.get("id") or refund.get("refund_id") or ""
    refund_amount = refund.get("amount_paise", refund.get("amount"))
    if isinstance(refund_amount, int) and not isinstance(refund_amount, bool):
        # Keep the adapter's wire ``amount`` while exposing one normalized
        # amount field to callers and the local reconciliation ledger.
        refund["amount_paise"] = refund_amount
    line_item_id = record_line_id(ma)
    refund["contract_id"] = contract_id
    if line_item_id is not None:
        refund["line_item_id"] = line_item_id
    if isinstance(ma.get("affected_breach_ids"), list):
        refund["affected_breach_ids"] = list(ma["affected_breach_ids"])
    # Both adapters normally persist the provider response. Promote the
    # trusted, already-validated line scope onto that local ledger record so
    # later executions can calculate the exact per-line balance. If a test or
    # alternate adapter only returns a response, persist the same durable
    # reconciliation record here.
    if refund_id:
        stored_refund = STORE.get(str(refund_id))
        if stored_refund is None:
            STORE.put({"_type": "razorpay_refund", **refund})
        else:
            STORE.update(
                str(refund_id),
                contract_id=contract_id,
                **(
                    {"line_item_id": line_item_id}
                    if line_item_id is not None
                    else {}
                ),
                **(
                    {"affected_breach_ids": list(ma["affected_breach_ids"])}
                    if isinstance(ma.get("affected_breach_ids"), list)
                    else {}
                ),
                **(
                    {"amount_paise": refund_amount}
                    if isinstance(refund_amount, int)
                    and not isinstance(refund_amount, bool)
                    else {}
                ),
            )
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
            "line_item_id": line_item_id,
            "affected_breach_ids": ma.get("affected_breach_ids") or [],
            "sandbox": bool(refund.get("sandbox")),
            "mode": rzp_mode(),
        },
        correlation_id=contract_id,
        causation_id=ma.get("remedy_proposal_id"),
    )

    fully_resolved = reconcile_contract_lifecycle(contract_id)
    if fully_resolved:
        append_event(
            aggregate_type="contract",
            aggregate_id=contract_id,
            event_type="CONTRACT_REMEDIATED",
            payload={
                "refund_id": refund_id,
                "money_action_id": ma["id"],
                "remedy_proposal_id": ma.get("remedy_proposal_id"),
                "line_item_id": line_item_id,
                "affected_breach_ids": ma.get("affected_breach_ids") or [],
                "amount_paise": ma["amount_paise"],
            },
            correlation_id=contract_id,
            causation_id=ma.get("remedy_proposal_id"),
        )
    else:
        append_event(
            aggregate_type="contract",
            aggregate_id=contract_id,
            event_type="STATE_RECONCILED",
            payload={
                "reason": "line_remedy_executed_other_breaches_open",
                "refund_id": refund_id,
                "money_action_id": ma["id"],
                "line_item_id": line_item_id,
                "affected_breach_ids": ma.get("affected_breach_ids") or [],
                "action": "remain_breach_detected",
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
