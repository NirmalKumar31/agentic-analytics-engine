"""A structured call can fail at three different places. Say which.

The real-model harness claimed to report "whether each agent role returned
output the schema accepted". It did not: it watched `complete_json`, which
succeeds whenever the provider returns *a dict*. A dict that then fails
`model_validate` is a model breaking its contract, and that was recorded as a
success. An HTTP timeout, meanwhile, was recorded as a format failure.

`ask_into` is the one place both steps happen, so it is the one place the
stage can be observed honestly.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel

from agentic_analytics.agents.base import (
    StructuredCall,
    ask_into,
    observe_structured_calls,
)
from agentic_analytics.llm.base import BudgetError, LLMError, LLMProvider, LLMRequest


class Answer(BaseModel):
    intent: str
    count: int


class Scripted(LLMProvider):
    """Returns a payload, or raises whatever it was given."""

    name = "scripted"
    requires_credentials = False

    def __init__(self, payload: Any = None, error: BaseException | None = None) -> None:
        super().__init__()
        self.payload = payload
        self.error = error

    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        return dict(self.payload or {})


async def _call(provider: LLMProvider) -> tuple[list[StructuredCall], BaseException | None]:
    seen: list[StructuredCall] = []
    raised: BaseException | None = None
    with observe_structured_calls(seen.append):
        try:
            await ask_into(provider, Answer, role="planner", system="s", user="u", context={})
        except BaseException as exc:
            raised = exc
    return seen, raised


async def test_a_valid_answer_passes_every_stage() -> None:
    seen, raised = await _call(Scripted({"intent": "x", "count": 1}))
    assert raised is None
    assert len(seen) == 1
    call = seen[0]
    assert call.stage == "success"
    assert call.transport_ok and call.json_object_ok and call.schema_validation_ok
    assert call.role == "planner"
    assert call.seconds >= 0


async def test_valid_json_that_violates_the_schema_is_a_schema_failure() -> None:
    """The case the old instrumentation could not see.

    The provider succeeded. It returned a dict. The dict is wrong.
    """
    seen, raised = await _call(Scripted({"intent": "x", "count": "not a number"}))
    assert isinstance(raised, LLMError)
    call = seen[0]
    assert call.stage == "schema_validation_error"
    # Transport and JSON both worked -- that is the whole point.
    assert call.transport_ok is True
    assert call.json_object_ok is True
    assert call.schema_validation_ok is False
    assert raised.kind == "schema_validation_error"


async def test_a_missing_field_is_also_a_schema_failure() -> None:
    seen, _ = await _call(Scripted({"intent": "x"}))
    assert seen[0].stage == "schema_validation_error"


async def test_malformed_json_is_a_json_failure_not_a_schema_failure() -> None:
    error = LLMError("provider did not return a JSON object", kind="json_parse_error")
    seen, raised = await _call(Scripted(error=error))
    call = seen[0]
    assert call.stage == "json_parse_error"
    assert call.transport_ok is True
    assert call.json_object_ok is False
    assert call.schema_validation_ok is False
    assert isinstance(raised, LLMError)


async def test_a_timeout_is_not_a_schema_failure() -> None:
    """The misclassification that made the first sweep unreadable."""
    error = LLMError("the language model did not respond in time", kind="timeout")
    seen, _ = await _call(Scripted(error=error))
    call = seen[0]
    assert call.stage == "timeout"
    assert call.transport_ok is False
    assert call.schema_validation_ok is False


async def test_a_transport_error_is_not_a_schema_failure() -> None:
    error = LLMError("the language model could not be reached", kind="transport_error")
    seen, _ = await _call(Scripted(error=error))
    assert seen[0].stage == "transport_error"
    assert seen[0].transport_ok is False


async def test_budget_exhaustion_is_its_own_stage() -> None:
    seen, raised = await _call(Scripted(error=BudgetError("out of calls")))
    assert seen[0].stage == "budget_exhausted"
    assert isinstance(raised, BudgetError)
    assert raised.kind == "budget_exhausted"


async def test_no_observer_means_no_overhead_and_no_error() -> None:
    """Production runs install nothing; the call must behave identically."""
    provider = Scripted({"intent": "x", "count": 2})
    answer = await ask_into(provider, Answer, role="planner", system="s", user="u", context={})
    assert answer.count == 2


async def test_an_observer_that_raises_cannot_break_a_run() -> None:
    def explode(call: StructuredCall) -> None:
        raise RuntimeError("telemetry is not allowed to matter")

    with observe_structured_calls(explode):
        answer = await ask_into(
            Scripted({"intent": "x", "count": 3}),
            Answer,
            role="planner",
            system="s",
            user="u",
            context={},
        )
    assert answer.count == 3


async def test_observation_reaches_concurrent_tasks() -> None:
    """Workers fan out into tasks; a ContextVar is copied into each."""
    seen: list[StructuredCall] = []

    async def one(index: int) -> None:
        await ask_into(
            Scripted({"intent": str(index), "count": index}),
            Answer,
            role=f"worker_{index}",
            system="s",
            user="u",
            context={},
        )

    with observe_structured_calls(seen.append):
        await asyncio.gather(*(one(i) for i in range(4)))

    assert len(seen) == 4
    assert {c.role for c in seen} == {f"worker_{i}" for i in range(4)}


async def test_the_observer_is_removed_when_the_block_ends() -> None:
    seen: list[StructuredCall] = []
    with observe_structured_calls(seen.append):
        await ask_into(
            Scripted({"intent": "a", "count": 1}),
            Answer,
            role="r",
            system="s",
            user="u",
            context={},
        )
    await ask_into(
        Scripted({"intent": "b", "count": 2}),
        Answer,
        role="r",
        system="s",
        user="u",
        context={},
    )
    assert len(seen) == 1, "calls outside the block were still observed"
