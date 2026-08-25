"""Application settings via pydantic-settings; secrets server-only."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root — settings.py lives at <root>/apps/api/project_dante/settings.py,
# so resolve .env locations absolutely instead of relative to the CWD.
_REPO_ROOT = Path(__file__).resolve().parents[3]


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

    database_url: str = ""
    redis_url: str = ""

    llm_provider: str = ""  # "" => deterministic rule engine fallback
    llm_model: str = ""
    llm_api_key: str = ""

    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = "dante-dev-webhook-secret"

    public_app_url: str = "http://localhost:3000"
    api_base_url: str = "http://localhost:8000"

    @property
    def razorpay_live_test_mode(self) -> bool:
        """True only when real test-mode keys are configured."""
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_provider and self.llm_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
