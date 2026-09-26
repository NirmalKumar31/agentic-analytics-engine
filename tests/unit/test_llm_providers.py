"""The provider abstraction: parsing, budgets, error sanitisation, selection.

The cloud adapter is exercised against a mock transport. No test in this file
opens a network connection or reads a credential.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from agentic_analytics.config import Settings
from agentic_analytics.llm.base import (
    BudgetError,
    LLMError,
    LLMProvider,
    LLMRequest,
    LLMUsage,
    extract_json,
    sanitize_provider_error,
)
from agentic_analytics.llm.fake import FakeProvider
from agentic_analytics.llm.registry import build_provider


def request(role: str = "question_analyst", **context: object) -> LLMRequest:
    return LLMRequest(role=role, system="s", user="u", schema={}, context=dict(context))


# ------------------------------------------------------------------ parsing

JSON_SHAPES = [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('```\n{"a": 1}\n```', {"a": 1}),
    ('Here is the answer:\n{"a": 1}\nHope that helps.', {"a": 1}),
    ('{"a": {"b": [1, 2]}}', {"a": {"b": [1, 2]}}),
    ('  \n {"a": 1} \n ', {"a": 1}),
]


@pytest.mark.parametrize("text,expected", JSON_SHAPES)
def test_extract_json_handles_the_shapes_models_emit(
    text: str, expected: dict[str, object]
) -> None:
    assert extract_json(text) == expected


@pytest.mark.parametrize("text", ["", "no json here", "[1, 2, 3]", "{broken", "null"])
def test_extract_json_fails_loudly(text: str) -> None:
    """An empty dict would become a run with no plan, so this must raise."""
    with pytest.raises(LLMError):
        extract_json(text)


# ----------------------------------------------------------- error redaction

REDACTION_CASES = [
    (httpx.ConnectTimeout("timed out"), "did not respond in time"),
    (httpx.ConnectError("connection refused"), "could not be reached"),
    (RuntimeError("401 Unauthorized: bad api key sk-abc123"), "rejected the configured"),
    (RuntimeError("429 rate limit exceeded"), "rate-limited"),
    (RuntimeError("something odd"), "call failed"),
]


@pytest.mark.parametrize("exc,expected", REDACTION_CASES)
def test_provider_errors_are_sanitised(exc: BaseException, expected: str) -> None:
    message = sanitize_provider_error(exc)
    assert expected in message
    assert "sk-abc123" not in message


# ------------------------------------------------------------------ budgets


async def test_llm_call_budget_is_enforced() -> None:
    provider = FakeProvider(max_calls=2)
    await provider.complete_json(request(question="q", metrics=[], dimensions=[]))
    await provider.complete_json(request(question="q", metrics=[], dimensions=[]))
    with pytest.raises(BudgetError, match="ceiling of 2"):
        await provider.complete_json(request(question="q", metrics=[], dimensions=[]))


async def test_failed_attempts_exhaust_the_budget_too() -> None:
    """Two timeouts must consume the ceiling, not zero of it."""
    import httpx

    from agentic_analytics.llm.ollama import OllamaProvider

    def times_out(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("")

    provider = OllamaProvider("http://x", "m", max_calls=2)
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(times_out), base_url="http://x"
    )
    try:
        for _ in range(2):
            with pytest.raises(LLMError):
                await provider.complete_json(request(question="q", metrics=[], dimensions=[]))
        assert provider.usage.attempts == 2
        assert provider.usage.successes == 0
        # The third is refused before anything is dispatched.
        with pytest.raises(BudgetError, match="ceiling of 2"):
            await provider.complete_json(request(question="q", metrics=[], dimensions=[]))
    finally:
        await provider.aclose()


async def test_a_mix_of_success_and_failure_exhausts_the_budget() -> None:
    from agentic_analytics.llm.fake import FakeProvider as _Fake

    class _FlakyOnce(_Fake):
        def __init__(self) -> None:
            super().__init__(max_calls=2)
            self.seen = 0

        async def complete_json(self, req: Any) -> dict[str, Any]:
            self.seen += 1
            if self.seen == 2:
                self._check_budget(req.role)
                raise LLMError("transient", kind="transport_error")
            return await super().complete_json(req)

    provider = _FlakyOnce()
    await provider.complete_json(request(question="q", metrics=[], dimensions=[]))
    with pytest.raises(LLMError):
        await provider.complete_json(request(question="q", metrics=[], dimensions=[]))
    assert provider.usage.attempts == 2
    assert provider.usage.successes == 1
    with pytest.raises(BudgetError):
        await provider.complete_json(request(question="q", metrics=[], dimensions=[]))


def test_usage_accounting() -> None:
    """Attempts and successes are counted separately.

    `start` reserves an attempt before dispatch; `record` marks the one that
    came back. A provider that fails leaves the two numbers apart, which is
    what makes the ceiling a ceiling.
    """
    usage = LLMUsage()
    for role, tokens in (("planner", (10, 3)), ("planner", (5, 2)), ("critic", (0, 0))):
        usage.start(role)
        usage.record(role, input_tokens=tokens[0], output_tokens=tokens[1])
    assert usage.as_dict() == {
        "provider_request_attempts": 3,
        "provider_successful_responses": 3,
        "llm_calls": 3,
        "input_tokens": 15,
        "output_tokens": 5,
        "by_role": {"planner": 2, "critic": 1},
    }


def test_a_failed_attempt_still_counts_against_the_ceiling() -> None:
    """The defect this closes.

    The budget was checked against *successes*, so a provider that only ever
    timed out consumed no budget and a run could retry forever. Against a
    paid endpoint that is not a ceiling, it is a suggestion.
    """
    usage = LLMUsage()
    usage.start("planner")  # dispatched, never answered
    usage.start("planner")
    usage.record("planner", input_tokens=4, output_tokens=1)

    assert usage.attempts == 2
    assert usage.successes == 1
    assert usage.as_dict()["provider_request_attempts"] == 2
    assert usage.as_dict()["provider_successful_responses"] == 1
    # Tokens follow the response, not the attempt.
    assert usage.input_tokens == 4


async def test_unknown_role_raises_rather_than_returning_nothing() -> None:
    provider = FakeProvider()
    with pytest.raises(KeyError, match="no handler for role"):
        await provider.complete_json(request(role="astrologer"))


# ------------------------------------------------------------- fake provider


async def test_fake_provider_is_deterministic() -> None:
    a, b = FakeProvider(), FakeProvider()
    context = {
        "question": "Why did gross margin fall in Q3 2025?",
        "metrics": ["gross_margin_pct", "revenue"],
        "dimensions": ["category", "region"],
    }
    first = await a.complete_json(request(context=context, **context))
    second = await b.complete_json(request(context=context, **context))
    assert first == second


async def test_fake_provider_never_invents_a_number() -> None:
    """Findings are read out of the result payload it was given."""
    provider = FakeProvider()
    result = {
        "result_id": "res_x",
        "tool_name": "analyze_timeseries",
        "columns": ["period", "revenue", "prev_period", "change_abs", "change_pct"],
        "rows": [
            ["2025-01-01", 100.0, None, None, None],
            ["2025-04-01", 150.0, 100.0, 50.0, 50.0],
        ],
        "row_count": 2,
    }
    payload = await provider.complete_json(
        request(role="worker_findings", task={"task_id": "t1"}, results=[result])
    )
    findings = payload["findings"]
    assert findings
    from agentic_analytics.verification.numeric import extract_numbers

    stated = {n for f in findings for n in extract_numbers(f["text"])}
    available = {100.0, 150.0, 50.0}
    for number in stated:
        assert any(abs(number - v) < 0.01 for v in available), number


async def test_fake_provider_declares_no_credential_requirement() -> None:
    assert FakeProvider().requires_credentials is False


# ---------------------------------------------------------------- registry


def test_registry_defaults_to_the_scripted_provider() -> None:
    provider = build_provider(Settings())
    assert provider.name == "fake"
    assert provider.requires_credentials is False


def test_registry_builds_the_local_provider_without_a_credential() -> None:
    provider = build_provider(Settings(provider_mode="local"))
    assert provider.name == "local"
    assert provider.requires_credentials is False


def test_cloud_mode_without_a_key_is_refused() -> None:
    with pytest.raises(LLMError, match="requires AAE_CLOUD_API_KEY"):
        build_provider(Settings(provider_mode="cloud", cloud_api_key=None))


def test_registry_honours_the_llm_call_budget() -> None:
    settings = Settings()
    settings.budgets.max_llm_calls = 5
    assert build_provider(settings).max_calls == 5


# ------------------------------------------------------------------ ollama


async def test_ollama_provider_parses_a_response() -> None:
    from agentic_analytics.llm.ollama import OllamaProvider

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/chat"
        body = req.read().decode()
        assert '"stream":false' in body.replace(" ", "")
        return httpx.Response(
            200,
            json={
                "message": {"content": '{"intent": "test", "target_metrics": []}'},
                "prompt_eval_count": 42,
                "eval_count": 7,
            },
        )

    provider = OllamaProvider("http://local", "model")
    provider._client = httpx.AsyncClient(
        base_url="http://local", transport=httpx.MockTransport(handler)
    )
    try:
        result = await provider.complete_json(request())
        assert result["intent"] == "test"
        assert provider.usage.input_tokens == 42
        assert provider.usage.output_tokens == 7
    finally:
        await provider.aclose()


async def test_ollama_errors_are_sanitised() -> None:
    from agentic_analytics.llm.ollama import OllamaProvider

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key sk-secret123456789"})

    provider = OllamaProvider("http://local", "model")
    provider._client = httpx.AsyncClient(
        base_url="http://local", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(LLMError) as excinfo:
            await provider.complete_json(request())
        assert "sk-secret" not in str(excinfo.value)
    finally:
        await provider.aclose()


# ------------------------------------------------------------------- cloud


async def test_cloud_provider_uses_a_forced_tool_call() -> None:
    from agentic_analytics.llm.cloud import RESPONSE_TOOL, CloudProvider

    def handler(req: httpx.Request) -> httpx.Response:
        payload = httpx.Request("POST", req.url, content=req.read()).read().decode()
        assert RESPONSE_TOOL in payload
        assert req.headers["x-api-key"] == "test-key"
        return httpx.Response(
            200,
            json={
                "content": [{"type": "tool_use", "name": RESPONSE_TOOL, "input": {"intent": "ok"}}],
                "usage": {"input_tokens": 11, "output_tokens": 3},
            },
        )

    provider = CloudProvider(api_key="test-key", model="m")
    provider._client = httpx.AsyncClient(
        base_url="http://cloud",
        transport=httpx.MockTransport(handler),
        headers={"x-api-key": "test-key"},
    )
    try:
        assert (await provider.complete_json(request()))["intent"] == "ok"
        assert provider.usage.input_tokens == 11
    finally:
        await provider.aclose()


async def test_cloud_provider_rejects_a_response_with_no_tool_call() -> None:
    from agentic_analytics.llm.cloud import CloudProvider

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": [{"type": "text", "text": "hi"}], "usage": {}})

    provider = CloudProvider(api_key="k", model="m")
    provider._client = httpx.AsyncClient(
        base_url="http://cloud", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(LLMError, match="did not return a structured answer"):
            await provider.complete_json(request())
    finally:
        await provider.aclose()


def test_cloud_provider_refuses_to_construct_without_a_key() -> None:
    from agentic_analytics.llm.cloud import CloudProvider

    with pytest.raises(LLMError, match="no API key"):
        CloudProvider(api_key="", model="m")


def test_base_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        LLMProvider()  # type: ignore[abstract]


# ------------------------------------------------- cloud: the hard ceiling


async def test_a_cloud_call_that_never_returns_is_bounded_by_the_application() -> None:
    """A connection that is accepted and then silent must still end.

    The local provider already carries this bound, added after a run wedged
    for twenty minutes: socket ESTABLISHED, no bytes moving, the HTTP
    client's read timeout never firing. Nothing about that failure is
    specific to Ollama -- it is what "accepted, then nothing" looks like
    from the client side -- so the cloud adapter needs the same backstop
    before it is ever pointed at a paid endpoint.

    The mock transport here never responds at all. If the `asyncio.timeout`
    were removed, this test would hang rather than fail, which is exactly
    the production behaviour it exists to prevent.
    """
    import asyncio

    from structlog.testing import capture_logs

    from agentic_analytics.llm.cloud import CloudProvider

    entered = asyncio.Event()

    async def never_responds(_: httpx.Request) -> httpx.Response:
        entered.set()
        await asyncio.Event().wait()  # pragma: no cover - never completes
        raise AssertionError("unreachable")

    provider = CloudProvider(api_key="sk-secret-key-value", model="m", timeout_seconds=0.25)
    # Keep the real headers: the point is partly that the key does not leak.
    provider._client = httpx.AsyncClient(
        base_url="http://cloud",
        transport=httpx.MockTransport(never_responds),
        # Deliberately far longer than the application ceiling, so only the
        # `asyncio.timeout` can be what ends this call.
        timeout=httpx.Timeout(600.0),
        headers={"x-api-key": "sk-secret-key-value"},
    )

    started = asyncio.get_running_loop().time()
    try:
        with capture_logs() as captured, pytest.raises(LLMError) as raised:
            await provider.complete_json(request(role="planner"))
        elapsed = asyncio.get_running_loop().time() - started
    finally:
        # Closing after a timed-out call must not raise or hang.
        await provider.aclose()

    assert entered.is_set(), "the request never reached the transport"
    assert elapsed < 10, f"the application ceiling did not fire (took {elapsed:.1f}s)"

    error = raised.value
    assert error.kind == "timeout", f"misclassified as {error.kind}"
    assert str(error) == "the language model did not respond in time"

    # The attempt is charged even though no response arrived. A budget that
    # only counts successes lets a provider failing every call retry until
    # the wall clock runs out.
    assert provider.usage.attempts == 1
    assert provider.usage.successes == 0
    assert provider.usage.as_dict()["provider_request_attempts"] == 1
    assert provider.usage.as_dict()["provider_successful_responses"] == 0

    records = [e for e in captured if e.get("event") == "cloud_call_failed"]
    assert records, f"no cloud_call_failed record was emitted; saw {captured}"
    record = records[0]
    assert record["error_type"] in {"TimeoutError", "CancelledError"}
    assert record["role"] == "planner"
    assert record["model"] == "m"

    # Nothing anywhere in the raised message or the log carries the key,
    # the header name, or a response body.
    surfaces = [str(error), repr(record)]
    for surface in surfaces:
        assert "sk-secret-key-value" not in surface
        assert "x-api-key" not in surface.lower()


async def test_the_cloud_ceiling_is_configured_not_hardcoded() -> None:
    from agentic_analytics.llm.cloud import CloudProvider

    provider = CloudProvider(api_key="k", model="m", timeout_seconds=7.5)
    try:
        assert provider.call_timeout_seconds == 7.5
    finally:
        await provider.aclose()


def test_the_cloud_ceiling_reaches_the_provider_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A setting nothing reads is a comment with a type annotation."""
    monkeypatch.setenv("AAE_PROVIDER_MODE", "cloud")
    monkeypatch.setenv("AAE_CLOUD_API_KEY", "k")
    monkeypatch.setenv("AAE_CLOUD_TIMEOUT_SECONDS", "33")
    cfg = Settings()
    assert cfg.cloud_timeout_seconds == 33.0
    provider = build_provider(cfg)
    assert provider.call_timeout_seconds == 33.0  # type: ignore[attr-defined]


def test_the_cloud_ceiling_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AAE_CLOUD_TIMEOUT_SECONDS", "0")
    with pytest.raises(ValueError):
        Settings()
