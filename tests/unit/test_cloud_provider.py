"""The cloud adapter: request shape, failure handling, and what it logs.

Exercised against `httpx.MockTransport`. No test here opens a network
connection or reads a credential.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from agentic_analytics.llm.base import LLMError, LLMRequest
from agentic_analytics.llm.cloud import (
    RESPONSE_TOOL,
    SAMPLING_FIELDS,
    CloudProvider,
)

KEY = "sk-ant-secret-value-do-not-log"
MODEL = "claude-sonnet-5"


def _request(**kwargs: Any) -> LLMRequest:
    base: dict[str, Any] = {"role": "planner", "system": "s", "user": "u", "schema": {}}
    return LLMRequest(**(base | kwargs))


def _provider(handler: Any, **kwargs: Any) -> CloudProvider:
    provider = CloudProvider(api_key=KEY, model=MODEL, **kwargs)
    provider._client = httpx.AsyncClient(
        base_url="http://cloud",
        transport=httpx.MockTransport(handler),
        headers={"x-api-key": KEY},
    )
    return provider


def _ok(body: dict[str, Any] | None = None) -> Any:
    payload = body or {
        "content": [{"type": "tool_use", "name": RESPONSE_TOOL, "input": {"intent": "ok"}}],
        "usage": {"input_tokens": 11, "output_tokens": 3},
    }

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return handler


# ---------------------------------------------------------- request shape
async def test_no_sampling_parameter_is_sent_by_default() -> None:
    """Some current models reject a non-default temperature outright.

    The engine does not depend on a sampling setting -- determinism comes
    from the analytics layer -- so the portable request omits them.
    """
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "content": [{"type": "tool_use", "name": RESPONSE_TOOL, "input": {"intent": "ok"}}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    provider = _provider(handler)
    try:
        await provider.complete_json(_request())
    finally:
        await provider.aclose()

    for field in SAMPLING_FIELDS:
        assert field not in captured, f"{field} was sent"
    # Nor any manual thinking control.
    assert "thinking" not in captured
    assert captured["model"] == MODEL
    assert captured["tool_choice"] == {"type": "tool", "name": RESPONSE_TOOL}
    assert captured["tools"][0]["name"] == RESPONSE_TOOL


async def test_temperature_is_sent_only_when_explicitly_enabled() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "content": [{"type": "tool_use", "name": RESPONSE_TOOL, "input": {"intent": "ok"}}],
                "usage": {},
            },
        )

    provider = _provider(handler, send_temperature=True)
    try:
        await provider.complete_json(_request(temperature=0.0))
    finally:
        await provider.aclose()
    assert captured["temperature"] == 0.0


async def test_token_usage_is_recorded_from_the_response() -> None:
    provider = _provider(_ok())
    try:
        await provider.complete_json(_request())
    finally:
        await provider.aclose()
    assert provider.usage.input_tokens == 11
    assert provider.usage.output_tokens == 3
    assert provider.usage.successes == 1
    assert provider.usage.attempts == 1


# --------------------------------------------------------- failure matrix
@pytest.mark.parametrize(
    ("status", "kind", "fragment"),
    [
        (400, "transport_error", "malformed"),
        (401, "transport_error", "credentials"),
        (403, "transport_error", "credentials"),
        (429, "transport_error", "rate-limited"),
        (500, "transport_error", "service returned an error (500)"),
        (503, "transport_error", "service returned an error (503)"),
    ],
)
async def test_http_errors_are_sanitised_and_classified(
    status: int, kind: str, fragment: str
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": f"upstream said {status}"}})

    provider = _provider(handler)
    try:
        with pytest.raises(LLMError) as raised:
            await provider.complete_json(_request())
    finally:
        await provider.aclose()

    assert raised.value.kind == kind
    assert fragment in str(raised.value)
    # The attempt is charged even though nothing came back.
    assert provider.usage.attempts == 1
    assert provider.usage.successes == 0


async def test_a_malformed_tool_response_is_refused() -> None:
    provider = _provider(
        _ok({"content": [{"type": "text", "text": "here is your answer"}], "usage": {}})
    )
    try:
        with pytest.raises(LLMError, match="did not return a structured answer"):
            await provider.complete_json(_request())
    finally:
        await provider.aclose()


async def test_a_tool_use_block_with_a_non_object_input_is_refused() -> None:
    provider = _provider(
        _ok(
            {
                "content": [{"type": "tool_use", "name": RESPONSE_TOOL, "input": "not an object"}],
                "usage": {},
            }
        )
    )
    try:
        with pytest.raises(LLMError, match="did not return a structured answer"):
            await provider.complete_json(_request())
    finally:
        await provider.aclose()


async def test_a_failure_logs_the_type_without_the_credential() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": KEY}})

    provider = _provider(handler)
    try:
        with capture_logs() as captured, pytest.raises(LLMError):
            await provider.complete_json(_request())
    finally:
        await provider.aclose()

    records = [e for e in captured if e.get("event") == "cloud_call_failed"]
    assert records
    assert records[0]["status"] == 500
    blob = repr(records)
    assert KEY not in blob
    assert "x-api-key" not in blob.lower()
    assert records[0]["model"] == MODEL


def test_construction_without_a_credential_is_refused() -> None:
    with pytest.raises(LLMError, match="no API key"):
        CloudProvider(api_key="", model=MODEL)


# ------------------------------------------------------- model preflight
async def test_model_preflight_confirms_the_configured_model() -> None:
    """A wrong model id should cost a lookup, not a completion."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v1/models/{MODEL}"
        return httpx.Response(
            200,
            json={
                "id": MODEL,
                "display_name": "Claude Sonnet 5",
                "type": "model",
                "created_at": "2026-01-01T00:00:00Z",
            },
        )

    provider = _provider(handler)
    try:
        resolved = await provider.verify_model()
    finally:
        await provider.aclose()

    assert resolved["id"] == MODEL
    assert resolved["display_name"] == "Claude Sonnet 5"
    assert provider.resolved_model == resolved
    # A preflight is not a completion and must not consume the call budget.
    assert provider.usage.attempts == 0


async def test_an_unknown_model_fails_preflight_without_a_completion() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "not found"}})

    provider = _provider(handler)
    try:
        with pytest.raises(LLMError, match="not available to this credential"):
            await provider.verify_model()
    finally:
        await provider.aclose()
    assert provider.usage.attempts == 0


@pytest.mark.parametrize("status", [401, 403])
async def test_preflight_reports_a_rejected_credential(status: int) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "nope"}})

    provider = _provider(handler)
    try:
        with pytest.raises(LLMError, match="rejected the configured credentials"):
            await provider.verify_model()
    finally:
        await provider.aclose()


async def test_preflight_reports_an_unhealthy_registry() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    provider = _provider(handler)
    try:
        with pytest.raises(LLMError, match="registry returned 503"):
            await provider.verify_model()
    finally:
        await provider.aclose()


async def test_preflight_does_not_leak_the_key_on_transport_failure() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach host with {KEY}")

    provider = _provider(handler)
    try:
        with capture_logs() as captured, pytest.raises(LLMError) as raised:
            await provider.verify_model()
    finally:
        await provider.aclose()
    assert KEY not in str(raised.value)
    assert KEY not in repr(captured)
