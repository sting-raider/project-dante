"""Project Dante API — FastAPI application assembly.

Route modules live in project_dante/api/routes/*, each exporting `router`.
They are auto-registered here so specialists never need to edit this file.
"""

from __future__ import annotations

import importlib
import pkgutil

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from project_dante.api.observability import ObservabilityMiddleware
from project_dante.api.rate_limit import RateLimitMiddleware
from project_dante.settings import get_settings

settings = get_settings()

app = FastAPI(
    title="Project Dante API",
    version="0.1.0",
    description="Buyer-owned agentic commerce runtime — intent to resolution.",
)

app.add_middleware(RateLimitMiddleware)
app.add_middleware(ObservabilityMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.public_app_url, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "Retry-After",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-Trace-Id",
        "X-Correlation-Id",
        "X-Contract-Id",
    ],
)


def _register_routes() -> None:
    import project_dante.api.routes as routes_pkg

    for mod_info in pkgutil.iter_modules(routes_pkg.__path__):
        mod = importlib.import_module(f"project_dante.api.routes.{mod_info.name}")
        router = getattr(mod, "router", None)
        if router is not None:
            app.include_router(router, prefix="/api")


_register_routes()


@app.get("/api/health")
async def health() -> dict:
    # Read live settings, not the import-time snapshot: tests (and operators)
    # mutate env + get_settings.cache_clear(), so a stale module constant would
    # misreport. "llm" reports the engine that will ACTUALLY serve requests —
    # configured-but-unusable states honestly show deterministic-fallback.
    current = get_settings()
    return {
        "status": "ok",
        "service": "project-dante-api",
        "env": current.app_env,
        "demo_mode": current.demo_mode,
        "razorpay": (
            "live-test-mode" if current.razorpay_live_test_mode else "sandbox-adapter"
        ),
        "llm": current.llm_engine or "deterministic-fallback",
        "llm_engine": current.llm_engine or "deterministic-fallback",
    }
