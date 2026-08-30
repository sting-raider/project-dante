"""Regression coverage for request identifiers and structured HTTP logs."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from project_dante.api.observability import ObservabilityMiddleware


def _client(*, raise_server_exceptions: bool = True) -> TestClient:
    test_app = FastAPI()

    @test_app.get("/api/contracts/con_test/status")
    async def contract_status(request: Request) -> dict[str, str]:
        return {
            "status": "ok",
            "trace_id": request.state.trace_id,
            "correlation_id": request.state.correlation_id,
        }

    @test_app.get("/api/explode")
    async def explode() -> None:
        raise RuntimeError("secret request detail must not be logged")

    test_app.add_middleware(ObservabilityMiddleware)
    return TestClient(test_app, raise_server_exceptions=raise_server_exceptions)


@pytest.fixture(autouse=True)
def _preserve_http_logger_level():
    # Keep the test isolated from any logging level a neighboring module may
    # install; caplog still receives records from the named logger.
    logger = logging.getLogger("project_dante.http")
    previous_level = logger.level
    yield
    logger.setLevel(previous_level)


def test_request_ids_and_contract_header_are_logged_without_query_data(caplog) -> None:
    caplog.set_level(logging.INFO, logger="project_dante.http")
    response = _client().get(
        "/api/contracts/con_test/status?credential=must-not-appear"
    )

    assert response.status_code == 200
    trace_id = response.headers["X-Trace-Id"]
    correlation_id = response.headers["X-Correlation-Id"]
    assert trace_id.startswith("trace_")
    assert correlation_id.startswith("corr_")
    assert response.headers["X-Contract-Id"] == "con_test"
    assert response.json()["trace_id"] == trace_id
    assert response.json()["correlation_id"] == correlation_id

    records = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "project_dante.http"
    ]
    assert len(records) == 1
    assert records[0] == {
        "event": "http_request_completed",
        "method": "GET",
        "path": "/api/contracts/con_test/status",
        "status_code": 200,
        "duration_ms": records[0]["duration_ms"],
        "trace_id": trace_id,
        "correlation_id": correlation_id,
        "contract_id": "con_test",
    }


def test_exceptions_emit_structured_500_record_without_exception_text(caplog) -> None:
    caplog.set_level(logging.INFO, logger="project_dante.http")
    response = _client(raise_server_exceptions=False).get("/api/explode")

    assert response.status_code == 500
    records = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "project_dante.http"
    ]
    assert len(records) == 1
    assert records[0]["event"] == "http_request_completed"
    assert records[0]["status_code"] == 500
    assert records[0]["error_type"] == "RuntimeError"
    assert "secret request detail" not in records[0].values()


def test_main_app_assembles_observability_middleware() -> None:
    from project_dante.api.app import app

    assert any(
        middleware.cls is ObservabilityMiddleware for middleware in app.user_middleware
    )
