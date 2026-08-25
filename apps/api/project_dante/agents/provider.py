"""Model provider abstraction (plan §11.4).

One provider done well: direct Anthropic Messages API over httpx with a
schema-validation retry loop. The deterministic rule engines in compiler.py
and evaluator.py are the real fallback — `get_provider` returns None when no
key is configured and callers drop to rules. DeterministicProvider exists only
to keep the protocol explicit; it refuses arbitrary schemas rather than
pretending to be an LLM.

Every agent run is persisted to STORE as an ``agent_run`` record for audit.
Secrets never enter prompts or logs — only the model name, summaries, and
latency are recorded.
"""

from __future__ import annotations

import json
import time
from typing import Protocol

import httpx
from pydantic import BaseModel, ValidationError

from project_dante.db.store import STORE
from project_dante.domain.events import new_id, now_iso
from project_dante.settings import Settings

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
REQUEST_TIMEOUT_S = 30.0
MAX_ATTEMPTS = 2  # initial attempt + one validation-error retry


class AgentValidationError(Exception):
    """Model output failed schema validation after all retries."""


class ModelProvider(Protocol):
    async def structured(
        self,
        *,
        system: str,
        user: str,
        output_schema: type[BaseModel],
        trace_id: str,
    ) -> BaseModel: ...


def _log_agent_run(
    *,
    agent_name: str,
    engine: str,
    input_summary: str,
    output_summary: str,
    started: float,
    validation_retries: int,
    trace_id: str,
) -> None:
    STORE.put(
        {
            "id": new_id("run_"),
            "_type": "agent_run",
            "agent_name": agent_name,
            "engine": engine,
            "input_summary": input_summary[:500],
            "output_summary": output_summary[:500],
            "latency_ms": int((time.monotonic() - started) * 1000),
            "validation_retries": validation_retries,
            "trace_id": trace_id,
            "created_at": now_iso(),
        }
    )


class AnthropicProvider:
    """Structured output via the Anthropic Messages API, tool-use free.

    Asks the model to return ONLY JSON matching the pydantic schema; on
    ValidationError the error text is fed back once; after MAX_ATTEMPTS total
    attempts raises AgentValidationError.
    """

    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.llm_api_key
        self.model = settings.llm_model or "claude-sonnet-4-5"
        self.retries = 0

    def _schema_hint(self, output_schema: type[BaseModel]) -> str:
        return json.dumps(output_schema.model_json_schema(), ensure_ascii=False)

    async def structured(
        self,
        *,
        system: str,
        user: str,
        output_schema: type[BaseModel],
        trace_id: str,
    ) -> BaseModel:
        schema_json = self._schema_hint(output_schema)
        base_system = (
            f"{system}\n\nReturn ONLY a JSON object conforming exactly to this "
            f"JSON Schema. No prose, no markdown fences, no extra keys:\n{schema_json}"
        )
        user_message = user
        last_error = ""
        self.retries = 0

        for attempt in range(1, MAX_ATTEMPTS + 1):
            payload = {
                "model": self.model,
                "max_tokens": 2048,
                "system": base_system,
                "messages": [{"role": "user", "content": user_message}],
            }
            try:
                async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
                    resp = await client.post(
                        ANTHROPIC_API_URL,
                        headers={
                            "x-api-key": self._api_key,
                            "anthropic-version": ANTHROPIC_VERSION,
                            "content-type": "application/json",
                        },
                        json=payload,
                    )
                    resp.raise_for_status()
            except httpx.HTTPError as exc:
                # Transport/HTTP failure is not a schema problem — do not burn
                # the retry on it; fail fast to the caller's fallback path.
                raise AgentValidationError(f"anthropic request failed: {exc}") from exc

            body = resp.json()
            text = "".join(
                block.get("text", "")
                for block in body.get("content", [])
                if isinstance(block, dict)
            ).strip()
            if text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:]
            try:
                return output_schema.model_validate(json.loads(text))
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = str(exc)[:2000]
                self.retries = attempt
                if attempt < MAX_ATTEMPTS:
                    # Feed the schema error back once (plan §19).
                    user_message = (
                        f"{user}\n\nYour previous reply was invalid:\n{last_error}\n"
                        "Reply again with ONLY corrected JSON matching the schema."
                    )

        raise AgentValidationError(
            f"structured output failed validation after {MAX_ATTEMPTS} attempts: {last_error}"
        )


class DeterministicProvider:
    """Placeholder that refuses arbitrary schemas.

    The rule engines in compiler/evaluator are the actual deterministic path;
    this class never fabricates LLM-like behavior.
    """

    async def structured(
        self,
        *,
        system: str,
        user: str,
        output_schema: type[BaseModel],
        trace_id: str,
    ) -> BaseModel:
        raise NotImplementedError(
            "DeterministicProvider does not simulate an LLM; use the rules engine "
            "(provider=None) instead."
        )


def get_provider(settings: Settings) -> AnthropicProvider | None:
    """AnthropicProvider when llm is configured, else None => rules engine."""
    if settings.llm_enabled and settings.llm_provider.lower() in ("", "anthropic"):
        return AnthropicProvider(settings)
    return None


__all__ = [
    "ANTHROPIC_API_URL",
    "AgentValidationError",
    "AnthropicProvider",
    "DeterministicProvider",
    "ModelProvider",
    "get_provider",
]

# Sibling agents import the shared run-logger directly.
__all__.append("_log_agent_run")
