"""Dante data store.

P0 strategy: a thread-safe, process-wide in-memory store persisted as a
single JSON snapshot file. This gives full end-to-end runs and tests with
zero infra friction; the interface mirrors the relational model (§25) so the
Postgres layer can be swapped in without touching domain code.

Every record carries its type under `_type`; `id` is unique across the store.
"""

from __future__ import annotations

import builtins
import json
import os
import threading
import time
from typing import Any


def _configured_string(env_name: str, settings_name: str, default: str) -> str:
    """Read a runtime string from process env, then the loaded .env settings."""
    value = os.environ.get(env_name)
    if value and value.strip():
        return value.strip()
    from project_dante.settings import get_settings

    configured = getattr(get_settings(), settings_name, "")
    return str(configured).strip() if configured else default


_STORE_PATH = _configured_string("DANTE_STORE_PATH", "dante_store_path", ".dante-store.json")

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

    def put_if_absent(self, record: dict[str, Any]) -> bool:
        """Insert ``record`` only when its id is not already present.

        This is the store-level claim primitive used by webhook processing.
        It is deliberately separate from ``put``: replay-safe effects must be
        able to distinguish "I claimed this id" from "the id already exists"
        without a racy get-then-put sequence.
        """
        rid = record["id"]
        with self._lock:
            if rid in self._records:
                return False
            self._records[rid] = dict(record)
            self._persist()
            return True

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

    def update_if(
        self, record_id: str, match_fields: dict[str, Any], **fields: Any
    ) -> bool:
        """Atomically check-then-set: apply ``fields`` only while the record's
        current values match ``match_fields`` exactly (missing key == None,
        same as ``find`` semantics).

        The whole compare-and-swap happens under ONE lock acquisition, so a
        racing writer either wins the swap or observes the loser's write —
        never both. Returns True when the update was applied.

        The Postgres backend implements the same primitive with a row lock, so
        callers do not need backend-specific fallbacks.
        """
        with self._lock:
            rec = self._records.get(record_id)
            if rec is None or not all(rec.get(k) == v for k, v in match_fields.items()):
                return False
            rec.update(fields)
            self._records[record_id] = rec
            self._persist()
            return True

    def list(self, record_type: str | None = None) -> builtins.list[dict[str, Any]]:
        with self._lock:
            recs = list(self._records.values())
        if record_type is not None:
            recs = [r for r in recs if r.get("_type") == record_type]
        return [dict(r) for r in recs]

    def find(self, record_type: str, **fields: Any) -> builtins.list[dict[str, Any]]:
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
    backend = _configured_string("DANTE_STORE_BACKEND", "dante_store_backend", "json").lower()
    if backend in ("postgres", "pg"):
        from project_dante.db.pg_store import PostgresStore
        from project_dante.settings import get_settings

        store = PostgresStore(
            os.environ.get("DATABASE_URL") or get_settings().database_url or None
        )
        # Final-assault [14]: when the operator explicitly selects the
        # postgres backend, an unreachable DB must fail STARTUP loudly —
        # deferring to first use hides misconfiguration until a money
        # mutation, and a silently-fallen-back store would split money state.
        # A short retry window absorbs transient DB cold-starts (Railway).
        last_exc: Exception | None = None
        for _attempt in range(5):
            try:
                store.ensure_schema()
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001 - re-raised below
                last_exc = exc
                time.sleep(1.5)
        if last_exc is not None:
            raise RuntimeError(
                "DANTE_STORE_BACKEND=postgres but the database is not "
                f"reachable (ensure_schema failed after retries): {last_exc}"
            ) from last_exc
        return store
    return Store()


STORE = _resolve_store()
