"""Promise & Evidence capture pipeline (plan §20).

Freeze-time capture: hash the merchant offer/evidence snapshot, extract typed
promises from STRUCTURED fields, scan rendered listing text only as additional
*untrusted* claims (they never override structured data — plan §23 "untrusted
evidence" threat), link materiality back to the buyer intent, and persist the
frozen promise set with deterministic hashes.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from project_dante.db.store import STORE
from project_dante.domain.events import LOG, append_event, new_id, now_iso
from project_dante.domain.hashing import canonical_json, sha256_hex, strip_volatile
from project_dante.domain.types import EvidenceArtifact, Promise

# Offer fields that change without changing what was promised.
VOLATILE_OFFER_KEYS = {"expires_at", "inventory"}

# Dotted promise keys that are material even when the intent did not constrain
# them explicitly (baseline materiality keeps the hero demo honest).
BASELINE_MATERIAL_KEYS = {
    "price.amount_paise",
    "warranty.type",
    "warranty.region",
    "delivery.promised_by_date",
}

# Intent constraint key -> promise key it directly satisfies.
CONSTRAINT_TO_PROMISE = {
    "max_price_paise": "price.amount_paise",
    "category": "category",
    "form_factor": "attributes.form_factor",
    "anc": "attributes.anc",
    "warranty.type": "warranty.type",
    "warranty.region": "warranty.region",
    "delivery_deadline": "delivery.promised_by_date",
    "condition": "condition",
    "product.region": "product.region",
}

REGION_ALIASES: dict[str, str] = {
    "in": "IN",
    "ind": "IN",
    "india": "IN",
    "ae": "AE",
    "uae": "AE",
    "united arab emirates": "AE",
    "dubai": "AE",
    "us": "US",
    "usa": "US",
    "united states": "US",
    "gb": "GB",
    "uk": "GB",
    "united kingdom": "GB",
    "eu": "EU",
    "de": "DE",
    "germany": "DE",
}

# Keys whose enum-ish value "unknown" means "nothing was promised".
_ENUM_KEYS = {"warranty.type", "condition"}

_CONF_VERIFIED = 0.95
_CONF_ASSERTED = 0.60
_CONF_UNTRUSTED_TEXT = 0.30


def _as_dict(value: Any) -> dict[str, Any]:
    """Accept Pydantic models or dicts interchangeably."""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


# ---------------------------------------------------------------- normalize


def normalize_region(value: Any) -> str | None:
    """Canonicalize country/region spellings (IN / India -> IN, UAE -> AE)."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    return REGION_ALIASES.get(s, str(value).strip().upper())


def parse_dt(value: Any) -> datetime | None:
    """Parse an ISO date/datetime; unparseable input yields None.

    Aware timestamps are converted to UTC and stripped of tzinfo so naive and
    aware values compare safely.
    """
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def normalize_value(key: str, value: Any) -> Any:
    """Canonical form of a promise/fact value for hashing and comparison."""
    if value is None:
        return None
    if key in ("warranty.region", "product.region"):
        return normalize_region(value)
    if key.endswith(".type") or key in ("condition", "category"):
        return str(value).strip().lower()
    if key == "delivery.promised_by_date":
        dt = parse_dt(value)
        return dt.date().isoformat() if dt else value
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.isdigit():
            return int(s)
        return s.lower()
    return value


# ---------------------------------------------------------------- evidence


def build_evidence(
    source_type: str,
    payload: Any,
    trusted_level: str,
    synthetic: bool = False,
    scenario_id: str | None = None,
    contract_id: str | None = None,
    excerpt: str | None = None,
) -> dict[str, Any]:
    """Hash + persist one evidence artifact; append EVIDENCE_SNAPSHOT_CREATED.

    P0 stores the raw payload inline on the record (`payload` field) with
    `raw_payload_ref` pointing at it via a `store://` URI; the shape survives
    a later move to object storage unchanged.
    """
    eid = new_id("ev_")
    artifact = EvidenceArtifact(
        id=eid,
        contract_id=contract_id,
        source_type=source_type,  # type: ignore[arg-type]
        raw_payload_ref=f"store://{eid}",
        sha256=sha256_hex(payload),
        observed_at=now_iso(),
        trusted_level=trusted_level,  # type: ignore[arg-type]
        synthetic=synthetic,
        scenario_id=scenario_id,
        excerpt=excerpt,
    )
    record: dict[str, Any] = artifact.model_dump()
    record["_type"] = "evidence"
    record["payload"] = payload
    STORE.put(record)
    append_event(
        aggregate_type="evidence",
        aggregate_id=eid,
        event_type="EVIDENCE_SNAPSHOT_CREATED",
        payload={
            "evidence_id": eid,
            "source_type": source_type,
            "sha256": artifact.sha256,
            "trusted_level": trusted_level,
            "synthetic": synthetic,
            "scenario_id": scenario_id,
        },
        synthetic=synthetic,
        scenario_id=scenario_id,
        idempotency_key=f"ev:{eid}",
    )
    return record


# ---------------------------------------------------------------- extraction


def _wants(key: str, value: Any) -> bool:
    """Skip absent values and 'unknown' enum placeholders (not real promises)."""
    if value is None or value == "":
        return False
    return not (key in _ENUM_KEYS and str(value).strip().lower() == "unknown")


def _structured_pairs(offer: dict[str, Any]) -> list[tuple[str, Any]]:
    """Flatten structured offer fields into (promise_key, raw_value) pairs."""
    terms = offer.get("terms") or {}
    delivery = offer.get("delivery_promise") or {}
    attributes = offer.get("attributes") or {}
    pairs: list[tuple[str, Any]] = [
        ("product.sku", offer.get("sku")),
        ("price.amount_paise", offer.get("unit_amount_paise")),
        ("warranty.type", terms.get("warranty_type")),
        ("warranty.duration_months", terms.get("warranty_duration_months")),
        ("warranty.region", terms.get("warranty_region")),
        ("delivery.promised_by_date", delivery.get("promised_by_date")),
        ("delivery.max_days", delivery.get("max_days")),
        ("returns.window_days", terms.get("return_window_days")),
        ("replacement.window_days", terms.get("replacement_window_days")),
        ("condition", terms.get("condition")),
        ("product.region", terms.get("region")),
        ("category", offer.get("category")),
        ("attributes.form_factor", attributes.get("form_factor")),
        ("attributes.anc", attributes.get("anc")),
    ]
    return [(k, v) for k, v in pairs if _wants(k, v)]


_WARRANTY_MONTH_TYPE = re.compile(
    r"(\d{1,2})\s*[-–]?\s*month(?:s)?[^.;\n]{0,80}?"
    r"(manufacturer|seller|brand|official)\s+warranty",
    re.I,
)
_WARRANTY_MONTHS_ONLY = re.compile(r"(\d{1,2})\s*[-–]?\s*month(?:s)?[^.;\n]{0,80}?warranty", re.I)
_WARRANTY_MONTHS_AFTER = re.compile(r"warranty[^.;\n]{0,60}?(\d{1,2})\s*[-–]?\s*months?", re.I)
_WARRANTY_TYPE_ONLY = re.compile(r"\b(manufacturer|seller|brand|official)\s+warranty", re.I)
_REGION_IN_TEXT = re.compile(
    r"(?:valid|supported|covered)[^.;\n]{0,30}?\b(?:in|across|for)\s+"
    r"(india|uae|united arab emirates|dubai|ae|usa|us|united states|uk|united kingdom)",
    re.I,
)
_RETURNS_DAYS_A = re.compile(r"(?:returns?|return\s+window)[^.;\n]{0,40}?(\d{1,3})\s*days?", re.I)
_RETURNS_DAYS_B = re.compile(r"(\d{1,3})[\s-]*days?[\s-]*(?:returns?|return\s+window)", re.I)
_DELIVERY_DAYS_A = re.compile(
    r"(?:deliver\w*|shipp\w*|arriv\w*)[^.;\n]{0,50}?within\s+(\d{1,2})\s*days?", re.I
)
_DELIVERY_DAYS_B = re.compile(r"(\d{1,2})[\s-]*days?[\s-]*(?:delivery|shipping|dispatch)", re.I)

_TEXT_REGION_WORDS = {
    "india": "IN",
    "uae": "AE",
    "united arab emirates": "AE",
    "dubai": "AE",
    "ae": "AE",
    "usa": "US",
    "us": "US",
    "united states": "US",
    "uk": "GB",
    "united kingdom": "GB",
}


def scan_text_claims(text: str) -> list[dict[str, Any]]:
    """Light pattern scan of untrusted rendered listing text.

    Returns candidate {key, value} claims. Deliberately narrow (warranty
    type/duration/region, delivery days, return window). Merchant prose is
    DATA (plan invariant 15): callers must treat results as unverified.
    """
    claims: dict[tuple[str, Any], None] = {}
    t = text or ""

    m = _WARRANTY_MONTH_TYPE.search(t)
    if m:
        claims[("warranty.duration_months", int(m.group(1)))] = None
        wtype = m.group(2).lower()
        wtype_norm = "manufacturer" if wtype in ("brand", "official") else wtype
        claims[("warranty.type", wtype_norm)] = None
    else:
        md = _WARRANTY_MONTHS_ONLY.search(t) or _WARRANTY_MONTHS_AFTER.search(t)
        if md:
            claims[("warranty.duration_months", int(md.group(1)))] = None
        mt = _WARRANTY_TYPE_ONLY.search(t)
        if mt:
            wt = mt.group(1).lower()
            claims[("warranty.type", "manufacturer" if wt in ("brand", "official") else wt)] = None

    mr = _REGION_IN_TEXT.search(t)
    if mr:
        region = _TEXT_REGION_WORDS.get(mr.group(1).strip().lower())
        if region:
            claims[("warranty.region", region)] = None

    for pat in (_RETURNS_DAYS_A, _RETURNS_DAYS_B):
        mm = pat.search(t)
        if mm:
            claims[("returns.window_days", int(mm.group(1)))] = None
            break

    for pat in (_DELIVERY_DAYS_A, _DELIVERY_DAYS_B):
        mm = pat.search(t)
        if mm:
            claims[("delivery.max_days", int(mm.group(1)))] = None
            break

    return [{"key": k, "value": v} for (k, v) in claims]


def _rendered_texts(offer: dict[str, Any], evidence: dict[str, Any]) -> list[str]:
    """Collect candidate human-readable listing text from defensive spots."""
    out: list[str] = []
    payload = evidence.get("payload")
    if isinstance(payload, dict):
        rt = payload.get("rendered_text")
        if isinstance(rt, str):
            out.append(rt)
    exc = evidence.get("excerpt")
    if isinstance(exc, str) and len(exc) > 80:  # long excerpt => likely full text
        out.append(exc)
    for spot in (offer.get("rendered_text"), offer.get("description")):
        if isinstance(spot, str):
            out.append(spot)
    notes = (offer.get("terms") or {}).get("notes")
    if isinstance(notes, str):
        out.append(notes)
    return [t for t in out if t.strip()]


def _make_promise(
    *,
    key: str,
    value: Any,
    evidence_id: str,
    extraction_method: str,
    verification_status: str,
    confidence: float,
) -> dict[str, Any]:
    p = Promise(
        id=new_id("pr_"),
        key=key,
        value=value,
        normalized_value=normalize_value(key, value),
        source_artifact_id=evidence_id,
        extraction_method=extraction_method,  # type: ignore[arg-type]
        verification_status=verification_status,  # type: ignore[arg-type]
        confidence=confidence,
    )
    return p.model_dump()


def _is_wrapper(d: dict[str, Any]) -> bool:
    """True for Agent-F freeze_offer payloads: {offer, evidence_payload, rendered_text}."""
    return isinstance(d, dict) and isinstance(d.get("offer"), dict) and "sku" not in d


def unwrap_offer(offer_like: Any) -> tuple[dict[str, Any], Any, str | None, str | None]:
    """Accept a bare MerchantOffer dict or a freeze_offer wrapper.

    Returns (offer, evidence_payload, rendered_text, trusted_level_hint).
    """
    offer_like = _as_dict(offer_like)
    if _is_wrapper(offer_like):
        rendered = offer_like.get("rendered_text")
        return (
            offer_like["offer"],
            offer_like.get("evidence_payload"),
            rendered if isinstance(rendered, str) else None,
            offer_like.get("trusted_level"),
        )
    rendered = offer_like.get("rendered_text")
    return (
        offer_like,
        None,
        rendered if isinstance(rendered, str) else None,
        offer_like.get("trusted_level"),
    )


def extract_promises(offer_dict: Any, evidence_dict: Any) -> list[dict[str, Any]]:
    """Extract typed promises from structured offer fields + untrusted text.

    Structured fields win, always. Rendered-text claims that AGREE with the
    structured data are ignored (already captured); claims that CONTRADICT it
    (or fill gaps) become additional promises with verification_status=
    "unverified" and confidence < 0.5. Untrusted text NEVER overrides
    structured values (plan §23).
    """
    offer_raw = _as_dict(offer_dict)
    evidence = _as_dict(evidence_dict)
    offer = offer_raw["offer"] if _is_wrapper(offer_raw) else offer_raw

    evidence_id = evidence.get("id", "")
    trusted = evidence.get("trusted_level", "merchant_asserted")
    status = "verified" if trusted == "structured_verified" else "merchant_asserted"
    confidence = _CONF_VERIFIED if status == "verified" else _CONF_ASSERTED

    promises = [
        _make_promise(
            key=key,
            value=value,
            evidence_id=evidence_id,
            extraction_method="structured",
            verification_status=status,
            confidence=confidence,
        )
        for key, value in _structured_pairs(offer)
    ]

    structured_by_key = {p["key"]: p for p in promises}
    seen_extra: set[tuple[str, Any]] = set()
    for text in _rendered_texts(offer, evidence):
        for claim in scan_text_claims(text):
            key, raw = claim["key"], claim["value"]
            norm = normalize_value(key, raw)
            sp = structured_by_key.get(key)
            if sp is not None and sp["normalized_value"] == norm:
                continue  # agrees with structured data: already captured
            dedupe = (key, norm)
            if dedupe in seen_extra:
                continue
            seen_extra.add(dedupe)
            promises.append(
                _make_promise(
                    key=key,
                    value=raw,
                    evidence_id=evidence_id,
                    extraction_method="agent_extracted",
                    verification_status="unverified",
                    confidence=_CONF_UNTRUSTED_TEXT,
                )
            )
    return promises


# ---------------------------------------------------------------- materiality


def _constraint_matches(promise_norm: Any, op: str, cval: Any) -> bool:
    """Does a normalized promise value satisfy one typed constraint?"""
    try:
        if op == "eq":
            return promise_norm == cval or promise_norm == normalize_value("", cval)
        if op == "in":
            vals = cval if isinstance(cval, list) else [cval]
            return any(promise_norm == v or promise_norm == normalize_value("", v) for v in vals)
        if op == "contains":
            if isinstance(cval, str) and isinstance(promise_norm, str):
                return cval.lower() in promise_norm
            if isinstance(cval, list):
                return promise_norm in cval
            return False
        # ordered ops: numeric or ISO-string comparison (ISO sorts correctly)
        pn: Any = promise_norm if isinstance(promise_norm, (int, float)) else str(promise_norm)
        cv: Any = cval if isinstance(cval, (int, float)) else str(normalize_value("", cval) or cval)
        if op == "lte":
            return pn <= cv
        if op == "gte":
            return pn >= cv
        if op == "lt":
            return pn < cv
        if op == "gt":
            return pn > cv
    except TypeError:
        return False
    return False


def link_materiality(promises: list[dict[str, Any]], intent_dict: Any) -> list[dict[str, Any]]:
    """Mark each promise material/unmaterial w.r.t. the compiled intent.

    Material iff it satisfies a CRITICAL hard constraint, or it carries
    baseline materiality (price / warranty type / warranty region / delivery
    date). Unverified text-derived claims are NEVER material — materiality
    requires a trusted source. Mutates and returns the list.
    """
    intent = _as_dict(intent_dict or {})
    constraints = [_as_dict(c) for c in (intent.get("hard_constraints") or [])]

    for p in promises:
        p["material_to_intent"] = False
        p["material_reason"] = None

        if p.get("verification_status") == "unverified":
            continue

        matched = None
        for c in constraints:
            if not c.get("critical", True):
                continue
            if CONSTRAINT_TO_PROMISE.get(c.get("key", "")) != p["key"]:
                continue
            if _constraint_matches(p.get("normalized_value"), c.get("op", "eq"), c.get("value")):
                matched = c
                break

        if matched is not None:
            p["material_to_intent"] = True
            p["material_reason"] = (
                f"satisfies critical hard constraint '{matched['key']}' "
                f"{matched.get('op', 'eq')} {matched.get('value')!r}"
            )
        elif p["key"] in BASELINE_MATERIAL_KEYS:
            p["material_to_intent"] = True
            p["material_reason"] = (
                f"baseline materiality: '{p['key']}' defines spend/variant truth "
                "for this intent even without an explicit constraint"
            )
    return promises


# ---------------------------------------------------------------- freeze


def compute_contract_hash(offer_hash: str, promise_set_hash: str) -> str:
    """The hash buyer authorization binds to (plan §33.3 drift guard)."""
    return sha256_hex({"offer_hash": offer_hash, "promise_set_hash": promise_set_hash})


def freeze_promise_set(offer_dict: Any, intent_dict: Any) -> dict[str, Any]:
    """Orchestrate evidence -> promises -> materiality -> persisted frozen set."""
    offer, ev_payload, rendered, trusted_hint = unwrap_offer(offer_dict)
    intent = _as_dict(intent_dict or {})

    stable_offer = strip_volatile(offer, VOLATILE_OFFER_KEYS)
    if ev_payload is None:
        ev_payload = {"offer": stable_offer}
    # The rendered listing text always rides along so the untrusted-text scan
    # can see it, whether the payload came from Agent F or was built here.
    if rendered and isinstance(ev_payload, dict) and not ev_payload.get("rendered_text"):
        ev_payload = {**ev_payload, "rendered_text": rendered}

    if trusted_hint in ("structured_verified", "merchant_asserted", "synthetic", "external"):
        trusted = trusted_hint
    else:
        has_structured = bool(stable_offer.get("unit_amount_paise")) and isinstance(
            stable_offer.get("terms"), dict
        )
        trusted = "structured_verified" if has_structured else "merchant_asserted"

    excerpt_src = rendered or canonical_json(stable_offer.get("terms") or {}).decode("utf-8")
    evidence = build_evidence(
        source_type="checkout_offer",
        payload=ev_payload,
        trusted_level=str(trusted),
        excerpt=excerpt_src[:280] or None,
    )
    evidence["offer_id"] = str(offer.get("id") or offer.get("sku") or "")
    STORE.update(evidence["id"], offer_id=evidence["offer_id"])

    promises = extract_promises(offer, evidence)
    promises = link_materiality(promises, intent)

    for p in promises:
        STORE.put({**p, "_type": "promise"})

    # Deterministic set hash over sorted (key, canonical-normalized-value).
    pairs = sorted((p["key"], canonical_json(p["normalized_value"]).decode()) for p in promises)
    promise_set_hash = sha256_hex([list(t) for t in pairs])
    offer_hash = sha256_hex(stable_offer)

    append_event(
        aggregate_type="offer",
        aggregate_id=evidence["offer_id"],
        event_type="PROMISE_SET_FROZEN",
        payload={
            "offer_hash": offer_hash,
            "promise_set_hash": promise_set_hash,
            "promise_ids": [p["id"] for p in promises],
            "evidence_ids": [evidence["id"]],
            "material_count": sum(1 for p in promises if p["material_to_intent"]),
        },
        idempotency_key=f"frozen:{evidence['id']}",
    )

    return {
        "promise_ids": [p["id"] for p in promises],
        "evidence_ids": [evidence["id"]],
        "promise_set_hash": promise_set_hash,
        "offer_hash": offer_hash,
        "promises": promises,
        "evidence": [evidence],
        "contract_hash": compute_contract_hash(offer_hash, promise_set_hash),
    }


def bind_to_contract(
    contract_id: str,
    promise_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
) -> int:
    """Attach frozen promises/evidence (and their events) to a contract.

    Agent C calls this right after creating the DanteContract so verifier and
    timeline queries by contract_id resolve. Freeze-time events are re-parented
    from the offer/evidence aggregates onto the contract aggregate.
    """
    ids = set(promise_ids or []) | set(evidence_ids or [])
    offer_ids: set[str] = set()
    n = 0
    for rid in ids:
        rec = STORE.get(rid)
        if not rec:
            continue
        updates = {"contract_id": contract_id}
        if rec.get("_type") == "evidence" and rec.get("offer_id"):
            offer_ids.add(rec["offer_id"])
        STORE.update(rid, **updates)
        n += 1

    for ev in LOG.all():
        pid = ev.get("aggregate_id")
        payload = ev.get("payload") or {}
        refs = set(payload.get("promise_ids") or []) | set(payload.get("evidence_ids") or []) | {
            payload.get("evidence_id")
        }
        if pid in offer_ids or (refs & ids):
            ev["aggregate_id"] = contract_id
    return n
