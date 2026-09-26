"""The local provider must fail rather than hang, and say why.

Both defects here were found by running a real evaluation, not by reading
the code.

A sweep wedged for twenty minutes on a single call. The socket to Ollama was
ESTABLISHED with no bytes moving, the server was idle, and httpx's read
timeout never fired. An agent loop that can block forever is worse than one
that fails: nothing downstream gets the chance to react, and the run cannot
even be cancelled cleanly.

Separately, every failure logged `error=` and nothing else, because
`httpx.ReadTimeout` stringifies to the empty string and the log used
`str(exc)`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from agentic_analytics.llm.base import LLMError, LLMRequest
from agentic_analytics.llm.ollama import OllamaProvider


def _request(**kwargs: Any) -> LLMRequest:
    base: dict[str, Any] = {"role": "planner", "system": "s", "user": "u"}
    return LLMRequest(**(base | kwargs))


async def test_a_call_that_never_returns_is_cut_off() -> None:
    """The hang, reproduced: a server that accepts and never answers."""

    async def never_answers(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(60)
        raise AssertionError("the timeout never fired")

    provider = OllamaProvider("http://x", "m", timeout_seconds=0.2)
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(never_answers), base_url="http://x"
    )
    try:
        with pytest.raises(LLMError, match=r"did not respond in time|call failed"):
            async with asyncio.timeout(5):
                await provider.complete_json(_request())
    finally:
        await provider.aclose()


async def test_the_timeout_is_taken_from_the_configured_value() -> None:
    provider = OllamaProvider("http://x", "m", timeout_seconds=12.5)
    try:
        assert provider.call_timeout_seconds == 12.5
    finally:
        await provider.aclose()


async def test_a_failure_with_no_message_still_reports_its_type() -> None:
    """The log must carry what the exception does not.

    `httpx.ReadTimeout` stringifies to the empty string, so a log built from
    `str(exc)` emitted `error=` and told an operator nothing. Twenty-four of
    those in one run. This asserts the emitted record, not just the premise.
    """
    assert str(httpx.ReadTimeout("")) == "", "the premise of this test changed"

    def times_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("")

    provider = OllamaProvider("http://x", "qwen-test", timeout_seconds=5)
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(times_out), base_url="http://x"
    )
    # `capture_logs` rather than `structlog.configure`: the latter is global
    # process state, so the test passed alone and failed in the suite.
    try:
        with capture_logs() as captured, pytest.raises(LLMError, match="did not respond in time"):
            await provider.complete_json(_request())
    finally:
        await provider.aclose()

    records = [e for e in captured if e.get("event") == "ollama_call_failed"]
    assert records, f"no ollama_call_failed record was emitted; saw {captured}"
    record = records[0]
    assert record["error_type"] == "ReadTimeout"
    assert record["model"] == "qwen-test"
    assert record["role"] == "planner"
    # The message is empty, so the record must not be.
    assert record["error"] == "(no message)"


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout(""),
        httpx.ConnectTimeout(""),
        httpx.PoolTimeout(""),
        TimeoutError(),
    ],
)
async def test_every_timeout_shape_is_classified_as_a_timeout(
    error: BaseException,
) -> None:
    """Not "call failed (TimeoutError)". A timeout is its own thing, and a
    real-model report that cannot tell one from a schema error is useless."""

    def raises(request: httpx.Request) -> httpx.Response:
        raise error

    provider = OllamaProvider("http://x", "m", timeout_seconds=5)
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(raises), base_url="http://x")
    try:
        with pytest.raises(LLMError) as excinfo:
            await provider.complete_json(_request())
    finally:
        await provider.aclose()

    assert excinfo.value.kind == "timeout"
    assert "did not respond in time" in str(excinfo.value)


async def test_a_connection_failure_is_a_transport_error_not_a_timeout() -> None:
    def refuses(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = OllamaProvider("http://x", "m")
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(refuses), base_url="http://x"
    )
    try:
        with pytest.raises(LLMError) as excinfo:
            await provider.complete_json(_request())
    finally:
        await provider.aclose()

    assert excinfo.value.kind == "transport_error"


async def test_malformed_json_is_a_json_parse_error() -> None:
    def prose(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "no json here at all"}})

    provider = OllamaProvider("http://x", "m")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(prose), base_url="http://x")
    try:
        with pytest.raises(LLMError) as excinfo:
            await provider.complete_json(_request())
    finally:
        await provider.aclose()

    assert excinfo.value.kind == "json_parse_error"


async def test_a_successful_call_parses_and_records_usage() -> None:
    def answers(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {"content": '{"tasks": []}'},
                "prompt_eval_count": 41,
                "eval_count": 7,
            },
        )

    provider = OllamaProvider("http://x", "m")
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(answers), base_url="http://x"
    )
    try:
        payload = await provider.complete_json(_request())
    finally:
        await provider.aclose()

    assert payload == {"tasks": []}
    assert provider.usage.input_tokens == 41
    assert provider.usage.output_tokens == 7


@pytest.mark.parametrize("think", [True, False])
async def test_the_think_flag_is_sent(think: bool) -> None:
    """Off by default, because a reasoning model spends its whole output
    budget before answering and returns truncated JSON."""
    seen: dict[str, Any] = {}

    def capture(request: httpx.Request) -> httpx.Response:
        seen.update(__import__("json").loads(request.content))
        return httpx.Response(200, json={"message": {"content": "{}"}})

    provider = OllamaProvider("http://x", "m", think=think)
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(capture), base_url="http://x"
    )
    try:
        await provider.complete_json(_request())
    finally:
        await provider.aclose()

    assert seen["think"] is think


async def test_thinking_is_off_by_default() -> None:
    provider = OllamaProvider("http://x", "m")
    try:
        assert provider.think is False
    finally:
        await provider.aclose()


def test_the_setting_default_matches_the_provider_default() -> None:
    from agentic_analytics.config import Settings

    assert Settings().ollama_think is False
