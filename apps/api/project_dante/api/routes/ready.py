"""Deployment readiness probe: ``GET /api/ready``.

Deliberately separate from ``/api/health``:

- ``/api/health`` answers "is this process alive and which engines are
  selected?" (liveness + configuration introspection).
- ``/api/ready`` answers "can this instance serve real traffic yet?" — the
  question Railway/Kubernetes readiness gates ask before routing. It returns
  ``503`` while the store backend is unusable so the platform holds traffic
  back instead of serving requests that would silently lose state.

The response carries deployment-posture facts only. It NEVER includes
secrets: key ids, key secrets, webhook secrets, operator tokens and LLM keys
are all reduced to coarse mode labels (e.g. ``razorpay_mode`` reports
``live-test-mode`` vs ``sandbox``, never the key id itself).

Registered through the standard routes auto-registration: app.py's
``_register_routes()`` scans exactly this ``routes`` package for modules
exporting ``router``, so the probe must live here (a router at the parent
``project_dante.api`` level would never be mounted).
"""

from __future__ import annotations

import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from project_dante.db.store import STORE
from project_dante.settings import get_settings

router = APIRouter(tags=["ops"])


@router.get("/ready")
async def ready() -> JSONResponse:
    """Report readiness + active posture. 200 when ready, 503 otherwise."""
    current = get_settings()
    store_ready, store_backend = _probe_store()
    body = {
        "ready": store_ready,
        "store_backend": store_backend,
        "razorpay_mode": current.razorpay_mode,
        "llm_engine": current.llm_engine or "deterministic-fallback",
        "demo_mode": current.demo_mode,
    }
    return JSONResponse(status_code=200 if store_ready else 503, content=body)


def _probe_store() -> tuple[bool, str]:
    """Can the store actually hold state right now?

    P0 backend is the process-wide JSON-snapshot store. Two failure modes
    matter for readiness:

    - the in-memory index is broken (lock poisoning, corruption) — count()
      raises;
    - the snapshot's directory is read-only, so every mutation would be
      memory-only and silently lost on restart (single-replica §11.4 makes
      that loss permanent).

    A never-yet-written snapshot file is HEALTHY: a fresh deployment starts
    empty and creates the file on first write.
    """
    try:
        STORE.count()
    except Exception:  # noqa: BLE001 - any store failure means not-ready
        return False, "json-snapshot:index-error"

    path = getattr(STORE, "_path", None)
    if path:
        directory = os.path.dirname(os.path.abspath(path)) or "."
        if not os.access(directory, os.W_OK):
            return False, "json-snapshot:readonly"

    return True, "json-snapshot"
