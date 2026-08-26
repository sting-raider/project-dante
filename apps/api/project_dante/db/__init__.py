"""Dante persistence layer.

``db.store`` remains the canonical import surface (``from
project_dante.db.store import STORE``) — every call site in the codebase.
This package exposes the backend factory: ``make_store()`` resolves the
store from ``DANTE_STORE_BACKEND``:

- ``json`` (default): the original thread-safe JSON-snapshot ``Store``
  (zero-infra P0 behavior, byte-for-byte compatible);
- ``postgres`` (or ``pg``): :class:`~project_dante.db.pg_store.PostgresStore`
  bound to ``DATABASE_URL``, same synchronous interface, schema per
  ``store_schema.sql``.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any


def make_store() -> Any:
    """Resolve the store backend from DANTE_STORE_BACKEND.

    Kept in db/__init__ so both this factory and db.store's module-level
    STORE share one resolution path. Postgres connection problems surface on
    first use rather than at import time.
    """
    backend = (os.environ.get("DANTE_STORE_BACKEND") or "json").strip().lower()
    if backend in ("postgres", "pg"):
        from project_dante.db.pg_store import PostgresStore

        store = PostgresStore(os.environ.get("DATABASE_URL") or None)
        # DB may not be up yet at import time; first real use re-raises a
        # clear PostgresStoreError instead of crashing imports.
        with contextlib.suppress(Exception):
            store.ensure_schema()
        return store
    from project_dante.db.store import Store

    return Store()
