"""Hybrid-demo operator-token gate (real-integration requirement 2).

Postures of the state-changing synthetic fulfillment endpoints
(reset / ship / deliver / replacement-unavailable):

- sandbox (no real Razorpay keys): DEMO_MODE=true alone unlocks them;
  X-Demo-Operator-Token is optional and ignored.
- live-test-mode (rzp_test_* keys configured): every state-changing call must
  ALSO carry ``X-Demo-Operator-Token`` matching settings.demo_operator_token.
  Empty configured token => LOCKED, whatever the request presents.

GET /api/demo/status stays open and reports the active posture for the UI.

Env manipulation follows the test_razorpay_service.py pattern: set/unset env
vars, then ``get_settings.cache_clear()`` so the request-time gate picks the
values up.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from project_dante.api.routes.demo import router as demo_router
from project_dante.settings import get_settings

# Key-shaped but obviously fake: real-key detection requires the rzp_test_
# prefix plus a non-empty secret. The id deliberately breaks the alphanumeric
# run with underscores so it cannot match secret-scanner patterns
# (test_security_redteam.py) — nothing here is a credential.
REAL_TEST_KEY_ID = "rzp_test_DUMMY_OPERATOR_GATE"
REAL_TEST_KEY_SECRET = "dummy-secret-for-operator-gate-tests"
OPERATOR_TOKEN = "op-token-hybrid-demo"

STATE_CHANGING_PATHS = [
    "/api/demo/reset",
    "/api/demo/contracts/con_gate01/ship",
    "/api/demo/contracts/con_gate01/deliver",
    "/api/demo/contracts/con_gate01/replacement-unavailable",
]


def _seed_contract(contract_id: str = "con_gate01") -> None:
    """Minimal PAID contract — enough for the routes' existence check."""
    from project_dante.db.store import STORE

    STORE.put({
        "_type": "contract",
        "id": contract_id,
        "status": "PAID",
        "offer_sku": "AST-HP-ANC-001",
        "amount_paise": 1149900,
    })


def _rebuild_settings(monkeypatch: pytest.MonkeyPatch):
    """Flush the cached Settings and rebuild from the current environment."""
    get_settings.cache_clear()
    return get_settings()


def _sandbox(monkeypatch: pytest.MonkeyPatch):
    """No Razorpay keys -> pure-sandbox posture."""
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    return _rebuild_settings(monkeypatch)


def _live_test_mode(
    monkeypatch: pytest.MonkeyPatch,
    *,
    demo_mode: str = "true",
    operator_token: str | None = OPERATOR_TOKEN,
):
    """Real rzp_test_* keys configured -> hybrid posture."""
    monkeypatch.setenv("DEMO_MODE", demo_mode)
    monkeypatch.setenv("RAZORPAY_KEY_ID", REAL_TEST_KEY_ID)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", REAL_TEST_KEY_SECRET)
    if operator_token is None:
        monkeypatch.delenv("DEMO_OPERATOR_TOKEN", raising=False)
    else:
        monkeypatch.setenv("DEMO_OPERATOR_TOKEN", operator_token)
    return _rebuild_settings(monkeypatch)


@pytest.fixture(autouse=True)
def _flush_settings_cache():
    """Never leak a settings instance built from monkeypatched env."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(demo_router, prefix="/api")
    return TestClient(app)


# --------------------------------------------------------------- sandbox


def test_sandbox_demo_mode_unlocks_all_state_changing_endpoints(
    client, monkeypatch
):
    s = _sandbox(monkeypatch)
    assert s.razorpay_mode == "sandbox"

    r = client.post("/api/demo/reset")
    assert r.status_code == 200

    _seed_contract()
    for path in STATE_CHANGING_PATHS[1:]:
        resp = client.post(path, json={"scenario": "correct"} if "deliver" in path else {})
        assert resp.status_code == 200, path
        assert resp.json()["synthetic"] is True, path


def test_sandbox_token_optional_even_if_configured(client, monkeypatch):
    """Token adds nothing in sandbox: configured-but-absent header still 200."""
    _sandbox(monkeypatch)
    monkeypatch.setenv("DEMO_OPERATOR_TOKEN", OPERATOR_TOKEN)
    _rebuild_settings(monkeypatch)

    assert client.post("/api/demo/reset").status_code == 200


# ------------------------------------------------------- live-test-mode


def test_live_test_mode_without_token_403_on_every_state_change(
    client, monkeypatch
):
    _live_test_mode(monkeypatch)
    _seed_contract()
    for path in STATE_CHANGING_PATHS:
        resp = client.post(
            path, json={"scenario": "correct"} if "deliver" in path else {}
        )
        assert resp.status_code == 403, path


def test_live_test_mode_correct_token_200(client, monkeypatch):
    _live_test_mode(monkeypatch)
    _seed_contract()

    r = client.post(
        "/api/demo/contracts/con_gate01/ship",
        headers={"X-Demo-Operator-Token": OPERATOR_TOKEN},
    )
    assert r.status_code == 200
    assert r.json()["synthetic"] is True


def test_live_test_mode_wrong_token_403(client, monkeypatch):
    _live_test_mode(monkeypatch)
    _seed_contract()

    r = client.post(
        "/api/demo/contracts/con_gate01/ship",
        headers={"X-Demo-Operator-Token": "not-the-real-token"},
    )
    assert r.status_code == 403


def test_live_test_mode_empty_configured_token_locks_endpoints(
    client, monkeypatch
):
    """Empty configured token => LOCKED, whatever the caller presents."""
    _live_test_mode(monkeypatch, operator_token=None)
    _seed_contract()

    # No header at all...
    assert client.post("/api/demo/reset").status_code == 403
    # ...and any header value (even whitespace-only lookalikes).
    for presented in ("anything", "", "   ", OPERATOR_TOKEN):
        r = client.post(
            "/api/demo/contracts/con_gate01/ship",
            headers={"X-Demo-Operator-Token": presented},
        )
        assert r.status_code == 403, repr(presented)


def test_live_test_mode_demo_off_still_403_even_with_valid_token(
    client, monkeypatch
):
    """The operator token supplements DEMO_MODE=true; it never replaces it."""
    _live_test_mode(monkeypatch, demo_mode="false")
    _seed_contract()

    r = client.post(
        "/api/demo/contracts/con_gate01/ship",
        headers={"X-Demo-Operator-Token": OPERATOR_TOKEN},
    )
    assert r.status_code == 403


def test_live_test_mode_whitespace_padded_values_match(
    client, monkeypatch
):
    """Trailing whitespace in env or header must not lock out the operator."""
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("RAZORPAY_KEY_ID", REAL_TEST_KEY_ID)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", REAL_TEST_KEY_SECRET)
    monkeypatch.setenv("DEMO_OPERATOR_TOKEN", f"{OPERATOR_TOKEN}  ")
    _rebuild_settings(monkeypatch)
    _seed_contract()

    r = client.post(
        "/api/demo/contracts/con_gate01/ship",
        headers={"X-Demo-Operator-Token": f"  {OPERATOR_TOKEN}"},
    )
    assert r.status_code == 200


def test_state_gate_uses_request_time_settings_after_token_rotation(
    client, monkeypatch
):
    """A settings cache refresh must change the live operator gate too."""
    _live_test_mode(monkeypatch)
    _seed_contract()

    rotated = "op-token-rotated"
    monkeypatch.setenv("DEMO_OPERATOR_TOKEN", rotated)
    get_settings.cache_clear()

    response = client.post(
        "/api/demo/contracts/con_gate01/ship",
        headers={"X-Demo-Operator-Token": rotated},
    )
    assert response.status_code == 200


# ---------------------------------------------------------------- status


def test_status_reports_sandbox_posture(client, monkeypatch):
    _sandbox(monkeypatch)

    body = client.get("/api/demo/status").json()
    assert body["demo_mode"] is True
    assert body["razorpay_mode"] == "sandbox"
    assert body["operator_token_required"] is False
    assert body["operator_token_configured"] is False


def test_status_reports_hybrid_posture_with_token(client, monkeypatch):
    _live_test_mode(monkeypatch)

    body = client.get("/api/demo/status").json()
    assert body["demo_mode"] is True
    assert body["razorpay_mode"] == "live-test-mode"
    assert body["operator_token_required"] is True
    assert body["operator_token_configured"] is True


def test_status_reports_locked_posture_without_token(client, monkeypatch):
    """Keys present + no provisioned token => UI must show 'locked'."""
    _live_test_mode(monkeypatch, operator_token=None)

    body = client.get("/api/demo/status").json()
    assert body["razorpay_mode"] == "live-test-mode"
    assert body["operator_token_required"] is True
    assert body["operator_token_configured"] is False


def test_status_reports_sandbox_when_demo_off(client, monkeypatch):
    _sandbox(monkeypatch)
    monkeypatch.setenv("DEMO_MODE", "false")
    _rebuild_settings(monkeypatch)

    body = client.get("/api/demo/status").json()
    assert body["demo_mode"] is False
    assert body["operator_token_required"] is False


def test_status_open_in_live_test_mode_without_token(client, monkeypatch):
    """Reading the posture never requires the operator token."""
    _live_test_mode(monkeypatch, operator_token=None)
    resp = client.get("/api/demo/status")
    assert resp.status_code == 200
