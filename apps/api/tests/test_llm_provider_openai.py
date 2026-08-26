"""Requirement 6 — LLM provider layer tests.

Covers:
- Engine-selection matrix: (provider, key) combos resolve to anthropic,
  openai-compatible, or '' (= rules engine), with get_provider delegating
  entirely to settings.llm_engine.
- OpenAICompatibleProvider.structured(): wire format (endpoint, Bearer auth,
  system+user roles, low temperature, json_object response_format), happy-path
  schema validation over httpx.MockTransport, one feedback-retry on malformed
  JSON, AgentValidationError after retries — and the api key never leaking
  into error strings.
- /api/health reports the ACTUAL active engine, honestly falling back when a
  provider is configured but unusable.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from project_dante.agents.provider import (
    OPENAI_DEFAULT_BASE_URL,
    AgentValidationError,
    AnthropicProvider,
    OpenAICompatibleProvider,
    get_provider,
)
from project_dante.settings import Settings, get_settings
from pydantic import BaseModel


class TinyOut(BaseModel):
    """Minimal structured-output target for wire-level tests."""

    answer: str
    confidence: float


def _settings(**kw) -> Settings:
    """Settings with explicit llm fields (kwargs beat any local .env)."""
    return Settings(**{"llm_provider": kw.pop("llm_provider", ""), **kw})


def _chat_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"index": 0, "message": {"role": "assistant",
                                                  "content": content}}]},
    )


# ------------------------------------------------------- engine selection


@pytest.mark.parametrize(
    ("provider", "key", "expected_engine"),
    [
        # NOTE: fixture keys must NOT match real-key shapes (sk-ant-*,
        # rzp_*): parametrized node ids land in .pytest_cache and the
        # repo-wide secrets scanner walks it.
        ("anthropic", "key-anthropic-fixture", "anthropic"),
        ("openai-compatible", "sk-test", "openai-compatible"),
        # configured but unusable -> '' -> rules engine
        ("anthropic", "", ""),
        ("openai-compatible", "", ""),
        ("", "key-fixture-1", ""),
        ("", "", ""),
        ("unknown-vendor", "key-fixture-2", ""),
    ],
)
def test_engine_selection_matrix(provider, key, expected_engine):
    s = _settings(llm_provider=provider, llm_api_key=key)
    assert s.llm_engine == expected_engine
    assert s.llm_enabled == bool(expected_engine)
    p = get_provider(s)
    if expected_engine == "anthropic":
        assert isinstance(p, AnthropicProvider)
    elif expected_engine == "openai-compatible":
        assert isinstance(p, OpenAICompatibleProvider)
    else:
        assert p is None, "no usable engine must select the rules engine (None)"


def test_get_provider_matches_llm_engine_for_every_combo():
    """get_provider must never disagree with settings.llm_engine."""
    for provider in ("", "anthropic", "openai-compatible", "mistral"):
        for key in ("", "key-fixture-3"):
            s = _settings(llm_provider=provider, llm_api_key=key)
            p = get_provider(s)
            if not s.llm_engine:
                assert p is None
            elif s.llm_engine == "anthropic":
                assert isinstance(p, AnthropicProvider)
            else:
                assert isinstance(p, OpenAICompatibleProvider)


# ------------------------------------------- OpenAICompatibleProvider wire


async def test_structured_happy_path_wire_format():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.read())
        return _chat_response(json.dumps({"answer": "forty-two", "confidence": 0.9}))

    s = _settings(
        llm_provider="openai-compatible",
        llm_api_key="key-fixture-openai",
        llm_model="test-model",
        llm_base_url="http://mock.local/v1/",  # trailing slash must be trimmed
    )
    provider = OpenAICompatibleProvider(s, transport=httpx.MockTransport(handler))
    out = await provider.structured(
        system="be terse", user="buyer text here",
        output_schema=TinyOut, trace_id="trace_t",
    )
    assert out == TinyOut(answer="forty-two", confidence=0.9)
    assert provider.retries == 0

    assert captured["url"] == "http://mock.local/v1/chat/completions"
    assert captured["auth"] == "Bearer key-fixture-openai"
    payload = captured["payload"]
    assert payload["model"] == "test-model"
    assert payload["temperature"] <= 0.5
    assert payload["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]
    assert "JSON Schema" in payload["messages"][0]["content"]
    assert "buyer text here" in payload["messages"][1]["content"]


async def test_default_base_url_is_openai_when_unset():
    seen_url = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_url
        seen_url = str(request.url)
        return _chat_response('{"answer": "a", "confidence": 1.0}')

    s = _settings(llm_provider="openai-compatible", llm_api_key="sk-x")
    provider = OpenAICompatibleProvider(s, transport=httpx.MockTransport(handler))
    await provider.structured(system="s", user="u", output_schema=TinyOut, trace_id="t")
    assert provider.base_url == OPENAI_DEFAULT_BASE_URL
    assert seen_url == f"{OPENAI_DEFAULT_BASE_URL}/chat/completions"


async def test_malformed_json_retries_once_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _chat_response("this is definitely { not json")
        return _chat_response('{"answer": "ok", "confidence": 0.5}')

    s = _settings(llm_provider="openai-compatible", llm_api_key="sk-x")
    provider = OpenAICompatibleProvider(s, transport=httpx.MockTransport(handler))
    out = await provider.structured(system="s", user="original ask",
                                    output_schema=TinyOut, trace_id="t")
    assert out.answer == "ok"
    assert calls["n"] == 2, "exactly one feedback retry expected"
    assert provider.retries == 1


async def test_always_invalid_output_raises_after_max_attempts():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _chat_response('{"answer": 123, "confidence": "not-a-number"}')

    s = _settings(llm_provider="openai-compatible", llm_api_key="sk-x")
    provider = OpenAICompatibleProvider(s, transport=httpx.MockTransport(handler))
    with pytest.raises(AgentValidationError, match="after 2 attempts"):
        await provider.structured(system="s", user="u", output_schema=TinyOut,
                                  trace_id="t")
    assert calls["n"] == 2
    # retries mirrors AnthropicProvider semantics: the attempt number of the
    # last validation failure (both attempts failed here).
    assert provider.retries == 2


async def test_http_error_fails_fast_without_retry_and_never_leaks_key():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    s = _settings(llm_provider="openai-compatible", llm_api_key="sk-SUPERSECRET")
    provider = OpenAICompatibleProvider(s, transport=httpx.MockTransport(handler))
    with pytest.raises(AgentValidationError) as excinfo:
        await provider.structured(system="s", user="u", output_schema=TinyOut,
                                  trace_id="t")
    assert calls["n"] == 1, "transport/HTTP failures fail fast, no retry burn"
    assert "sk-SUPERSECRET" not in str(excinfo.value)


async def test_null_message_content_raises_not_crashes():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": None}}]})

    s = _settings(llm_provider="openai-compatible", llm_api_key="sk-x")
    provider = OpenAICompatibleProvider(s, transport=httpx.MockTransport(handler))
    with pytest.raises(AgentValidationError):
        await provider.structured(system="s", user="u", output_schema=TinyOut,
                                  trace_id="t")


def test_markdown_fenced_json_is_tolerated():
    """Models occasionally wrap JSON in ``` fences despite instructions."""
    fenced = "```json\n{\"answer\": \"fenced\", \"confidence\": 0.1}\n```"

    def handler(request: httpx.Request) -> httpx.Response:
        return _chat_response(fenced)

    s = _settings(llm_provider="openai-compatible", llm_api_key="sk-x")
    provider = OpenAICompatibleProvider(s, transport=httpx.MockTransport(handler))

    import asyncio

    out = asyncio.run(provider.structured(system="s", user="u",
                                          output_schema=TinyOut, trace_id="t"))
    assert out.answer == "fenced"


# --------------------------------------------------------- health endpoint


def _health_with_env(monkeypatch, *, provider: str, key: str) -> dict:
    """GET /api/health under explicit LLM env; hermetic against local .env."""
    import project_dante.api.app as app_mod

    for name, value in (
        ("LLM_PROVIDER", provider),
        ("LLM_API_KEY", key),
        ("LLM_MODEL", ""),
        ("LLM_BASE_URL", ""),
    ):
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    try:
        return TestClient(app_mod.app).get("/api/health").json()
    finally:
        get_settings.cache_clear()


def test_health_reports_openai_compatible_engine(monkeypatch):
    body = _health_with_env(monkeypatch, provider="openai-compatible", key="sk-h")
    assert body["status"] == "ok"
    assert body["llm"] == "openai-compatible"
    assert body["llm_engine"] == "openai-compatible"


def test_health_reports_anthropic_engine(monkeypatch):
    body = _health_with_env(monkeypatch, provider="anthropic", key="sk-a")
    assert body["llm"] == "anthropic"
    assert body["llm_engine"] == "anthropic"


def test_health_configured_but_unusable_reports_honest_fallback(monkeypatch):
    """Provider named without a key must NOT show as that provider."""
    body = _health_with_env(monkeypatch, provider="openai-compatible", key="")
    assert body["llm"] == "deterministic-fallback"
    assert body["llm_engine"] == "deterministic-fallback"


def test_health_no_llm_config_reports_fallback(monkeypatch):
    body = _health_with_env(monkeypatch, provider="", key="")
    assert body["llm"] == "deterministic-fallback"
