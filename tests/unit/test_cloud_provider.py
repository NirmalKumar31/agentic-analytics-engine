"""The cloud adapter: request shape, response parsing, and what it logs.

Exercised against `httpx.MockTransport`. No test here opens a network
connection or reads a credential.

The Responses API returns a typed array rather than a message, so most of
this file is about telling its failure modes apart: a refusal, a ceiling
reached mid-answer, and a contract violation all arrive as HTTP 200 and
need different responses.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from agentic_analytics.llm.base import LLMError, LLMRequest
from agentic_analytics.llm.cloud import (
    FORBIDDEN_SAMPLING_FIELDS,
    RESPONSE_FORMAT_NAME,
    CloudProvider,
    is_retryable_status,
)

KEY = "sk-proj-secret-value-do-not-log"
MODEL = "gpt-6-luna"

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {"type": "string"},
        "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["intent"],
}


def _request(**kwargs: Any) -> LLMRequest:
    base: dict[str, Any] = {"role": "planner", "system": "s", "user": "u", "schema": SCHEMA}
    return LLMRequest(**(base | kwargs))


def _provider(handler: Any, **kwargs: Any) -> CloudProvider:
    provider = CloudProvider(api_key=KEY, model=MODEL, **kwargs)
    provider._client = httpx.AsyncClient(
        base_url="http://cloud",
        transport=httpx.MockTransport(handler),
        headers={"authorization": f"Bearer {KEY}"},
    )
    return provider


def _completed(
    text: str = '{"intent": "ok"}',
    usage: dict[str, Any] | None = None,
    extra_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A well-formed completed response, reasoning item included.

    The reasoning item is first on purpose: it is what makes
    `output[0].content[0]` the wrong place to look.
    """
    return {
        "id": "resp_1",
        "object": "response",
        "status": "completed",
        "output": [
            {"id": "rs_1", "type": "reasoning", "content": [], "summary": []},
            *(extra_items or []),
            {
                "id": "msg_1",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            },
        ],
        "usage": usage
        if usage is not None
        else {
            "input_tokens": 11,
            "output_tokens": 3,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def _ok(body: dict[str, Any] | None = None) -> Any:
    payload = body if body is not None else _completed()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/input_tokens"):
            return httpx.Response(200, json={"object": "response.input_tokens", "input_tokens": 7})
        return httpx.Response(200, json=payload)

    return handler


def _capture() -> tuple[Any, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/input_tokens"):
            return httpx.Response(200, json={"input_tokens": 7})
        return httpx.Response(200, json=_completed())

    return handler, seen


# ---------------------------------------------------------- request shape
async def test_the_credential_is_sent_as_a_bearer_token() -> None:
    handler, seen = _capture()
    provider = _provider(handler)
    try:
        await provider.complete_json(_request())
    finally:
        await provider.aclose()
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"


async def test_no_anthropic_headers_are_sent() -> None:
    """A leftover vendor header is how a migration half-happens."""
    provider = CloudProvider(api_key=KEY, model=MODEL)
    try:
        headers = {k.lower() for k in provider._client.headers}
    finally:
        await provider.aclose()
    assert "x-api-key" not in headers
    assert "anthropic-version" not in headers


async def test_generation_goes_to_the_responses_endpoint() -> None:
    handler, seen = _capture()
    provider = _provider(handler)
    try:
        await provider.complete_json(_request())
    finally:
        await provider.aclose()
    assert [r.url.path for r in seen] == ["/v1/responses"]


async def test_counting_goes_to_the_input_tokens_endpoint() -> None:
    handler, seen = _capture()
    provider = _provider(handler)
    try:
        counted = await provider.count_input_tokens(_request())
    finally:
        await provider.aclose()
    assert counted == 7
    assert [r.url.path for r in seen] == ["/v1/responses/input_tokens"]


async def test_the_request_disables_storage() -> None:
    """The prompt can carry schema and results derived from an upload."""
    handler, seen = _capture()
    provider = _provider(handler)
    try:
        await provider.complete_json(_request())
    finally:
        await provider.aclose()
    assert json.loads(seen[0].content)["store"] is False


async def test_the_service_tier_is_stated_rather_than_inherited() -> None:
    """A project-level tier would change the price without changing code."""
    handler, seen = _capture()
    provider = _provider(handler)
    try:
        await provider.complete_json(_request())
    finally:
        await provider.aclose()
    assert json.loads(seen[0].content)["service_tier"] == "default"


async def test_reasoning_effort_is_sent_and_defaults_to_low() -> None:
    handler, seen = _capture()
    provider = _provider(handler)
    try:
        await provider.complete_json(_request())
    finally:
        await provider.aclose()
    assert json.loads(seen[0].content)["reasoning"] == {"effort": "low"}


async def test_an_unsupported_reasoning_effort_is_refused_at_construction() -> None:
    """A bad value must fail before a run starts, not on the first paid call."""
    with pytest.raises(LLMError, match="reasoning effort"):
        CloudProvider(api_key=KEY, model=MODEL, reasoning_effort="turbo")


async def test_no_sampling_parameter_is_ever_sent() -> None:
    """The engine's determinism comes from the analytics layer.

    A reasoning model rejects or ignores these, and sending one is how a
    request starts failing for a reason unrelated to the analysis.
    """
    handler, seen = _capture()
    provider = _provider(handler)
    try:
        await provider.complete_json(_request())
        await provider.count_input_tokens(_request())
    finally:
        await provider.aclose()
    for request in seen:
        body = json.loads(request.content)
        for field in FORBIDDEN_SAMPLING_FIELDS:
            assert field not in body, f"{field} was sent to {request.url.path}"


async def test_the_schema_is_sent_as_a_strict_json_schema_format() -> None:
    handler, seen = _capture()
    provider = _provider(handler)
    try:
        await provider.complete_json(_request())
    finally:
        await provider.aclose()
    fmt = json.loads(seen[0].content)["text"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["name"] == RESPONSE_FORMAT_NAME
    assert fmt["strict"] is True
    assert fmt["schema"]["additionalProperties"] is False
    # Optionality became nullability, and every property is required.
    assert sorted(fmt["schema"]["required"]) == ["intent", "note"]
    assert fmt["schema"]["properties"]["note"]["type"] == ["string", "null"]


async def test_no_hosted_tools_are_requested() -> None:
    """This engine owns its tools; a hosted one would bypass the governance."""
    handler, seen = _capture()
    provider = _provider(handler)
    try:
        await provider.complete_json(_request())
    finally:
        await provider.aclose()
    body = json.loads(seen[0].content)
    assert "tools" not in body
    assert "tool_choice" not in body


async def test_the_count_and_generation_payloads_cannot_drift() -> None:
    """The count must be taken over what is actually sent.

    Counting a differently-shaped payload produces a number that bounds
    nothing, so the two bodies are compared field by field on everything
    that bears tokens.
    """
    provider = CloudProvider(api_key=KEY, model=MODEL)
    try:
        request = _request(user="a longer question about revenue")
        generate = provider.build_payload(request)
        count = provider.count_payload(request)
    finally:
        await provider.aclose()

    for field in ("model", "instructions", "input", "text", "reasoning"):
        assert count[field] == generate[field], f"{field} differs between count and generation"
    # Only non-token-bearing fields may be absent from the count request.
    # `reasoning` is not one of them: reasoning effort can change the hidden
    # instructions the model is given, which is input, and the endpoint is
    # documented as taking the same payload as `responses.create`.
    assert set(generate) - set(count) == {
        "max_output_tokens",
        "service_tier",
        "store",
    }


# ------------------------------------------------------- response parsing
async def test_a_completed_structured_response_is_parsed() -> None:
    provider = _provider(_ok())
    try:
        result = await provider.complete_json(_request())
    finally:
        await provider.aclose()
    assert result == {"intent": "ok"}


async def test_the_answer_is_found_past_the_reasoning_item() -> None:
    """`output[0]` is the reasoning item, not the answer."""
    provider = _provider(_ok(_completed(extra_items=[{"type": "reasoning", "content": []}])))
    try:
        assert await provider.complete_json(_request()) == {"intent": "ok"}
    finally:
        await provider.aclose()


async def test_a_refusal_is_detected_explicitly() -> None:
    """The model understood and declined; that is not a parse failure."""
    body = _completed()
    body["output"][-1]["content"] = [{"type": "refusal", "refusal": "I cannot help with that."}]
    provider = _provider(_ok(body))
    try:
        with pytest.raises(LLMError) as raised:
            await provider.complete_json(_request())
    finally:
        await provider.aclose()
    assert raised.value.kind == "refused"


async def test_an_incomplete_response_is_refused() -> None:
    body = _completed()
    body["status"] = "incomplete"
    body["incomplete_details"] = {"reason": "content_filter"}
    provider = _provider(_ok(body))
    try:
        with pytest.raises(LLMError, match="incomplete"):
            await provider.complete_json(_request())
    finally:
        await provider.aclose()


async def test_output_token_exhaustion_says_so() -> None:
    """A truncated answer must not look like a malformed one.

    The fix for one is a larger allowance; for the other it is a different
    prompt, so conflating them sends an operator the wrong way.
    """
    body = _completed()
    body["status"] = "incomplete"
    body["incomplete_details"] = {"reason": "max_output_tokens"}
    provider = _provider(_ok(body))
    try:
        with pytest.raises(LLMError, match="ran out of output tokens"):
            await provider.complete_json(_request())
    finally:
        await provider.aclose()


async def test_a_failed_status_is_refused() -> None:
    body = _completed()
    body["status"] = "failed"
    provider = _provider(_ok(body))
    try:
        with pytest.raises(LLMError, match="did not complete"):
            await provider.complete_json(_request())
    finally:
        await provider.aclose()


async def test_missing_output_text_is_refused() -> None:
    body = _completed()
    body["output"] = [{"id": "rs_1", "type": "reasoning", "content": []}]
    provider = _provider(_ok(body))
    try:
        with pytest.raises(LLMError, match="did not return a structured answer"):
            await provider.complete_json(_request())
    finally:
        await provider.aclose()


async def test_more_than_one_structured_answer_is_refused() -> None:
    """Picking one would be a guess about which is the answer."""
    body = _completed()
    body["output"].append(
        {
            "id": "msg_2",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": '{"intent": "other"}'}],
        }
    )
    provider = _provider(_ok(body))
    try:
        with pytest.raises(LLMError, match="more than one"):
            await provider.complete_json(_request())
    finally:
        await provider.aclose()


async def test_malformed_json_is_refused() -> None:
    provider = _provider(_ok(_completed(text="{not json")))
    try:
        with pytest.raises(LLMError, match="not valid JSON"):
            await provider.complete_json(_request())
    finally:
        await provider.aclose()


async def test_structured_output_that_is_not_an_object_is_refused() -> None:
    provider = _provider(_ok(_completed(text="[1, 2, 3]")))
    try:
        with pytest.raises(LLMError, match="not an object"):
            await provider.complete_json(_request())
    finally:
        await provider.aclose()


# ---------------------------------------------------------------- usage
async def test_usage_is_recorded_before_the_output_is_parsed() -> None:
    """A response that arrived was billed, whether or not it parsed."""
    provider = _provider(_ok(_completed(text="{not json")))
    try:
        with pytest.raises(LLMError):
            await provider.complete_json(_request())
    finally:
        await provider.aclose()
    assert provider.usage.input_tokens == 11
    assert provider.usage.output_tokens == 3


async def test_cached_and_cache_write_tokens_are_recorded() -> None:
    usage = {
        "input_tokens": 1000,
        "output_tokens": 50,
        "input_tokens_details": {"cached_tokens": 600, "cache_write_tokens": 200},
        "output_tokens_details": {"reasoning_tokens": 20},
    }
    provider = _provider(_ok(_completed(usage=usage)))
    try:
        await provider.complete_json(_request())
    finally:
        await provider.aclose()
    assert provider.last_cached_tokens == 600
    assert provider.last_cache_write_tokens == 200
    assert provider.usage.input_tokens == 1000


async def test_reasoning_tokens_are_not_added_to_output_again() -> None:
    """`output_tokens` already includes them; adding would charge twice."""
    usage = {
        "input_tokens": 100,
        "output_tokens": 50,
        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 40},
    }
    provider = _provider(_ok(_completed(usage=usage)))
    try:
        await provider.complete_json(_request())
    finally:
        await provider.aclose()
    assert provider.usage.output_tokens == 50
    assert provider.last_reasoning_tokens == 40


async def test_missing_usage_records_zero_rather_than_failing() -> None:
    """An answer without usage is still an answer; it is priced elsewhere."""
    body = _completed()
    body.pop("usage")
    provider = _provider(_ok(body))
    try:
        assert await provider.complete_json(_request()) == {"intent": "ok"}
    finally:
        await provider.aclose()
    assert provider.usage.input_tokens == 0


async def test_malformed_usage_does_not_produce_negative_counts() -> None:
    usage = {
        "input_tokens": -5,
        "output_tokens": "many",
        "input_tokens_details": {"cached_tokens": None},
    }
    provider = _provider(_ok(_completed(usage=usage)))
    try:
        await provider.complete_json(_request())
    finally:
        await provider.aclose()
    assert provider.usage.input_tokens == 0
    assert provider.usage.output_tokens == 0
    assert provider.last_cached_tokens == 0


async def test_a_negative_token_count_is_refused() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"input_tokens": -1})

    provider = _provider(handler)
    try:
        with pytest.raises(LLMError, match="usable token count"):
            await provider.count_input_tokens(_request())
    finally:
        await provider.aclose()


# ------------------------------------------------------- failure handling
@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 503])
async def test_every_error_status_is_sanitised(status: int) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": KEY, "code": "boom"}})

    provider = _provider(handler)
    try:
        with pytest.raises(LLMError) as raised:
            await provider.complete_json(_request())
    finally:
        await provider.aclose()
    assert KEY not in str(raised.value)


@pytest.mark.parametrize("code", ["project_spend_limit_exceeded", "credit_balance_exhausted"])
def test_a_spend_limit_429_is_not_retryable(code: str) -> None:
    """Waiting does not lift an account limit.

    It arrives as 429 like ordinary throttling and means the opposite, so
    retrying burns the run's attempts against a wall.
    """
    assert is_retryable_status(429, code) is False


def test_an_ordinary_rate_limit_429_is_retryable() -> None:
    assert is_retryable_status(429, "rate_limit_exceeded") is True
    assert is_retryable_status(429) is True


def test_server_errors_are_retryable_and_client_errors_are_not() -> None:
    assert is_retryable_status(500) is True
    assert is_retryable_status(503) is True
    assert is_retryable_status(400) is False
    assert is_retryable_status(404) is False


async def test_a_spend_limit_failure_says_so() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"error": {"code": "project_spend_limit_exceeded", "message": "no"}}
        )

    provider = _provider(handler)
    try:
        with pytest.raises(LLMError, match="spending limit"):
            await provider.complete_json(_request())
    finally:
        await provider.aclose()


async def test_a_timeout_is_categorised_as_one() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("", request=request)

    provider = _provider(handler)
    try:
        with pytest.raises(LLMError) as raised:
            await provider.complete_json(_request())
    finally:
        await provider.aclose()
    assert raised.value.kind == "timeout"


# --------------------------------------------------------------- secrecy
async def test_the_credential_never_reaches_a_log_or_an_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": f"bad key {KEY}", "code": "x"}})

    provider = _provider(handler)
    with capture_logs() as logs:
        try:
            with pytest.raises(LLMError) as raised:
                await provider.complete_json(_request())
        finally:
            await provider.aclose()
    blob = json.dumps(logs) + str(raised.value)
    assert KEY not in blob
    assert "sk-proj" not in blob
    assert "authorization" not in blob.lower()


async def test_the_response_body_is_never_logged() -> None:
    """A body may echo the request, which can hold an uploaded cell."""
    secret_cell = "Wollongong-8675309"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"code": "bad", "message": f"near {secret_cell}"}}
        )

    provider = _provider(handler)
    with capture_logs() as logs:
        try:
            with pytest.raises(LLMError) as raised:
                await provider.complete_json(_request())
        finally:
            await provider.aclose()
    blob = json.dumps(logs) + str(raised.value)
    assert secret_cell not in blob


# ------------------------------------------------------------- preflight
async def test_model_preflight_records_only_safe_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == f"/v1/models/{MODEL}"
        return httpx.Response(
            200,
            json={
                "id": MODEL,
                "object": "model",
                "created": 1756315696,
                "owned_by": "openai",
                "secret_internal_field": "must not be published",
            },
        )

    provider = _provider(handler)
    try:
        resolved = await provider.verify_model()
    finally:
        await provider.aclose()
    assert resolved["id"] == MODEL
    assert resolved["owned_by"] == "openai"
    assert "secret_internal_field" not in resolved


async def test_an_unknown_model_is_refused_by_preflight() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"code": "model_not_found"}})

    provider = _provider(handler)
    try:
        with pytest.raises(LLMError, match="not available to this credential"):
            await provider.verify_model()
    finally:
        await provider.aclose()


async def test_a_rejected_credential_is_refused_by_preflight() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "invalid_api_key"}})

    provider = _provider(handler)
    try:
        with pytest.raises(LLMError, match="rejected the configured credentials"):
            await provider.verify_model()
    finally:
        await provider.aclose()


async def test_a_call_that_never_returns_is_bounded_by_the_application() -> None:
    """A connection that is accepted and then silent must still end.

    The local provider carries this bound too, added after a run wedged for
    twenty minutes: socket ESTABLISHED, no bytes moving, the HTTP client's
    read timeout never firing. Nothing about that failure is specific to a
    vendor. It is what "accepted, then nothing" looks like from the client
    side, so the cloud adapter keeps the same backstop.

    The mock transport here never responds at all. If the `asyncio.timeout`
    were removed this test would hang rather than fail, which is exactly the
    production behaviour it exists to prevent.
    """
    import asyncio

    async def never_responds(_: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()  # pragma: no cover - never completes
        raise AssertionError("unreachable")

    provider = CloudProvider(api_key=KEY, model=MODEL, timeout_seconds=0.25)
    provider._client = httpx.AsyncClient(
        base_url="http://cloud",
        transport=httpx.MockTransport(never_responds),
        headers={"authorization": f"Bearer {KEY}"},
    )
    with capture_logs() as logs:
        try:
            with pytest.raises(LLMError) as raised:
                await provider.complete_json(_request())
        finally:
            await provider.aclose()
    assert raised.value.kind == "timeout"
    assert KEY not in json.dumps(logs)
