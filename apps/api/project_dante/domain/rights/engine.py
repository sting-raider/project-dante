"""Purchase Rights Graph engine (master plan §13, §14.1).

Derives the entitlements a purchase creates, evaluates their eligibility
against current observed reality (breaches, replacement inventory, expiry,
evidence), and projects the whole thing as a graph for the UI.

All of it is deterministic derivation from STORE records — no LLM anywhere.
Entitlement statuses: dormant | eligible | active | consumed | expired |
invalid | blocked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from project_dante.db.store import STORE
from project_dante.domain.events import append_event

# ------------------------------------------------------------------ helpers


def _now() -> datetime:
    return datetime.now(UTC)


def _parse_ts(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _get_contract(contract_id: str) -> dict[str, Any]:
    contract = STORE.get(contract_id)
    if contract is None:
        raise KeyError(f"contract {contract_id} not found")
    return contract


def _promises(contract_id: str) -> list[dict[str, Any]]:
    return STORE.find("promise", contract_id=contract_id)


def _facts(contract_id: str) -> list[dict[str, Any]]:
    return STORE.find("fact", contract_id=contract_id)


def _breaches(contract_id: str) -> list[dict[str, Any]]:
    return STORE.find("breach", contract_id=contract_id)


def _evidence(contract_id: str) -> list[dict[str, Any]]:
    return STORE.find("evidence", contract_id=contract_id)


def _remedies(contract_id: str) -> list[dict[str, Any]]:
    return STORE.find("remedy", contract_id=contract_id)


def _fact_value(facts: list[dict[str, Any]], key: str) -> Any:
    """Latest observed fact value for a key (records are append-ordered)."""
    val = None
    for f in facts:
        if f.get("key") == key:
            val = f.get("value")
    return val


def _promise_value(promises: list[dict[str, Any]], key: str) -> Any:
    for p in promises:
        if p.get("key") == key:
            return p.get("value")
    return None


def _reason_codes(breaches: list[dict[str, Any]]) -> set[str]:
    return {b.get("reason_code") for b in breaches if b.get("reason_code")}


# Material-breach reason codes the demo/verifier emits. A rights set is only
# "activated" by a MATERIAL_VARIANT_MISMATCH-class breach; a minor SLA miss
# activates only the delivery-compensation right.
MATERIAL_REASONS = {
    "WRONG_SKU",
    "SKU_MISMATCH",
    "REGION_MISMATCH",
    "WARRANTY_REGION_MISMATCH",
    "WARRANTY_TYPE_MISMATCH",
    "VARIANT_MISMATCH",
    "MATERIAL_VARIANT_MISMATCH",
    "MATERIALLY_NOT_AS_DESCRIBED",
    "NOT_AS_DESCRIBED",
}
SLA_REASONS = {"DELIVERY_SLA_MISS", "DELIVERY_SLA_MINOR", "LATE_DELIVERY"}


# ------------------------------------------------------------- predicates


def _eval_predicate(pred: dict[str, Any], ctx: dict[str, Any]) -> bool:
    """Evaluate one frozen Predicate against the resolution context."""
    op = pred.get("op", "eq")
    actual = ctx.get(pred.get("key"))
    expected = pred.get("value")
    try:
        if op == "eq":
            return actual == expected
        if op == "neq":
            return actual != expected
        if op == "exists":
            return actual is not None
        if op == "truthy":
            return bool(actual)
        if op == "in":
            return actual in (expected or [])
        if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
            if op == "gt":
                return actual > expected
            if op == "gte":
                return actual >= expected
            if op == "lt":
                return actual < expected
            if op == "lte":
                return actual <= expected
        return False
    except TypeError:
        return False


def resolve_ctx(
    contract_id: str, promises: list[dict], facts: list[dict], breaches: list[dict]
) -> dict[str, Any]:
    """Flat dotted-key context for predicate evaluation.

    Layers: promise values (what was promised) < observed facts (reality) <
    derived scenario markers. ``breach.reason_codes`` is a list so `contains`
    style checks work; ``breach.reason_code`` is the first material breach.
    """
    ctx: dict[str, Any] = {}
    for p in promises:
        ctx[p.get("key")] = p.get("value")
    for f in facts:
        ctx[f.get("key")] = f.get("value")

    reasons = sorted(_reason_codes(breaches))
    ctx["breach.reason_codes"] = reasons
    material = [r for r in reasons if r in MATERIAL_REASONS]
    sla = [r for r in reasons if r in SLA_REASONS]
    # Strictly material: rights that need a material breach key off this.
    ctx["breach.reason_code"] = material[0] if material else None
    ctx["breach.any_reason_code"] = material[0] if material else (sla[0] if sla else None)
    ctx["breach.material_present"] = bool(material)
    ctx["breach.sla_present"] = bool(sla)

    # Received-unit attributes (observed reality beats promise for these).
    received_region = _fact_value(facts, "product.region")
    received_warranty = _fact_value(facts, "warranty.type")
    ctx.setdefault("received.product.region", received_region)
    ctx.setdefault("received.warranty.type", received_warranty)

    # Scenario markers injected by demo control (Agent F): replacement.available,
    # replacement.attempted etc. Normalize to booleans.
    inv = _fact_value(facts, "replacement.available")
    attempted = _fact_value(facts, "replacement.attempted")
    ctx["replacement.available"] = bool(inv) if inv is not None else None
    ctx["replacement.attempted"] = bool(attempted) if attempted is not None else False
    return ctx


# --------------------------------------------------------- derivation


_ENTITLEMENT_SPECS: list[dict[str, Any]] = [
    {
        "slug": "merchant_replacement",
        "issuer_type": "merchant",
        "issuer_name": "Aster Electronics",
        "type": "replacement",
        "activates_when": [
            {"key": "breach.reason_code", "op": "eq", "value": "MATERIAL_VARIANT_MISMATCH"},
        ],
        # Broad activation OR-list handled in evaluate_eligibility; the frozen
        # predicate above stays declarative for the UI.
        "activation_reasons": ["MATERIAL_VARIANT_MISMATCH"],
        "required_evidence_types": ["delivery_event", "device_metadata"],
        "remedy_value_paise": None,  # filled from contract amount at derive time
        "estimated_resolution_hours": 72.0,
        "execution_mode": "merchant_api",
        "requires": [],
        "blocks": [],
        "fallback_to": [],  # linked after all ids exist
    },
    {
        "slug": "merchant_full_refund",
        "issuer_type": "merchant",
        "issuer_name": "Aster Electronics",
        "type": "refund",
        "activates_when": [
            {"key": "breach.reason_codes", "op": "in", "value": []},  # populated at derive time
        ],
        "activation_reasons": sorted(MATERIAL_REASONS),
        "required_evidence_types": ["delivery_event"],
        "estimated_resolution_hours": 24.0,
        "execution_mode": "razorpay_refund",
        "requires": [],
        "blocks": [],
        "fallback_to": [],  # -> merchant_replacement (inverse fallback)
    },
    {
        "slug": "merchant_partial_refund_delivery",
        "issuer_type": "merchant",
        "issuer_name": "Aster Electronics",
        "type": "partial_refund",
        "activates_when": [
            {"key": "breach.reason_code", "op": "eq", "value": "DELIVERY_SLA_MISS"},
        ],
        "activation_reasons": sorted(SLA_REASONS),
        "required_evidence_types": ["shipment_event", "delivery_event"],
        "remedy_value_paise": 30000,  # Rs 300 SLA compensation
        "estimated_resolution_hours": 6.0,
        "execution_mode": "razorpay_refund",
        "requires": [],
        "blocks": [],
        "fallback_to": [],
    },
    {
        "slug": "manufacturer_warranty",
        "issuer_type": "manufacturer",
        "issuer_name": "Device manufacturer (region-locked)",
        "type": "warranty",
        "activates_when": [
            {"key": "warranty.type", "op": "eq", "value": "manufacturer"},
            {"key": "product.region", "op": "eq", "value": "IN"},
        ],
        "activation_reasons": [],
        "required_evidence_types": ["device_metadata"],
        "estimated_resolution_hours": 168.0,
        "execution_mode": "external_manual",
        "requires": [],
        "blocks": [],
        "fallback_to": [],
    },
    {
        "slug": "buyer_protection_fallback",
        "issuer_type": "payment_provider",
        "issuer_name": "Payment provider buyer protection",
        "type": "buyer_protection",
        "activates_when": [],
        "activation_reasons": [],
        "required_evidence_types": [],
        "estimated_resolution_hours": 336.0,
        "execution_mode": "external_manual",
        "requires": [],
        "blocks": [],
        "fallback_to": [],
    },
]


def _entitlement_id(contract_id: str, slug: str) -> str:
    # Stable per-contract id so re-derivation updates rather than duplicates.
    from project_dante.domain.hashing import short_hash

    return f"ent_{short_hash([contract_id, slug])}"


def derive_entitlements(contract_id: str) -> list[dict[str, Any]]:
    """Construct + persist the Entitlement set a purchase creates (plan §13).

    Re-derivation is idempotent: stable ids keyed by (contract, slug), records
    updated in place. Appends RIGHTS_REEVALUATED and links the ids onto the
    contract's ``entitlement_ids``.
    """
    contract = _get_contract(contract_id)
    captured = int(contract.get("amount_paise") or 0)

    out: list[dict[str, Any]] = []
    id_by_slug: dict[str, str] = {
        spec["slug"]: _entitlement_id(contract_id, spec["slug"])
        for spec in _ENTITLEMENT_SPECS
    }

    for spec in _ENTITLEMENT_SPECS:
        eid = id_by_slug[spec["slug"]]
        value = spec.get("remedy_value_paise")
        rec: dict[str, Any] = {
            "_type": "entitlement",
            "id": eid,
            "contract_id": contract_id,
            "slug": spec["slug"],
            "issuer_type": spec["issuer_type"],
            "issuer_name": spec["issuer_name"],
            "type": spec["type"],
            "activates_when": spec["activates_when"],
            "expires_at": None,  # P0: no expiring rights; expiry logic ready
            "required_evidence_types": spec["required_evidence_types"],
            "remedy_value_paise": (
                value if value is not None else (captured or None)
            ),
            "estimated_resolution_hours": spec["estimated_resolution_hours"],
            "requires": spec["requires"],
            "blocks": spec["blocks"],
            "fallback_to": spec["fallback_to"],
            "execution_mode": spec["execution_mode"],
            "status": "dormant",
            "synthetic": True,  # demo-defined merchant/manufacturer terms
        }

        # Cross-links per plan §13 / task spec.
        if spec["slug"] == "merchant_replacement":
            rec["fallback_to"] = [id_by_slug.get("merchant_full_refund", "")]
            rec["blocks"] = [id_by_slug.get("merchant_full_refund", "")]
        elif spec["slug"] == "merchant_full_refund":
            # Refund is the fallback when replacement was attempted/unavailable;
            # it also blocks nothing itself. requires is checked dynamically.
            rec["requires"] = []
        elif spec["slug"] == "buyer_protection_fallback":
            rec["requires"] = [
                sid for slug, sid in id_by_slug.items() if slug != "buyer_protection_fallback"
            ]

        existing = STORE.get(eid)
        if existing:
            rec["status"] = existing.get("status", "dormant")  # preserve computed status
        STORE.put(rec)
        out.append(rec)

    ent_ids = [r["id"] for r in out]
    if sorted(contract.get("entitlement_ids") or []) != sorted(ent_ids):
        STORE.update(contract_id, entitlement_ids=ent_ids)

    append_event(
        aggregate_type="contract",
        aggregate_id=contract_id,
        event_type="RIGHTS_REEVALUATED",
        payload={
            "entitlement_ids": ent_ids,
            "statuses": {r["slug"]: r["status"] for r in out},
        },
    )
    return out


def _has_required_evidence(
    ent: dict[str, Any], evidence: list[dict[str, Any]]
) -> tuple[bool, list[str]]:
    have = {e.get("source_type") for e in evidence}
    missing = [t for t in ent.get("required_evidence_types") or [] if t not in have]
    return (not missing), missing


def evaluate_eligibility(contract_id: str) -> list[dict[str, Any]]:
    """Recompute each entitlement's status against CURRENT state (§14.1).

    Statuses:
      eligible   — trigger met, evidence present, no blocking dependency
      blocked    — trigger met but a dependency/fallback condition unmet
                   (e.g. refund before replacement attempted/unavailable)
      invalid    — right cannot apply to the RECEIVED unit (variant mismatch)
      expired    — expires_at in the past
      dormant    — trigger not met yet (no relevant breach)
      consumed   — already exercised via a remediated/executed remedy
    """
    contract = _get_contract(contract_id)
    ents = STORE.find("entitlement", contract_id=contract_id) or derive_entitlements(contract_id)
    promises = _promises(contract_id)
    facts = _facts(contract_id)
    breaches = _breaches(contract_id)
    evidence = _evidence(contract_id)
    remedies = _remedies(contract_id)
    ctx = resolve_ctx(contract_id, promises, facts, breaches)

    now = _now()
    material_reason = ctx.get("breach.reason_code")
    sla_reason = next((r for r in ctx["breach.reason_codes"] if r in SLA_REASONS), None)

    # Replacement availability drives both the replacement right and the
    # refund's fallback condition.
    repl_available = ctx.get("replacement.available")  # True/False/None(unknown)
    repl_attempted = ctx.get("replacement.attempted")  # bool

    replacement_unavailable_or_attempted = (
        repl_attempted or (repl_available is False)
    )

    # A remedy already executed/consumed marks its entitlement consumed.
    executed_ent_ids = {
        r.get("entitlement_id")
        for r in remedies
        if r.get("entitlement_id")
        and (
            r.get("status") == "executed"
            or r.get("rejected_reason") is None and r.get("rank") == 1
            and contract.get("status") == "REMEDIATED"
        )
    }

    for ent in ents:
        slug = ent.get("slug")
        status = "dormant"

        # ---- expiry ------------------------------------------------------
        exp = _parse_ts(ent.get("expires_at"))
        if exp is not None and exp < now:
            status = "expired"

        # ---- consumed ----------------------------------------------------
        if ent["id"] in executed_ent_ids:
            status = "consumed"
            STORE.update(ent["id"], status=status)
            continue

        # ---- per-right logic ---------------------------------------------
        if slug == "merchant_replacement":
            if material_reason:
                ok_ev, _missing = _has_required_evidence(ent, evidence)
                if repl_available is False:
                    status = "blocked"  # inventory gone: right exists, can't execute
                elif ok_ev:
                    status = "eligible"
                else:
                    status = "blocked"  # missing delivery/device evidence
            # else stays dormant (no material breach yet)

        elif slug == "merchant_full_refund":
            if material_reason:
                ok_ev, _missing = _has_required_evidence(ent, evidence)
                if replacement_unavailable_or_attempted:
                    # Fallback condition satisfied: replacement dead or buyer
                    # prefers refund. Eligible subject to evidence.
                    status = "eligible" if ok_ev else "blocked"
                else:
                    status = "blocked"  # replacement path must be tried first
            # else dormant

        elif slug == "merchant_partial_refund_delivery":
            if sla_reason:
                ok_ev, _missing = _has_required_evidence(ent, evidence)
                status = "eligible" if ok_ev else "blocked"

        elif slug == "manufacturer_warranty":
            # Valid only on the RECEIVED unit: promised warranty must be
            # manufacturer AND region IN *on what arrived*.
            recv_region = ctx.get("received.product.region")
            recv_warranty = ctx.get("received.warranty.type")
            promised_warranty = _promise_value(promises, "warranty.type")
            promised_region = _promise_value(promises, "product.region")
            effective_warranty = recv_warranty if recv_warranty is not None else promised_warranty
            effective_region = recv_region if recv_region is not None else promised_region
            if effective_warranty != "manufacturer" or effective_region != "IN":
                status = "invalid" if material_reason else "dormant"
            elif material_reason:
                # Right unit but something else breached — warranty still valid.
                status = "eligible"
            else:
                status = "dormant"

        elif slug == "buyer_protection_fallback":
            # Always external/dormant in P0 (plan §59: no direct claim API).
            status = "dormant"

        STORE.update(ent["id"], status=status)

    append_event(
        aggregate_type="contract",
        aggregate_id=contract_id,
        event_type="RIGHTS_REEVALUATED",
        payload={
            "trigger": "eligibility_check",
            "statuses": {e["slug"]: (STORE.get(e["id"]) or {}).get("status") for e in ents},
        },
    )
    return [STORE.get(e["id"]) for e in ents]


# ------------------------------------------------------------------ graph


def build_rights_graph(contract_id: str) -> dict[str, Any]:
    """Project the purchase's rights structure as {nodes, edges} (plan §13).

    Pure derivation from STORE records: purchase, promises(material),
    entitlements, breaches, evidence artifacts, remedies; edges SUPPORTED_BY /
    MATERIAL_TO / ACTIVATED_BY / REQUIRES / BLOCKS / FALLBACK_TO / REMEDIES /
    ISSUED_BY.
    """
    contract = _get_contract(contract_id)
    ents = STORE.find("entitlement", contract_id=contract_id) or derive_entitlements(contract_id)
    promises = [p for p in _promises(contract_id)]
    facts = _facts(contract_id)
    breaches = _breaches(contract_id)
    evidence = _evidence(contract_id)
    remedies = _remedies(contract_id)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    def node(nid: str, ntype: str, label: str, **extra: Any) -> None:
        n = {"id": nid, "type": ntype, "label": label, **extra}
        nodes.append(n)

    def edge(src: str, dst: str, kind: str, **extra: Any) -> None:
        edges.append({"source": src, "target": dst, "kind": kind, **extra})

    # ---- purchase root ---------------------------------------------------
    purchase_id = f"purchase:{contract['id']}"
    node(
        purchase_id,
        "purchase",
        contract.get("display_code") or contract["id"],
        status=contract.get("status"),
        amount_paise=contract.get("amount_paise"),
        razorpay_payment_id=contract.get("razorpay_payment_id"),
        sandbox=bool(contract.get("sandbox_mode")),
    )

    # ---- material promises -----------------------------------------------
    material_promises = [p for p in promises if p.get("material_to_intent")]
    for p in material_promises:
        pid = f"promise:{p['id']}"
        node(
            pid,
            "promise",
            f"{p.get('key')} = {p.get('value')}",
            key=p.get("key"),
            value=p.get("value"),
            material_reason=p.get("material_reason"),
            source_artifact_id=p.get("source_artifact_id"),
        )
        edge(pid, purchase_id, "SUPPORTED_BY")

    # ---- entitlements ------------------------------------------------------
    for ent in ents:
        nid = f"entitlement:{ent['id']}"
        node(
            nid,
            "entitlement",
            ent.get("slug") or ent["id"],
            slug=ent.get("slug"),
            issuer_type=ent.get("issuer_type"),
            issuer_name=ent.get("issuer_name"),
            entitlement_type=ent.get("type"),
            status=ent.get("status"),
            execution_mode=ent.get("execution_mode"),
            remedy_value_paise=ent.get("remedy_value_paise"),
            estimated_resolution_hours=ent.get("estimated_resolution_hours"),
            required_evidence_types=ent.get("required_evidence_types"),
        )
        edge(nid, purchase_id, "ISSUED_BY")
        # Materiality linkage: each entitlement rests on the material promises
        # whose breach family it remedies (replacement/refund) or that define
        # it (warranty).
        for p in material_promises:
            edge(nid, f"promise:{p['id']}", "REQUIRES")

    # ---- breaches ----------------------------------------------------------
    fact_by_id = {f["id"]: f for f in facts}
    promise_by_id = {p["id"]: p for p in promises}
    for b in breaches:
        bid = f"breach:{b['id']}"
        prom = promise_by_id.get(b.get("promise_id"), {})
        node(
            bid,
            "breach",
            b.get("reason_code") or "BREACH",
            severity=b.get("severity"),
            reason_code=b.get("reason_code"),
            explanation=b.get("explanation"),
            detected_at=b.get("detected_at"),
        )
        edge(bid, purchase_id, "ACTIVATED_BY")
        if b.get("promise_id"):
            edge(
                f"promise:{b['promise_id']}",
                bid,
                "MATERIAL_TO",
                promised=prom.get("value"),
                observed=(
                    fact_by_id[b["observed_fact_id"]].get("value")
                    if b.get("observed_fact_id") in fact_by_id
                    else None
                ),
            )
        # Breach activates the rights whose reason families match.
        reason = b.get("reason_code")
        for ent in ents:
            if reason in (MATERIAL_REASONS & set(ent.get("_activation_reasons", []) or [])) or (
                reason in MATERIAL_REASONS
                and ent.get("slug")
                in {"merchant_replacement", "merchant_full_refund"}
            ):
                edge(bid, f"entitlement:{ent['id']}", "ACTIVATED_BY")

    # ---- evidence artifacts -------------------------------------------------
    ev_nodes: set[str] = set()
    for e in evidence:
        eid_n = f"evidence:{e['id']}"
        if eid_n in ev_nodes:
            continue
        ev_nodes.add(eid_n)
        node(
            eid_n,
            "evidence",
            e.get("source_type") or "artifact",
            source_type=e.get("source_type"),
            trusted_level=e.get("trusted_level"),
            sha256=e.get("sha256"),
            synthetic=e.get("synthetic", False),
            excerpt=(e.get("excerpt") or "")[:160] or None,
        )
        edge(eid_n, purchase_id, "SUPPORTED_BY")

    # Promise -> its source evidence; breach -> observed-fact evidence.
    for p in promises:
        src = p.get("source_artifact_id")
        if src:
            edge(f"promise:{p['id']}", f"evidence:{src}", "SUPPORTED_BY")
    for b in breaches:
        fct = fact_by_id.get(b.get("observed_fact_id"))
        if fct and fct.get("source_artifact_id"):
            edge(
                f"evidence:{fct['source_artifact_id']}",
                f"breach:{b['id']}",
                "ACTIVATED_BY",
            )

    # ---- entitlement cross-links --------------------------------------------
    for ent in ents:
        for req in ent.get("requires") or []:
            edge(f"entitlement:{req}", f"entitlement:{ent['id']}", "REQUIRES")
        for blk in ent.get("blocks") or []:
            edge(f"entitlement:{ent['id']}", f"entitlement:{blk}", "BLOCKS")
        for fb in ent.get("fallback_to") or []:
            edge(f"entitlement:{ent['id']}", f"entitlement:{fb}", "FALLBACK_TO")

    # ---- remedies -------------------------------------------------------------
    for r in remedies:
        rid = f"remedy:{r['id']}"
        node(
            rid,
            "remedy",
            r.get("remedy_type") or "remedy",
            remedy_type=r.get("remedy_type"),
            rank=r.get("rank"),
            rejected_reason=r.get("rejected_reason"),
            amount_paise=r.get("amount_paise"),
            status=r.get("status"),
        )
        if r.get("breach_id"):
            kind = "REMEDIATES" if r.get("rejected_reason") is None else "PROPOSED_FOR"
            edge(rid, f"breach:{r['breach_id']}", kind)
        if r.get("entitlement_id"):
            edge(f"entitlement:{r['entitlement_id']}", rid, "REMEDIES")

    # Deduplicate edges (same src/dst/kind) keeping first.
    seen_edges: set[tuple] = set()
    dedup: list[dict] = []
    for e in edges:
        key = (e["source"], e["target"], e["kind"])
        if key in seen_edges:
            continue
        seen_edges.add(key)
        dedup.append(e)

    return {"nodes": nodes, "edges": dedup}


def get_breaches(contract_id: str) -> list[dict[str, Any]]:
    """Breaches for a contract, oldest first."""
    return sorted(_breaches(contract_id), key=lambda b: b.get("detected_at") or "")
