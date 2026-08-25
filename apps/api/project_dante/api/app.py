"""Project Dante API — FastAPI application assembly.

Route modules register themselves here; each specialist owns their router.
"""

from __future__ import annotations

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
