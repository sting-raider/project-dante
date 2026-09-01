"""Model provider abstraction (plan §11.4).

Two providers behind one ``ModelProvider`` protocol, each a thin direct-HTTP
client with a schema-validation retry loop:

- ``AnthropicProvider`` — Anthropic Messages API.
- ``OpenAICompatibleProvider`` — any ``/chat/completions`` server speaking the
  OpenAI wire format (OpenAI, OpenRouter, vLLM, Ollama-openai, …).

The deterministic rule engines in compiler.py and evaluator.py are the real
fallback — ``get_provider`` returns None whenever ``settings.llm_engine`` says
no usable engine is configured and callers drop to rules.
DeterministicProvider exists only to keep the protocol explicit; it refuses
arbitrary schemas rather than pretending to be an LLM.

Every agent run is persisted to STORE as an ``agent_run`` record for audit.
Secrets never enter prompts or logs — only the model name, summaries, and
latency are recorded.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ValidationError

from project_dante.db.store import STORE
from project_dante.domain.events import new_id, now_iso
from project_dante.settings import Settings

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
REQUEST_TIMEOUT_S = 30.0
MAX_ATTEMPTS = 2  # initial attempt + one validation-error retry
NVIDIA_TRANSIENT_HTTP_ATTEMPTS = 3
NVIDIA_TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
NVIDIA_TRANSIENT_RETRY_DELAY_S = 0.5


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
    intent_id: str | None = None,
    compilation_provenance: dict[str, Any] | None = None,
) -> None:
    record: dict[str, Any] = {
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
    if intent_id is not None:
        record["intent_id"] = intent_id
    if compilation_provenance is not None:
        record["compilation_provenance"] = compilation_provenance
    STORE.put(record)


class AnthropicProvider:
    """Structured output via the Anthropic Messages API, tool-use free.

    Asks the model to return ONLY JSON matching the pydantic schema; on
    ValidationError the error text is fed back once; after MAX_ATTEMPTS total
    attempts raises AgentValidationError.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = settings.llm_api_key
        self.provider_name = "anthropic"
        self.model = settings.llm_model or "claude-sonnet-4-5"
        self._transport = transport  # test seam: httpx.MockTransport
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
                async with httpx.AsyncClient(
                    timeout=REQUEST_TIMEOUT_S, transport=self._transport
                ) as client:
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


class OpenAICompatibleProvider:
    """Structured output via any OpenAI-compatible /chat/completions server.

    Same contract as AnthropicProvider: JSON-only output matching the pydantic
    schema, one ValidationError feedback retry, AgentValidationError after
    MAX_ATTEMPTS. Transport/HTTP failures fail fast to the caller's fallback
    path exactly like the Anthropic client. Works against OpenAI itself or any
    compatible gateway via settings.llm_base_url (…/v1).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = settings.llm_api_key.strip()
        configured_provider = settings.llm_provider.strip().lower()
        self.provider_name = configured_provider or "openai-compatible"
        self._is_nvidia_nim = configured_provider == "nvidia"
        self.model = settings.llm_model.strip() or "gpt-4o-mini"
        self.base_url = (settings.llm_base_url.strip() or OPENAI_DEFAULT_BASE_URL).rstrip("/")
        self._transport = transport  # test seam: httpx.MockTransport
        self.retries = 0

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _schema_hint(self, output_schema: type[BaseModel]) -> str:
        return json.dumps(output_schema.model_json_schema(), ensure_ascii=False)

    def _extract_text(self, body: dict) -> str:
        """choices[0].message.content, tolerating a null content part."""
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return ""
        return content.strip() if isinstance(content, str) else ""

    async def structured(
        self,
        *,
        system: str,
        user: str,
        output_schema: type[BaseModel],
        trace_id: str,
    ) -> BaseModel:
        del trace_id  # audit logging happens at the agent layer
        schema_json = self._schema_hint(output_schema)
        base_system = (
            f"{system}\n\nReturn ONLY a JSON object conforming exactly to this "
            f"JSON Schema. No prose, no markdown fences, no extra keys:\n{schema_json}"
        )
        messages = [
            {"role": "system", "content": base_system},
            {"role": "user", "content": user},
        ]
        last_error = ""
        self.retries = 0

        for attempt in range(1, MAX_ATTEMPTS + 1):
            payload = {
                "model": self.model,
                "messages": messages,
                # NVIDIA's Nemotron NIM recommends this sampling pair. Other
                # compatible providers retain Dante's conservative default.
                "temperature": 1.0 if self._is_nvidia_nim else 0.1,
                "response_format": {"type": "json_object"},
            }
            if self._is_nvidia_nim:
                # OpenAI's SDK calls these extra_body fields; raw HTTP must
                # place them at the top level. Disable the reasoning trace so
                # the structured response remains a JSON object.
                payload["top_p"] = 0.95
                payload["chat_template_kwargs"] = {"enable_thinking": False}
            # The api key is sent only in the Authorization header and is never
            # logged — error strings below carry URL/status only.
            resp: httpx.Response | None = None
            request_attempts = (
                NVIDIA_TRANSIENT_HTTP_ATTEMPTS if self._is_nvidia_nim else 1
            )
            for request_attempt in range(1, request_attempts + 1):
                try:
                    async with httpx.AsyncClient(
                        timeout=REQUEST_TIMEOUT_S, transport=self._transport
                    ) as client:
                        resp = await client.post(
                            self.endpoint,
                            headers={
                                "Authorization": f"Bearer {self._api_key}",
                                "content-type": "application/json",
                            },
                            json=payload,
                        )
                        resp.raise_for_status()
                    break
                except httpx.HTTPStatusError as exc:
                    is_transient = (
                        self._is_nvidia_nim
                        and exc.response.status_code in NVIDIA_TRANSIENT_STATUS_CODES
                    )
                    if not is_transient or request_attempt == request_attempts:
                        request_url = (
                            exc.request.url if exc.request is not None else self.endpoint
                        )
                        raise AgentValidationError(
                            f"openai-compatible request failed: {type(exc).__name__} "
                            f"POST {request_url}"
                        ) from exc
                    await asyncio.sleep(NVIDIA_TRANSIENT_RETRY_DELAY_S * request_attempt)
                except httpx.HTTPError as exc:
                    request_url = exc.request.url if exc.request is not None else self.endpoint
                    raise AgentValidationError(
                        f"openai-compatible request failed: {type(exc).__name__} "
                        f"POST {request_url}"
                    ) from exc

            if resp is None:  # pragma: no cover - loop either returns or raises
                raise AgentValidationError("openai-compatible request produced no response")

            try:
                text = self._extract_text(resp.json())
            except ValueError as exc:  # non-JSON body from the server
                raise AgentValidationError(
                    f"openai-compatible response was not JSON: {exc}"
                ) from exc
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
                    # One-shot schema-error feedback retry (plan §19).
                    messages = [
                        messages[0],
                        {
                            "role": "user",
                            "content": (
                                f"{user}\n\nYour previous reply was invalid:\n"
                                f"{last_error}\nReply again with ONLY corrected "
                                "JSON matching the schema."
                            ),
                        },
                    ]

        raise AgentValidationError(
            f"structured output failed validation after {MAX_ATTEMPTS} attempts: "
            f"{last_error}"
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


def get_provider(settings: Settings) -> AnthropicProvider | OpenAICompatibleProvider | None:
    """Provider matching the ACTIVE engine (settings.llm_engine), else None.

    Selection is delegated entirely to settings.llm_engine so provider/key
    usability is decided in exactly one place: 'anthropic' -> Anthropic,
    'openai-compatible'/'groq'/'nvidia' -> OpenAICompatible, '' => rules engine.
    """
    engine = settings.llm_engine
    if not engine:
        return None
    if engine == "anthropic":
        return AnthropicProvider(settings)
    return OpenAICompatibleProvider(settings)


__all__ = [
    "ANTHROPIC_API_URL",
    "OPENAI_DEFAULT_BASE_URL",
    "AgentValidationError",
    "AnthropicProvider",
    "DeterministicProvider",
    "ModelProvider",
    "OpenAICompatibleProvider",
    "get_provider",
]

# Sibling agents import the shared run-logger directly.
__all__.append("_log_agent_run")
