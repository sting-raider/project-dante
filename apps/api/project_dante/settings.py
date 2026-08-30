"""Application settings via pydantic-settings; secrets server-only."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root — settings.py lives at <root>/apps/api/project_dante/settings.py,
# so resolve .env locations absolutely instead of relative to the CWD.
_REPO_ROOT = Path(__file__).resolve().parents[3]

# Razorpay test-mode key ids are exactly "rzp_test_" + 14 alphanumeric chars.
_RZP_TEST_PREFIX = "rzp_test_"
_RZP_LIVE_PREFIX = "rzp_live_"


class LiveKeyRejected(RuntimeError):
    """rzp_live_* credentials detected — Dante refuses to start."""


class Settings(BaseSettings):
    # Env files are merged in listed order: later entries override earlier
    # ones (pydantic-settings semantics), and real environment variables
    # still take precedence over every file. Missing files are skipped, so
    # a root .env, an apps/api/.env, both, or neither all work regardless
    # of where the server is launched from.
    model_config = SettingsConfigDict(
        env_file=[
            _REPO_ROOT / ".env",            # general (overridden by…)
            _REPO_ROOT / "apps/api/.env",   # …app-specific values
        ],
        extra="ignore",
    )

    app_env: str = "development"
    demo_mode: bool = True

    # Operator gate for the HYBRID demo (real Razorpay Test Mode payment +
    # synthetic fulfillment steps). While real test keys are configured, the
    # state-changing /api/demo/* endpoints require the X-Demo-Operator-Token
    # header to match this value. Empty string => those endpoints stay LOCKED
    # whenever real test keys are present.
    demo_operator_token: str = ""

    database_url: str = ""
    redis_url: str = ""

    llm_provider: str = ""  # "" | anthropic | openai-compatible | groq
    llm_model: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""  # OpenAI-compatible base (…/v1); empty => api.openai.com/v1

    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = "dante-dev-webhook-secret"

    public_app_url: str = "http://localhost:3000"
    api_base_url: str = "http://localhost:8000"

    # ----------------------------------------------------------- razorpay

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self._enforce_test_only_keys()

    def _enforce_test_only_keys(self) -> None:
        """HARD-REJECT live credentials (real-integration mode requirement 3).

        rzp_live_* fails closed at process start — Dante never constructs a
        client, never issues a request, never partially runs against live
        money keys. Test mode is the only accepted real-key configuration.
        """
        kid = (self.razorpay_key_id or "").strip()
        if kid.startswith(_RZP_LIVE_PREFIX) or (
            self.razorpay_key_secret and kid.startswith("rzp_") is False
            and "live" in kid.lower()
        ):
            raise LiveKeyRejected(
                f"RAZORPAY_KEY_ID {kid[:12]}… is a LIVE key. Project Dante "
                "accepts only rzp_test_* credentials; live keys fail closed."
            )

    @property
    def razorpay_mode(self) -> str:
        """Active gateway mode: 'live-test-mode' or 'sandbox'.

        Renamed from the misleading razorpay_live_test_mode: nothing here is
        'live' in the money sense — it is Razorpay's TEST environment.
        """
        return (
            "live-test-mode"
            if self._has_real_test_keys
            else "sandbox"
        )

    @property
    def _has_real_test_keys(self) -> bool:
        kid = (self.razorpay_key_id or "").strip()
        ksec = (self.razorpay_key_secret or "").strip()
        return bool(kid and ksec and kid.startswith(_RZP_TEST_PREFIX))

    @property
    def razorpay_live_test_mode(self) -> bool:
        """Back-compat alias for razorpay_mode == 'live-test-mode'."""
        return self._has_real_test_keys

    @property
    def razorpay_sandbox_mode(self) -> bool:
        return not self._has_real_test_keys

    # --------------------------------------------------------------- llm

    @property
    def llm_engine(self) -> str:
        """The engine that will ACTUALLY serve requests ('' => rules)."""
        provider = (self.llm_provider or "").strip().lower()
        if provider == "anthropic" and self.llm_api_key:
            return "anthropic"
        if provider in ("openai-compatible", "groq") and self.llm_api_key:
            return "openai-compatible"
        return ""

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_engine)


@lru_cache
def get_settings() -> Settings:
    return Settings()
