"""Outcome / Promise verifier (plan §5 Problem C, §20).

Deterministic comparison of observed facts against the frozen MATERIAL
promise set. No LLM here: breach detection must be auditable and replayable.
"""

from __future__ import annotations

from typing import Any

from project_dante.db.store import STORE
from project_dante.domain.events import append_event, new_id, now_iso
from project_dante.domain.promises.pipeline import (
    CONSTRAINT_TO_PROMISE,
    normalize_value,
    parse_dt,
)
from project_dante.domain.state_machine import validate_transition
from project_dante.domain.types import Breach

# observed-fact key -> promise key it speaks to
_FACT_TO_PROMISE = {
    "warranty.type": "warranty.type",
    "warranty.region": "warranty.region",
    "product.region": "product.region",
    "condition": "condition",
    "price.amount_paise": "price.amount_paise",
    "unit_amount_paise": "price.amount_paise",  # raw-offer-style fact alias
    "payment.amount_paise": "price.amount_paise",  # captured-payment fact
    "amount_paid_paise": "price.amount_paise",
    "delivery.delivered_date": "delivery.promised_by_date",
    "delivery.actual_date": "delivery.promised_by_date",
}

# Promise keys other modules historically used for the delivery deadline.
_PROMISE_KEY_ALIASES = {
    "delivery.latest": "delivery.promised_by_date",
}

# Mismatch on these keys => material severity, MATERIAL_VARIANT_MISMATCH.
_VARIANT_KEYS = {"warranty.type", "warranty.region", "product.region"}

# Promise keys a fulfillment observation CAN speak to. Satisfaction is judged
# only over material promises in this set — e.g. "category" is material to
# intent but has no post-purchase observation, so demanding a fact for it
# would make SATISFIED unreachable.
_OBSERVABLE_KEYS = set(_FACT_TO_PROMISE.values())

# Late-delivery thresholds: <=24h late is minor, beyond that material.
_MINOR_LATE_MAX_HOURS = 24.0


def _breach_from_record(record: dict[str, Any]) -> Breach:
    """Rehydrate a stored breach record, dropping store-internal fields."""
    return Breach.model_validate({k: v for k, v in record.items() if not k.startswith("_")})


def _latest_fact(facts: list[dict[str, Any]], promise_key: str) -> dict[str, Any] | None:
    """Latest observed fact whose key maps onto the given promise key."""
    candidates = [f for f in facts if _FACT_TO_PROMISE.get(f.get("key")) == promise_key]
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.get("observed_at") or "")


def _canonical_promise_key(key: str) -> str:
    """Map historical/alternate promise keys onto canonical ones."""
    return _PROMISE_KEY_ALIASES.get(key, key)


def _make_breach(
    promise: dict[str, Any],
    fact: dict[str, Any],
    severity: str,
    reason_code: str,
    explanation: str,
) -> Breach:
    return Breach(
        id=new_id("br_"),
        contract_id=fact["contract_id"],
        promise_id=promise["id"],
        observed_fact_id=fact["id"],
        severity=severity,  # type: ignore[arg-type]
        reason_code=reason_code,
        explanation=explanation,
        detected_at=now_iso(),
    )


def _promise_deadline(promise: dict[str, Any]) -> Any:
    """Deadline datetime for a delivery promise.

    A date-only promise ("2026-08-27") means end of that day, so a delivery
    at any time ON the promised date is on time.
    """
    raw = str(promise.get("value") or "").strip()
    dt = parse_dt(raw) or parse_dt(promise.get("normalized_value"))
    if dt is None:
        return None
    if len(raw) == 10:  # YYYY-MM-DD
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return dt


def _late_delivery_breach(promise: dict[str, Any], fact: dict[str, Any]) -> Breach | None:
    """DELIVERY_SLA_MISS: minor when <=24h late, material beyond that."""
    promised = _promise_deadline(promise)
    delivered = parse_dt(fact.get("value"))
    if promised is None or delivered is None:
        return None
    if delivered <= promised:
        return None
    hours_late = (delivered - promised).total_seconds() / 3600.0
    severity = "minor" if hours_late <= _MINOR_LATE_MAX_HOURS else "material"
    explanation = (
        f"Delivered {delivered.isoformat()} vs promised by {promised.isoformat()} "
        f"({hours_late:.1f}h late)"
    )
    return _make_breach(promise, fact, severity, "DELIVERY_SLA_MISS", explanation)


def _compare_pair(promise: dict[str, Any], fact: dict[str, Any]) -> Breach | None:
    """Compare one material promise against the latest matching observed fact."""
    pkey = promise["key"]

    if pkey == "delivery.promised_by_date":
        return _late_delivery_breach(promise, fact)

    pval = promise.get("normalized_value")
    fval = normalize_value(pkey, fact.get("value"))
    if fval == pval:
        return None

    if pkey in _VARIANT_KEYS:
        severity, reason = "material", "MATERIAL_VARIANT_MISMATCH"
    elif pkey == "condition":
        severity, reason = "critical", "CONDITION_MISMATCH"
    else:
        severity, reason = "minor", "PROMISE_MISMATCH"
    explanation = (
        f"Promised {pkey}={pval!r} but observed {fact.get('key')}="
        f"{fact.get('value')!r} (normalized {fval!r})"
    )
    return _make_breach(promise, fact, severity, reason, explanation)


def _evaluation_floor(contract_id: str) -> dict[str, bool]:
    """Selection-time critical-constraint map from Agent C's evaluation record.

    Keys whose offer satisfied a CRITICAL hard constraint at select-offer time
    (per STORE `_type=evaluation`) get a severity FLOOR of material on any
    verification mismatch — the buyer explicitly gated selection on them.
    """
    evals = [e for e in STORE.list("evaluation") if e.get("contract_id") == contract_id]
    if not evals:
        return {}
    floor: dict[str, bool] = {}
    for e in evals:
        hard_failures = e.get("hard_failures") or []
        # A key with NO recorded failure against a feasible offer satisfied its
        # constraint; if that constraint was critical, mismatches are material+.
        evaluated = {f.get("key") for f in hard_failures}
        constraints = e.get("constraints") or e.get("hard_constraints") or []
        for c in constraints:
            if not isinstance(c, dict) or not c.get("critical", True):
                continue
            ckey = CONSTRAINT_TO_PROMISE.get(c.get("key", ""))
            if ckey and ckey not in evaluated:
                floor[ckey] = True
    return floor


def evaluate_contract(contract_id: str) -> dict[str, Any]:
    """Verify all material promises against observed facts for a contract.

    Persists Breach records + PROMISE_BREACH_DETECTED events (idempotent per
    (contract, promise, fact)), transitions DELIVERED/VERIFYING ->
    SATISFIED | BREACH_DETECTED, and returns the verification result.
    Missing observations produce neither satisfaction nor breach.
    """
    contract = STORE.get(contract_id)
    if not contract:
        raise LookupError(f"Contract {contract_id} not found")

    promises = [
        p
        for p in STORE.list("promise")
        if p.get("contract_id") == contract_id and p.get("material_to_intent")
    ]
    facts = [f for f in STORE.list("fact") if f.get("contract_id") == contract_id]

    # Idempotency: never duplicate a breach for the same promise+fact pair.
    existing_pairs = {
        (b["promise_id"], b["observed_fact_id"])
        for b in STORE.list("breach")
        if b.get("contract_id") == contract_id
    }

    new_breaches: list[Breach] = []
    satisfied_keys: set[str] = set()
    severity_floor = _evaluation_floor(contract_id)

    for promise in promises:
        promise["key"] = _canonical_promise_key(promise["key"])
        fact = _latest_fact(facts, promise["key"])
        if fact is None:
            continue  # nothing observed yet: neither satisfied nor breached
        breach = _compare_pair(promise, fact)
        if breach is None:
            satisfied_keys.add(promise["key"])
            continue
        # Selection-time critical constraints floor mismatch severity at
        # material: the buyer explicitly gated selection on these keys.
        # DELIVERY_SLA_MISS is exempt — its minor/material split is the
        # documented compensation policy (<=24h late => minor, plan §8.3),
        # so a critical deadline escalates only beyond 24h.
        if (
            breach.severity == "minor"
            and breach.reason_code != "DELIVERY_SLA_MISS"
            and severity_floor.get(promise["key"])
        ):
            breach.severity = "material"
            breach.explanation += " (critical selection constraint — severity floor material)"
        if (breach.promise_id, breach.observed_fact_id) in existing_pairs:
            continue  # already recorded; stay idempotent
        STORE.put({**breach.model_dump(), "_type": "breach"})
        existing_pairs.add((breach.promise_id, breach.observed_fact_id))
        new_breaches.append(breach)
        append_event(
            aggregate_type="contract",
            aggregate_id=contract_id,
            event_type="PROMISE_BREACH_DETECTED",
            payload={
                "breach_id": breach.id,
                "promise_id": breach.promise_id,
                "observed_fact_id": breach.observed_fact_id,
                "severity": breach.severity,
                "reason_code": breach.reason_code,
                "explanation": breach.explanation,
                "synthetic": bool(fact.get("synthetic")),
                "scenario_id": fact.get("scenario_id"),
            },
            synthetic=bool(fact.get("synthetic")),
            scenario_id=fact.get("scenario_id"),
        )

    # All breaches ever recorded for this contract keep responses stable
    # across repeated verifications.
    stored_breaches = [
        b for b in STORE.list("breach") if b.get("contract_id") == contract_id
    ]
    all_breaches = sorted(
        (_breach_from_record(b) for b in stored_breaches),
        key=lambda b: b.detected_at or "",
    )

    # SATISFIED only when every OBSERVABLE material promise has a matching
    # observation. Material-but-unobservable keys (e.g. category) are out of
    # scope; missing observations leave the result inconclusive.
    verifiable = [
        {**p, "key": _canonical_promise_key(p["key"])}
        for p in promises
        if _canonical_promise_key(p["key"]) in _OBSERVABLE_KEYS
    ]
    observed = {p["key"] for p in verifiable if _latest_fact(facts, p["key"]) is not None}
    all_keys = {p["key"] for p in verifiable}
    satisfied = bool(verifiable) and observed == all_keys and len(satisfied_keys) == len(all_keys)

    status_target = (
        "SATISFIED" if satisfied else ("BREACH_DETECTED" if all_breaches else None)
    )

    if status_target:
        current_status = contract.get("status")
        try:
            validate_transition(current_status, "VERIFYING")
            STORE.update(contract_id, status="VERIFYING")
            current_status = "VERIFYING"
        except Exception:
            pass  # already VERIFYING, terminal, or allowed straight to target

        if current_status != status_target:
            try:
                validate_transition(current_status, status_target)  # type: ignore[arg-type]
                STORE.update(contract_id, status=status_target)
                if status_target == "SATISFIED":
                    append_event(
                        aggregate_type="contract",
                        aggregate_id=contract_id,
                        event_type="CONTRACT_SATISFIED",
                        payload={
                            "verified_promises": sorted(satisfied_keys),
                            "fact_count": len(facts),
                        },
                    )
            except Exception:
                pass  # illegal from this state; leave status untouched

    final_status = (STORE.get(contract_id) or {}).get("status")
    return {
        "breaches": [Breach.model_validate(b) for b in all_breaches],
        "new_breach_count": len(new_breaches),
        "satisfied": satisfied,
        "status_target": status_target or "INCONCLUSIVE",
        "status": final_status,
        "checked_promise_count": len(observed),
        "unobserved_material_keys": sorted(
            p["key"]
            for p in promises
            if p["key"] in _OBSERVABLE_KEYS and _latest_fact(facts, p["key"]) is None
        ),
    }
