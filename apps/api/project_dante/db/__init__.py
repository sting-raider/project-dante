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

from project_dante.settings import get_settings


def make_store() -> Any:
    """Resolve the store backend from DANTE_STORE_BACKEND.

    Kept in db/__init__ so both this factory and db.store's module-level
    STORE share one resolution path.

    Failure postures: missing DATABASE_URL under the postgres backend
    raises immediately (operator misconfiguration must be loud); an
    unreachable-but-configured database defers its error to first use.
    """
    settings = get_settings()
    backend = (
        os.environ.get("DANTE_STORE_BACKEND") or settings.dante_store_backend or "json"
    ).strip().lower()
    if backend in ("postgres", "pg"):
        from project_dante.db.pg_store import PostgresStore

        store = PostgresStore(os.environ.get("DATABASE_URL") or settings.database_url or None)
        with contextlib.suppress(Exception):
            store.ensure_schema()
        return store
    from project_dante.db.store import Store

    return Store()
