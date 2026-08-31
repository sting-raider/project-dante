"""Small, shared helpers for keeping basket records line-scoped.

The commerce runtime stores most records as dictionaries so the JSON and
Postgres stores can share one interface.  These helpers centralize the one
important rule those records need to share: a populated ``line_item_id`` is
an exact scope, while ``None`` is the legacy single-item scope.
"""

from __future__ import annotations

from typing import Any


def contract_line_ids(contract: dict[str, Any]) -> list[str]:
    """Return valid frozen line ids in their stored order."""
    line_items = contract.get("line_items") or []
    if not isinstance(line_items, list):
        return []
    return [
        str(line["id"])
        for line in line_items
        if isinstance(line, dict) and isinstance(line.get("id"), str) and line["id"]
    ]


def contract_line_scopes(contract: dict[str, Any]) -> list[str | None]:
    """Return every independent scope, preserving legacy contracts."""
    ids = contract_line_ids(contract)
    scopes: list[str | None] = []
    if ids:
        scopes.extend(ids)
    else:
        scopes.append(None)
    return scopes


def record_line_id(record: dict[str, Any]) -> str | None:
    """Normalize a stored line scope; absent/blank values are legacy scope."""
    value = record.get("line_item_id")
    return value if isinstance(value, str) and value else None


def record_matches_scope(
    record: dict[str, Any],
    line_item_id: str | None,
    *,
    allow_unscoped: bool = False,
) -> bool:
    """Whether a record belongs to one exact line scope.

    ``allow_unscoped`` is intentionally opt-in and is useful only for shared
    evidence artifacts.  Promises, facts, breaches, remedies, and money
    actions must use exact scope matching.
    """
    record_scope = record_line_id(record)
    return record_scope == line_item_id or (
        allow_unscoped and line_item_id is not None and record_scope is None
    )


def records_for_scope(
    records: list[dict[str, Any]],
    line_item_id: str | None,
    *,
    allow_unscoped: bool = False,
) -> list[dict[str, Any]]:
    """Filter records without allowing one basket line to leak into another."""
    return [
        record
        for record in records
        if record_matches_scope(
            record, line_item_id, allow_unscoped=allow_unscoped
        )
    ]


def line_item_amount_paise(
    contract: dict[str, Any], line_item_id: str | None
) -> int | None:
    """Return the frozen ceiling for a scope, or the legacy contract total.

    A scoped refund fails closed when its frozen line amount is unavailable;
    it must never silently fall back to the basket total.
    """
    if line_item_id is None:
        amount = contract.get("amount_paise")
        if isinstance(amount, int) and not isinstance(amount, bool) and amount > 0:
            return amount
        return None

    line_items = contract.get("line_items") or []
    if not isinstance(line_items, list):
        return None
    for line in line_items:
        if not isinstance(line, dict) or str(line.get("id") or "") != line_item_id:
            continue
        amount = line.get("amount_paise")
        if isinstance(amount, int) and not isinstance(amount, bool) and amount > 0:
            return amount
        return None
    return None


__all__ = [
    "contract_line_ids",
    "contract_line_scopes",
    "line_item_amount_paise",
    "record_line_id",
    "record_matches_scope",
    "records_for_scope",
]
