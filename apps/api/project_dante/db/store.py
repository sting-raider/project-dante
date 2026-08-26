"""Dante data store.

P0 strategy: a thread-safe, process-wide in-memory store persisted as a
single JSON snapshot file. This gives full end-to-end runs and tests with
zero infra friction; the interface mirrors the relational model (§25) so the
Postgres layer can be swapped in without touching domain code.

Every record carries its type under `_type`; `id` is unique across the store.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from typing import Any

_STORE_PATH = os.environ.get("DANTE_STORE_PATH", ".dante-store.json")

TYPE_PREFIXES = {
    "intent": "int_",
    "offer": "off_",
    "evidence": "ev_",
    "promise": "pr_",
    "contract": "con_",
    "entitlement": "ent_",
    "fact": "obs_",
    "breach": "br_",
    "remedy": "rem_",
    "money_action": "ma_",
    "policy_decision": "pd_",
    "agent_run": "run_",
    "razorpay_order": "rzo_",
    "razorpay_payment": "rzp_",
    "razorpay_refund": "rzr_",
    "webhook_event": "wh_",
    "fulfillment_event": "fe_",
    "merchant_insight": "mi_",
}


class Store:
    """Thread-safe typed record store with optional JSON persistence."""

    def __init__(self, path: str | None = None) -> None:
        self._path = path or _STORE_PATH
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._load()

    # ------------------------------------------------------------ internals

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._records = data
        except (json.JSONDecodeError, OSError):
            # Corrupt/locked snapshot: start clean rather than crash.
            self._records = {}

    def _persist(self) -> None:
        try:
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._records, f, ensure_ascii=False)
            os.replace(tmp, self._path)
        except OSError:
            # Persistence best-effort in P0; memory remains source of truth.
            pass

    # ------------------------------------------------------------ public

    def put(self, record: dict[str, Any]) -> dict[str, Any]:
        rid = record["id"]
        with self._lock:
            self._records[rid] = dict(record)
            self._persist()
            return dict(record)

    def get(self, record_id: str) -> dict[str, Any] | None:
        with self._lock:
            rec = self._records.get(record_id)
            return dict(rec) if rec else None

    def delete(self, record_id: str) -> bool:
        with self._lock:
            existed = record_id in self._records
            self._records.pop(record_id, None)
            if existed:
                self._persist()
            return existed

    def update(self, record_id: str, **fields: Any) -> dict[str, Any] | None:
        with self._lock:
            rec = self._records.get(record_id)
            if rec is None:
                return None
            rec.update(fields)
            self._records[record_id] = rec
            self._persist()
            return dict(rec)

    def list(self, record_type: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            recs = list(self._records.values())
        if record_type is not None:
            recs = [r for r in recs if r.get("_type") == record_type]
        return [dict(r) for r in recs]

    def find(self, record_type: str, **fields: Any) -> list[dict[str, Any]]:
        """Find records of type matching all field==value pairs."""
        out = []
        for r in self.list(record_type):
            if all(r.get(k) == v for k, v in fields.items()):
                out.append(r)
        return out

    def find_one(self, record_type: str, **fields: Any) -> dict[str, Any] | None:
        matches = self.find(record_type, **fields)
        return matches[0] if matches else None

    def count(self, record_type: str | None = None) -> int:
        return len(self.list(record_type))

    def reset(self) -> int:
        """Wipe all records (demo reset). Returns count removed."""
        with self._lock:
            n = len(self._records)
            self._records.clear()
            try:
                if os.path.exists(self._path):
                    os.remove(self._path)
            except OSError:
                pass
            return n


def _resolve_store() -> Any:
    """Resolve the process-wide STORE through the backend factory.

    DANTE_STORE_BACKEND=json (default) -> Store() below — byte-for-byte the
    original behavior. 'postgres'/'pg' -> PostgresStore(DATABASE_URL) with
    the identical interface, so no call site changes either way.

    Failure postures, deliberately different:

    - missing DATABASE_URL under the postgres backend raises immediately
      (fail-fast operator misconfiguration — a silent JSON fallback would
      split Dante's money state across two backends);
    - an unreachable-but-configured database defers the error to first
      real use, so processes can start before their DB finishes booting.
    """
    backend = (os.environ.get("DANTE_STORE_BACKEND") or "json").strip().lower()
    if backend in ("postgres", "pg"):
        from project_dante.db.pg_store import PostgresStore

        store = PostgresStore(os.environ.get("DATABASE_URL") or None)
        with contextlib.suppress(Exception):
            store.ensure_schema()
        return store
    return Store()


STORE = _resolve_store()
