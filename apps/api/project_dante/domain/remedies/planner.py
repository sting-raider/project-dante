"""Deterministic Remedy Planner (master plan §14).

The planner NEVER asks an LLM what sounds fair. It:
  1. runs the eligibility engine over current reality,
  2. maps eligible entitlements onto typed RemedyProposals,
  3. scores them with the fixed, visible function from plan §14.2,
  4. applies the REPLACEMENT-UNAVAILABLE rule (inventory false => replacement
     is rejected with reason, excluded from ranking),
  5. ranks the rest deterministically and persists: top proposal active
     (``rank=1``, no rejected_reason), the others carry ``rejected_reason``.

Explanation strings are generated from facts — auditable, replayable.
"""

from __future__ import annotations

from typing import Any

from project_dante.db.store import STORE
from project_dante.domain.events import append_event, new_id
from project_dante.domain.rights.engine import (
    MATERIAL_REASONS,
    evaluate_eligibility,
    get_breaches,
    resolve_ctx,
)

# Scoring weights — master plan §14.2 exactly.
W_VALUE = 0.40
W_INTENT = 0.35
W_SPEED = 0.15
W_INCONVENIENCE = -0.10

INTENT_RESTORATION = {
    "replacement": 1.0,
    "refund_full": 0.6,
    "refund_partial": 0.3,
}
INCONVENIENCE = {
    "replacement": 0.4,
    "refund_full": 0.1,
    "refund_partial": 0.05,
}


def _fmt_inr(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"


def _speed_score(hours: float | None) -> float:
    """1 / (1 + hours/24) per plan §14.2."""
    h = max(float(hours if hours is not None else 72), 0.0)
    return 1.0 / (1.0 + h / 24.0)


def score_remedy(
    remedy_type: str, value_paise: int, captured_paise: int, resolution_hours: float | None
) -> dict[str, float]:
    """The visible deterministic scorer (plan §14.2). Returns components + total."""
    norm_value = value_paise / max(captured_paise, 1)
    intent = INTENT_RESTORATION.get(remedy_type, 0.5)
    speed = _speed_score(resolution_hours)
    inconvenience = INCONVENIENCE.get(remedy_type, 0.2)
    total = (
        W_VALUE * norm_value
        + W_INTENT * intent
        + W_SPEED * speed
        + W_INCONVENIENCE * inconvenience
    )
    return {
        "normalized_value": round(norm_value, 6),
        "intent_restoration": intent,
        "speed": round(speed, 6),
        "inconvenience": inconvenience,
        "score": round(total, 6),
    }


def _get_contract(contract_id: str) -> dict[str, Any]:
    contract = STORE.get(contract_id)
    if contract is None:
        raise KeyError(f"contract {contract_id} not found")
    return contract


def _material_breach(breaches: list[dict[str, Any]]) -> dict[str, Any] | None:
    for b in breaches:
        if b.get("reason_code") in MATERIAL_REASONS or b.get("severity") in {"material", "critical"}:
            return b
    return breaches[0] if breaches else None


def plan_remedies(contract_id: str) -> dict[str, Any]:
    """Derive, score, rank, and persist remedy proposals for a contract.

    Returns ``{proposals: [...], chosen: {...}|None}``. Idempotent-ish: a
    fresh planning run supersedes previous proposals by re-planning only when
    none exist; existing proposals are returned as-is (they may already have
    money actions bound to their stable ids).
    """
    contract = _get_contract(contract_id)

    existing = sorted(
        STORE.find("remedy", contract_id=contract_id),
        key=lambda r: (r.get("rank") is None, r.get("rank") or 999),
    )
    if existing:
        chosen = next((r for r in existing if r.get("rejected_reason") is None), None)
        return {"proposals": existing, "chosen": chosen}

    entitlements = evaluate_eligibility(contract_id)
    promises = STORE.find("promise", contract_id=contract_id)
    facts = STORE.find("fact", contract_id=contract_id)
    breaches = get_breaches(contract_id)
    ctx = resolve_ctx(contract_id, promises, facts, breaches)

    breach = _material_breach(breaches)
    captured = int(contract.get("amount_paise") or 0)
    evidence_ids = [e["id"] for e in STORE.find("evidence", contract_id=contract_id)]

    candidates: list[dict[str, Any]] = []

    for ent in entitlements:
        slug = ent.get("slug")
        status = ent.get("status")
        etype = ent.get("type")

        # Only eligible rights produce executable candidates.
        if status != "eligible":
            continue

        if slug == "merchant_replacement":
            rtype = "replacement"
            hours = ent.get("estimated_resolution_hours") or 72.0
            value = captured
            expl = (
                "Replacement preserves the original purchase intent (the exact "
                "promised variant) with no buyer loss. Chosen first because the "
                "buyer wanted the product, not merely its price back."
            )
        elif etype == "refund" or slug == "merchant_full_refund":
            rtype = "refund_full"
            hours = ent.get("estimated_resolution_hours") or 24.0
            value = ent.get("remedy_value_paise") or captured
            avail = ctx.get("replacement.available")
            attempted = ctx.get("replacement.attempted")
            why = (
                "replacement inventory is unavailable"
                if avail is False
                else ("a replacement was already attempted" if attempted else "no valid replacement exists")
            )
            expl = f"Full refund of the captured amount selected because {why}."
        elif slug == "merchant_partial_refund_delivery":
            rtype = "refund_partial"
            hours = ent.get("estimated_resolution_hours") or 6.0
            value = int(ent.get("remedy_value_paise") or 30000)
            expl = (
                f"Partial compensation of {_fmt_inr(value)} for the delivery-SLA miss "
                f"(plan §8.3 merchant policy)."
            )
        else:
            continue

        sc = score_remedy(rtype, value, captured, hours)
        candidates.append(
            {
                "entitlement": ent,
                "remedy_type": rtype,
                "value": value,
                "hours": hours,
                "explanation": expl,
                **sc,
            }
        )

    # ---- REPLACEMENT UNAVAILABLE RULE ------------------------------------
    # (Handled upstream via eligibility marking replacement blocked when the
    # inventory fact is False; belt-and-braces here in case an eligible
    # replacement slips through with inventory explicitly False.)
    repl_available = ctx.get("replacement.available")
    ranked: list[dict[str, Any]] = []
    for c in candidates:
        if c["remedy_type"] == "replacement" and repl_available is False:
            c["rejected_reason"] = "replacement_inventory_unavailable"
        else:
            ranked.append(c)

    ranked.sort(key=lambda c: (-c["score"], c["remedy_type"]))

    proposals: list[dict[str, Any]] = []
    for i, c in enumerate(candidates):
        ent = c["entitlement"]
        rejected = c.get("rejected_reason")
        if rejected is None:
            rejected = "ranked_lower" if i > 0 else None
        rec = {
            "_type": "remedy",
            "id": new_id("rem"),
            "breach_id": (breach or {}).get("id"),
            "entitlement_id": ent["id"],
            "contract_id": contract_id,
            "remedy_type": c["remedy_type"],
            "amount_paise": int(c["value"]) or None,
            "expected_buyer_value": float(c["normalized_value"]),
            "estimated_time_hours": float(c["hours"]),
            "inconvenience_score": float(c["inconvenience"]),
            "confidence": round(min(1.0, max(0.0, c["score"] / 1.2)), 4),
            "evidence_ids": evidence_ids,
            "explanation": c["explanation"],
            "rejected_reason": rejected,
            "rank": None if rejected == "replacement_inventory_unavailable" else i + 1,
            "score_breakdown": {
                k: c[k] for k in ("normalized_value", "intent_restoration", "speed", "inconvenience", "score")
            },
            "synthetic": False,
        }
        STORE.put(rec)
        proposals.append(rec)

    top = next((p for p in proposals if p.get("rejected_reason") is None), None)

    append_event(
        aggregate_type="contract",
        aggregate_id=contract_id,
        event_type="REMEDY_PROPOSED",
        payload={
            "chosen": (top or {}).get("id"),
            "proposals": [
                {
                    "id": p["id"],
                    "remedy_type": p["remedy_type"],
                    "rank": p.get("rank"),
                    "rejected_reason": p.get("rejected_reason"),
                    "score": (p.get("score_breakdown") or {}).get("score"),
                }
                for p in proposals
            ],
            "reason_codes": sorted({b.get("reason_code", "") for b in breaches}),
        },
        causation_id=(breach or {}).get("id"),
    )

    return {"proposals": proposals, "chosen": top}


def get_proposals(contract_id: str) -> list[dict[str, Any]]:
    """Persisted proposals for a contract, ranked order (unranked last)."""
    props = STORE.find("remedy", contract_id=contract_id)
    return sorted(props, key=lambda r: (r.get("rank") is None, r.get("rank") or 999))
