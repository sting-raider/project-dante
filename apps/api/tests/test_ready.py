"""Deployment readiness probe tests (deployment specialist).

Covers ``GET /api/ready``:

- ready + 200 in the normal sandbox posture;
- 503 when the store index is unusable (monkeypatched count blow-up);
- 503 when the snapshot directory is read-only (state would be lost);
- posture fields reflect settings (razorpay_mode / llm_engine / demo_mode)
  and NEVER contain secret material.

Env manipulation follows test_demo_operator_token.py: set/del env vars, then
``get_settings.cache_clear()`` and repoint the ready module's captured
settings object; the autouse fixture flushes the cache afterwards so no
monkeypatched instance leaks.
"""

from __future__ import annotations

import os
import stat

import project_dante.api.routes.ready as ready_mod
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from project_dante.api.app import app as full_app  # auto-registration sanity
from project_dante.api.routes.ready import router as ready_router
from project_dante.settings import get_settings

# Key-shaped but fake: matches the rzp_test_ prefix + non-empty-secret rule,
# with underscores breaking any scanner-friendly alphanumeric run. Not a
# credential — same convention as test_demo_operator_token.py.
REAL_TEST_KEY_ID = "rzp_test_DUMMY_READY_PROBE"
REAL_TEST_KEY_SECRET = "dummy-secret-for-ready-probe-tests"


@pytest.fixture(autouse=True)
def _flush_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(ready_router, prefix="/api")
    return TestClient(app)


def _rebuild_settings(monkeypatch: pytest.MonkeyPatch):
    """Flush cached Settings, rebuild from env, repoint ready.get_settings."""
    get_settings.cache_clear()
    fresh = get_settings()
    monkeypatch.setattr(ready_mod, "get_settings", lambda: fresh)
    return fresh


# ---------------------------------------------------------------- happy path


def test_ready_ok_in_sandbox_posture(client, monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    _rebuild_settings(monkeypatch)
    res = client.get("/api/ready")
    assert res.status_code == 200
    body = res.json()
    assert body["ready"] is True
    assert body["store_backend"] == "json-snapshot"
    assert body["razorpay_mode"] == "sandbox"
    assert body["llm_engine"] == "deterministic-fallback"
    assert isinstance(body["demo_mode"], bool)


def test_auto_registration_mounts_ready_under_api():
    """app.py's auto-registration must pick the module up.

    Asserted through a real request rather than app.routes introspection:
    recent FastAPI lazily materializes included routers (_IncludedRouter),
    so route objects do not expose a plain .path until dispatched.
    """
    client = TestClient(full_app)
    res = client.get("/api/ready")
    assert res.status_code == 200
    assert res.json()["ready"] is True


# ---------------------------------------------------------- not-ready paths


def test_store_index_failure_returns_503(client, monkeypatch):
    _rebuild_settings(monkeypatch)

    def boom(record_type=None):  # noqa: ANN001 - mirrors STORE.count signature
        raise RuntimeError("store index poisoned")

    monkeypatch.setattr(ready_mod.STORE, "count", boom)
    res = client.get("/api/ready")
    assert res.status_code == 503
    body = res.json()
    assert body["ready"] is False
    assert body["store_backend"] == "json-snapshot:index-error"


def test_readonly_snapshot_directory_returns_503(
    client, monkeypatch, tmp_path
):
    _rebuild_settings(monkeypatch)

    class FakeStore:
        _path = str(tmp_path / "ro" / "store.json")

        def count(self, record_type=None):  # noqa: ANN001
            return 0

    monkeypatch.setattr(ready_mod, "STORE", FakeStore(), raising=False)
    ro_dir = tmp_path / "ro"
    ro_dir.mkdir()
    # r-x on POSIX: listing works, writes do not. Skipped on Windows, where
    # directory write bits are not enforced this way — the probe's os.access
    # check is platform-honest there and this branch is POSIX behavior.
    os.chmod(ro_dir, stat.S_IREAD | stat.S_IEXEC)

    try:
        res = client.get("/api/ready")
        if os.name == "posix":
            assert res.status_code == 503
            assert res.json()["store_backend"] == "json-snapshot:readonly"
        else:
            # On Windows the fake dir still looks writable; readiness holds.
            assert res.status_code == 200
    finally:
        os.chmod(ro_dir, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)


# -------------------------------------------------------------------- posture


def test_posture_reflects_real_test_mode_and_llm(client, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("RAZORPAY_KEY_ID", REAL_TEST_KEY_ID)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", REAL_TEST_KEY_SECRET)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "sk-dummy-not-a-real-key")
    fresh = _rebuild_settings(monkeypatch)

    res = client.get("/api/ready")
    assert res.status_code == 200
    body = res.json()
    assert body["razorpay_mode"] == "live-test-mode"
    assert body["llm_engine"] == "anthropic"
    assert body["demo_mode"] is False
    # The rebuilt settings object itself must agree (guards a stale capture).
    assert fresh.razorpay_mode == "live-test-mode"


# --------------------------------------------------------------------- secrecy


def test_ready_never_leaks_secrets(client, monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "sk-super-secret-value")
    monkeypatch.setenv("DEMO_OPERATOR_TOKEN", "operator-secret-value")
    _rebuild_settings(monkeypatch)

    raw = client.get("/api/ready").text.lower()
    for needle in (
        "sk-super-secret-value",
        "operator-secret-value",
        "razorpay_key",
        "webhook_secret",
        "llm_api_key",
        "demo_operator_token",
    ):
        assert needle not in raw, f"leaked secret material: {needle}"
