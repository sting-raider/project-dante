"""Deterministic canonical-JSON hashing for offers, promise sets, contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> bytes:
    """Stable JSON: sorted keys, no whitespace, explicit separators."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def short_hash(value: Any, length: int = 12) -> str:
    return sha256_hex(value)[:length]


def strip_volatile(record: dict[str, Any], volatile_keys: set[str]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k not in volatile_keys}
