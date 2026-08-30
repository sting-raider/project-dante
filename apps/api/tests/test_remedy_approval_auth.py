"""Request-level authentication for the human money-approval endpoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from project_dante.db.store import STORE
from project_dante.domain.money import policy
from project_dante.settings import get_settings


def _app() -> FastAPI:
    from project_dante.api.routes.rights import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return app


def _proposal() -> None:
    STORE.put(
        {
            "_type": "remedy",
            "id": "rem_auth_guard",
            "contract_id": "con_auth_guard",
            "remedy_type": "refund_full",
            "rank": 1,
        }
    )


def test_human_approval_requires_operator_token(monkeypatch):
    monkeypatch.setenv("DEMO_OPERATOR_TOKEN", "approval-operator-token")
    get_settings.cache_clear()
    _proposal()
    called: list[str] = []
    monkeypatch.setattr(
        policy,
        "approve_remedy",
        lambda proposal_id: called.append(proposal_id) or {"money_action": {}, "refund": None},
    )

    try:
        with TestClient(_app()) as client:
            missing = client.post("/api/remedies/rem_auth_guard/approve")
            assert missing.status_code == 403
            wrong = client.post(
                "/api/remedies/rem_auth_guard/approve",
                headers={"X-Demo-Operator-Token": "wrong-token"},
            )
            assert wrong.status_code == 403
            valid = client.post(
                "/api/remedies/rem_auth_guard/approve",
                headers={"X-Demo-Operator-Token": "approval-operator-token"},
            )
            assert valid.status_code == 200
            assert called == ["rem_auth_guard"]
    finally:
        get_settings.cache_clear()


def test_human_approval_fails_closed_when_token_is_unconfigured(monkeypatch):
    monkeypatch.delenv("DEMO_OPERATOR_TOKEN", raising=False)
    get_settings.cache_clear()
    _proposal()

    try:
        with TestClient(_app()) as client:
            response = client.post(
                "/api/remedies/rem_auth_guard/approve",
                headers={"X-Demo-Operator-Token": "anything"},
            )
            assert response.status_code == 503
    finally:
        get_settings.cache_clear()
