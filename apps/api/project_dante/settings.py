"""Application settings via pydantic-settings; secrets server-only."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
