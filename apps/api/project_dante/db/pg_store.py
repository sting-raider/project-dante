"""Postgres-backed implementation of the Dante store contract (plan §11).

Same synchronous interface as ``db.store.Store`` — put/get/update/delete/
list/find/find_one/count/reset — so every existing call site works unchanged.
The driver is **psycopg 3 (sync, binary build)**: routes are async but the
STORE call sites throughout the domain are synchronous, so a sync driver
keeps the contract honest without thread-offloading gymnastics. psycopg
releases the GIL during socket I/O, so concurrent callers interleave safely.

Storage model: one row per record in ``records`` — ``id`` TEXT PK,
``record_type`` TEXT, full record as ``payload`` JSONB. ``_type`` and ``id``
are promoted into columns for indexing; everything else stays inside the
payload so get()/list()/find() return dicts shaped exactly like the JSON
backend's.

find() semantics match db.store.Store exactly: equality over the *Python*
values of each field. SQL-side filtering uses ``payload->>k = %s`` only for
scalar string/number/boolean fields where the text projection is lossless;
any other shape (None values, containers, non-scalar JSON) falls back to
post-fetch Python comparison. Correctness over cleverness.

Connection lifecycle: one connection per PostgresStore instance, guarded by
an RLock (the JSON store holds its lock across read-modify-write too). A
dropped connection is re-established lazily on the next operation.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("store_schema.sql")

# Fields whose payload->>text projection round-trips exactly for find().
# - str: ->> is the text itself.
# - bool/int/float: compared via their canonical text form (see _sql_comparable).
_SQL_SCALAR_TYPES = (str, int, float)


def load_schema_sql() -> str:
    """Return the raw DDL from store_schema.sql."""
    return SCHEMA_PATH.read_text(encoding="utf-8")


class PostgresStoreError(RuntimeError):
    """Raised when the Postgres backend cannot serve an operation."""


class PostgresStore:
    """Synchronous Postgres store with the exact Store interface."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or os.environ.get("DATABASE_URL", "")
        if not self._dsn:
            raise PostgresStoreError(
                "PostgresStore requires DATABASE_URL (or an explicit dsn)"
            )
        self._conn: Any = None
        self._lock = threading.RLock()
        self._schema_ready = False

    # ------------------------------------------------------------ internals

    def _connect(self) -> Any:
        import psycopg  # deferred: json backend never pays the import cost

        try:
            self._conn = psycopg.connect(self._dsn)
        except Exception as exc:  # noqa: BLE001 - surfaced as store error
            raise PostgresStoreError(f"cannot connect to Postgres: {exc}") from exc
        return self._conn

    def _connection(self) -> Any:
        with self._lock:
            if self._conn is None or self._conn.closed:
                self._connect()
            assert self._conn is not None
            return self._conn

    def ensure_schema(self) -> None:
        """Apply store_schema.sql (idempotent DDL). Called on first use."""
        with self._lock:
            if self._schema_ready and self._conn is not None and not self._conn.closed:
                return
            conn = self._connection()
            with conn.cursor() as cur:
                cur.execute(load_schema_sql())
            conn.commit()
            self._schema_ready = True

    def close(self) -> None:
        with self._lock:
            if self._conn is not None and not self._conn.closed:
                self._conn.close()
            self._conn = None
            self._schema_ready = False

    @staticmethod
    def _row_to_record(payload_text: str) -> dict[str, Any]:
        rec = json.loads(payload_text)
        return rec if isinstance(rec, dict) else {}

    @staticmethod
    def _sql_comparable(value: Any) -> str | None:
        """Text projection for scalar values whose ->> form is exact."""
        if isinstance(value, bool):
            # psycopg/JSONB render booleans as true/false; ->> yields "true"/"false".
            return "true" if value else "false"
        if isinstance(value, int) and not isinstance(value, bool):
            # JSONB numeric ->> renders ints canonically ("42", no exponent).
            return str(value)
        if isinstance(value, float):
            # Floats risk representation drift between Python repr and PG text;
            # refuse the SQL fast path — caller falls back to Python matching.
            return None
        if isinstance(value, str):
            return value
        return None

    # ------------------------------------------------------------ public API

    def put(self, record: dict[str, Any]) -> dict[str, Any]:
        rid = record["id"]
        rtype = record["_type"]
        conn = self._connection()
        with self._lock:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO records (id, record_type, payload)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (id) DO UPDATE
                        SET record_type = EXCLUDED.record_type,
                            payload = EXCLUDED.payload,
                            updated_at = now()
                    """,
                    (rid, rtype, json.dumps(record, ensure_ascii=False)),
                )
            conn.commit()
            return dict(record)

    def get(self, record_id: str) -> dict[str, Any] | None:
        conn = self._connection()
        with self._lock:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT payload::text FROM records WHERE id = %s", (record_id,)
                )
                row = cur.fetchone()
            return self._row_to_record(row[0]) if row else None

    def delete(self, record_id: str) -> bool:
        conn = self._connection()
        with self._lock:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM records WHERE id = %s", (record_id,))
                deleted = cur.rowcount > 0
            if deleted:
                conn.commit()
            return deleted

    def update(self, record_id: str, **fields: Any) -> dict[str, Any] | None:
        conn = self._connection()
        with self._lock:
            current = self.get(record_id)
            if current is None:
                return None
            merged = {**current, **fields}
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE records
                       SET payload = %s, updated_at = now()
                     WHERE id = %s
                    """,
                    (json.dumps(merged, ensure_ascii=False), record_id),
                )
            conn.commit()
            return merged

    def list(self, record_type: str | None = None) -> list[dict[str, Any]]:
        conn = self._connection()
        query = "SELECT payload::text FROM records"
        params: tuple[Any, ...] = ()
        if record_type is not None:
            query += " WHERE record_type = %s"
            params = (record_type,)
        with self._lock:
            with conn.cursor() as cur:
                cur.execute(query + " ORDER BY created_at, id", params)
                rows = cur.fetchall()
            return [self._row_to_record(r[0]) for r in rows]

    @staticmethod
    def _find_plan(
        record_type: str, fields: dict[str, Any]
    ) -> tuple[list[str], list[tuple[str, Any]], list[tuple[str, Any]]]:
        """Split find() criteria into SQL-side and Python-side filters.

        Returns (sql_where_fragments, sql_params_as_(key, value)_pairs,
        python_checks). Scalar values whose JSONB ->> projection is exact
        become ``payload->>%s = %s`` server-side; everything else (None,
        floats, containers) is deferred to post-fetch Python equality so the
        semantics match db.store.Store exactly.
        """
        sql_parts: list[str] = ["record_type = %s"]
        params: list[tuple[str, Any]] = [("record_type", record_type)]
        python_checks: list[tuple[str, Any]] = []

        for key, value in fields.items():
            comparable = PostgresStore._sql_comparable(value)
            if comparable is not None:
                # Lossless scalar: filter server-side. Missing keys yield NULL
                # which never equals a scalar — same as Store's .get(k) == v
                # mismatch.
                sql_parts.append("payload->>%s = %s")
                params.append((key, comparable))
            else:
                python_checks.append((key, value))
        return sql_parts, params, python_checks

    def find(self, record_type: str, **fields: Any) -> list[dict[str, Any]]:
        """Find records of type matching all field==value pairs (Store semantics)."""
        if not fields:
            return self.list(record_type)

        sql_parts, param_pairs, python_checks = self._find_plan(record_type, fields)
        params: list[Any] = []
        for _, value in param_pairs:
            params.append(value)

        conn = self._connection()
        query = (
            "SELECT payload::text FROM records WHERE "
            + " AND ".join(sql_parts)
            + " ORDER BY created_at, id"
        )
        with self._lock:
            with conn.cursor() as cur:
                cur.execute(query, tuple(params))
                rows = cur.fetchall()

            out: list[dict[str, Any]] = []
            for row in rows:
                rec = self._row_to_record(row[0])
                if all(rec.get(k) == v for k, v in python_checks):
                    out.append(rec)
            return out

    def find_one(self, record_type: str, **fields: Any) -> dict[str, Any] | None:
        matches = self.find(record_type, **fields)
        return matches[0] if matches else None

    def count(self, record_type: str | None = None) -> int:
        conn = self._connection()
        query = "SELECT COUNT(*) FROM records"
        params: tuple[Any, ...] = ()
        if record_type is not None:
            query += " WHERE record_type = %s"
            params = (record_type,)
        with self._lock:
            with conn.cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
            return int(row[0]) if row else 0

    def reset(self) -> int:
        """Wipe all records (demo reset). Returns count removed."""
        conn = self._connection()
        with self._lock:
            n = self.count()
            with conn.cursor() as cur:
                cur.execute("DELETE FROM records")
            conn.commit()
            return n
