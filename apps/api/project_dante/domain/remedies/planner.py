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
from project_dante.domain.line_items import (
    line_item_amount_paise,
    records_for_scope,
)
from project_dante.domain.rights.engine import (
    MATERIAL_REASONS,
    SLA_REASONS,
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


def _bounded_amount(raw: Any, fallback: int, ceiling: int) -> int:
    """Normalize a persisted remedy value without weakening type guarantees."""
    value = raw if isinstance(raw, int) and not isinstance(raw, bool) else fallback
    return min(value, ceiling)


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
        if b.get("reason_code") in MATERIAL_REASONS or b.get(
            "severity"
        ) in {"material", "critical"}:
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
    evidence = STORE.find("evidence", contract_id=contract_id)

    candidates: list[dict[str, Any]] = []

    for ent in entitlements:
        line_item_id = ent.get("line_item_id")
        scoped_promises = records_for_scope(promises, line_item_id)
        scoped_facts = records_for_scope(facts, line_item_id)
        scoped_breaches = records_for_scope(breaches, line_item_id)
        ctx = resolve_ctx(
            contract_id,
            scoped_promises,
            scoped_facts,
            scoped_breaches,
            line_item_id=line_item_id,
        )
        breach = _material_breach(scoped_breaches)
        line_ceiling = line_item_amount_paise(contract, line_item_id)
        if line_item_id is not None:
            if line_ceiling is None:
                # A scoped money action cannot safely fall back to the basket
                # total when its frozen line amount is absent.
                continue
            captured = line_ceiling
        else:
            captured = int(contract.get("amount_paise") or 0)
        if captured <= 0:
            # A scoped money action cannot safely fall back to the basket
            # total when its frozen line amount is absent.
            continue
        evidence_ids = [
            e["id"]
            for e in records_for_scope(evidence, line_item_id, allow_unscoped=True)
            if e.get("id")
        ]
        slug = ent.get("slug")
        status = ent.get("status")
        etype = ent.get("type")

        # Only eligible rights produce executable candidates — with ONE
        # exception: a replacement blocked purely by inventory still becomes a
        # visible, rejected candidate (replacement_inventory_unavailable) so
        # the UI can explain WHY the fallback won.
        inventory_blocked = (
            slug == "merchant_replacement"
            and status == "blocked"
            and ctx.get("replacement.available") is False
        )
        if status != "eligible" and not inventory_blocked:
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
            if inventory_blocked:
                expl = (
                    "Replacement would normally rank first, but the synthetic "
                    "merchant API reports zero matching inventory."
                )
        elif etype == "refund" or slug == "merchant_full_refund":
            rtype = "refund_full"
            hours = ent.get("estimated_resolution_hours") or 24.0
            value = _bounded_amount(ent.get("remedy_value_paise"), captured, captured)
            avail = ctx.get("replacement.available")
            attempted = ctx.get("replacement.attempted")
            why = (
                "replacement inventory is unavailable"
                if avail is False
                else (
                    "a replacement was already attempted"
                    if attempted
                    else "no valid replacement exists"
                )
            )
            expl = f"Full refund of the captured amount selected because {why}."
        elif slug == "merchant_partial_refund_delivery":
            rtype = "refund_partial"
            hours = ent.get("estimated_resolution_hours") or 6.0
            value = _bounded_amount(ent.get("remedy_value_paise"), 30000, captured)
            expl = (
                f"Partial compensation of {_fmt_inr(value)} for the delivery-SLA miss "
                f"(plan §8.3 merchant policy)."
            )
        else:
            continue

        relevant_breaches = [
            candidate_breach
            for candidate_breach in scoped_breaches
            if (
                candidate_breach.get("reason_code") in SLA_REASONS
                if rtype == "refund_partial"
                else (
                    candidate_breach.get("reason_code") in MATERIAL_REASONS
                    or candidate_breach.get("severity") in {"material", "critical"}
                )
            )
        ]
        primary_breach = _material_breach(relevant_breaches) or breach
        sc = score_remedy(rtype, value, captured, hours)
        candidates.append(
            {
                "entitlement": ent,
                "remedy_type": rtype,
                "value": value,
                "hours": hours,
                "explanation": expl,
                "line_item_id": line_item_id,
                "breach": primary_breach,
                "evidence_ids": evidence_ids,
                "replacement_available": ctx.get("replacement.available"),
                "affected_breach_ids": [
                    b["id"] for b in relevant_breaches if b.get("id")
                ],
                **sc,
            }
        )

    # ---- REPLACEMENT UNAVAILABLE + PER-LINE RANKING ----------------------
    # Ranking is independent for every line. A replacement for line A can be
    # rank 1 while a refund for line B is also rank 1; they are not siblings.
    groups: dict[str | None, list[dict[str, Any]]] = {}
    for candidate in candidates:
        groups.setdefault(candidate["line_item_id"], []).append(candidate)

    rank_by_id: dict[int, int] = {}
    rejected_by_id: dict[int, str] = {}
    for _line_item_id, group in groups.items():
        repl_available = group[0].get("replacement_available")
        ranked = []
        for candidate in group:
            if candidate["remedy_type"] == "replacement" and repl_available is False:
                rejected_by_id[id(candidate)] = "replacement_inventory_unavailable"
            else:
                ranked.append(candidate)
        ranked.sort(key=lambda c: (-c["score"], c["remedy_type"]))
        rank_by_id.update({id(candidate): i + 1 for i, candidate in enumerate(ranked)})

    proposals: list[dict[str, Any]] = []
    for c in candidates:
        ent = c["entitlement"]
        rejected = rejected_by_id.get(id(c))
        if rejected is None:
            pos = rank_by_id.get(id(c))
            rejected = "ranked_lower" if (pos is not None and pos > 1) else None
        rec = {
            "_type": "remedy",
            "id": new_id("rem"),
            "breach_id": (c["breach"] or {}).get("id"),
            "line_item_id": c["line_item_id"],
            "affected_breach_ids": c["affected_breach_ids"],
            "entitlement_id": ent["id"],
            "contract_id": contract_id,
            "remedy_type": c["remedy_type"],
            "amount_paise": int(c["value"]) or None,
            "expected_buyer_value": float(c["normalized_value"]),
            "estimated_time_hours": float(c["hours"]),
            "inconvenience_score": float(c["inconvenience"]),
            "confidence": round(min(1.0, max(0.0, c["score"] / 1.2)), 4),
            "evidence_ids": c["evidence_ids"],
            "explanation": c["explanation"],
            "rejected_reason": rejected,
            "rank": (
                rank_by_id.get(id(c))
                if rejected != "replacement_inventory_unavailable"
                else None
            ),
            "score_breakdown": {
                k: c[k]
                for k in (
                    "normalized_value",
                    "intent_restoration",
                    "speed",
                    "inconvenience",
                    "score",
                )
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
                    "line_item_id": p.get("line_item_id"),
                    "affected_breach_ids": p.get("affected_breach_ids") or [],
                    "remedy_type": p["remedy_type"],
                    "rank": p.get("rank"),
                    "rejected_reason": p.get("rejected_reason"),
                    "score": (p.get("score_breakdown") or {}).get("score"),
                }
                for p in proposals
            ],
            "reason_codes": sorted({b.get("reason_code", "") for b in breaches}),
        },
        causation_id=(proposals[0].get("breach_id") if proposals else None),
    )

    chosen_by_line = {
        str(p.get("line_item_id") or "__legacy__"): p
        for p in proposals
        if p.get("rejected_reason") is None and p.get("rank") == 1
    }
    return {
        "proposals": proposals,
        "chosen": top,
        "chosen_by_line": chosen_by_line,
    }


def get_proposals(contract_id: str) -> list[dict[str, Any]]:
    """Persisted proposals for a contract, ranked order (unranked last)."""
    props = STORE.find("remedy", contract_id=contract_id)
    return sorted(props, key=lambda r: (r.get("rank") is None, r.get("rank") or 999))
