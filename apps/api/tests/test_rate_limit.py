"""Regression coverage for the production-only API edge limiter."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from project_dante.api.rate_limit import RateLimitMiddleware
from project_dante.settings import get_settings


def _client(*, read_limit: int = 2, write_limit: int = 1) -> TestClient:
    test_app = FastAPI()

    @test_app.get("/api/read")
    async def read_route() -> dict[str, str]:
        return {"status": "ok"}

    @test_app.post("/api/write")
    async def write_route() -> dict[str, str]:
        return {"status": "ok"}

    @test_app.get("/api/health")
    async def health_route() -> dict[str, str]:
        return {"status": "ok"}

    @test_app.post("/api/webhooks/razorpay")
    async def webhook_route() -> dict[str, str]:
        return {"status": "ok"}

    @test_app.get("/outside-api")
    async def outside_api_route() -> dict[str, str]:
        return {"status": "ok"}

    test_app.add_middleware(
        RateLimitMiddleware,
        window_seconds=60,
        read_limit=read_limit,
        write_limit=write_limit,
    )
    return TestClient(test_app)


@pytest.fixture(autouse=True)
def _flush_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_production_limits_reads_and_writes(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    client = _client(read_limit=2, write_limit=1)

    first_read = client.get("/api/read")
    second_read = client.get("/api/read")
    limited_read = client.get("/api/read")
    first_write = client.post("/api/write")
    limited_write = client.post("/api/write")

    assert first_read.status_code == 200
    assert second_read.status_code == 200
    assert first_read.headers["X-RateLimit-Limit"] == "2"
    assert first_read.headers["X-RateLimit-Remaining"] == "1"
    assert limited_read.status_code == 429
    assert limited_read.json()["detail"] == "rate_limit_exceeded"
    assert limited_read.headers["Retry-After"]
    assert first_write.status_code == 200
    assert first_write.headers["X-RateLimit-Limit"] == "1"
    assert limited_write.status_code == 429


def test_health_webhook_and_non_api_paths_are_exempt(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    client = _client(read_limit=1, write_limit=1)

    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health").status_code == 200
    assert client.post("/api/webhooks/razorpay").status_code == 200
    assert client.post("/api/webhooks/razorpay").status_code == 200
    assert client.get("/outside-api").status_code == 200
    assert client.get("/outside-api").status_code == 200


def test_non_production_is_not_limited(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    get_settings.cache_clear()
    client = _client(read_limit=1, write_limit=1)

    assert client.get("/api/read").status_code == 200
    assert client.get("/api/read").status_code == 200
    assert client.post("/api/write").status_code == 200
    assert client.post("/api/write").status_code == 200


def test_main_app_assembles_rate_limit_middleware() -> None:
    from project_dante.api.app import app

    assert any(middleware.cls is RateLimitMiddleware for middleware in app.user_middleware)
