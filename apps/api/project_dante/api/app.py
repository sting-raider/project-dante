"""Project Dante API — FastAPI application assembly.

Route modules live in project_dante/api/routes/*, each exporting `router`.
They are auto-registered here so specialists never need to edit this file.
"""

from __future__ import annotations

import importlib
import pkgutil

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from project_dante.settings import get_settings

settings = get_settings()

app = FastAPI(
    title="Project Dante API",
    version="0.1.0",
    description="Buyer-owned agentic commerce runtime — intent to resolution.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.public_app_url, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    return {
        "status": "ok",
        "service": "project-dante-api",
        "env": settings.app_env,
        "demo_mode": settings.demo_mode,
        "razorpay": "live-test-mode" if settings.razorpay_live_test_mode else "sandbox-adapter",
        "llm": settings.llm_provider or "deterministic-fallback",
    }
