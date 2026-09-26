"""Shared plumbing for agent calls.

Every agent role does the same two things: ask a provider for JSON, then
validate that JSON into a Pydantic model. `ask_into` is that pair, in one
place, so there is a single point where the outcome of a structured call can
be observed -- and observed *accurately*.

Accuracy is the reason this exists. Watching only the provider call records
"the provider returned a dict" as a success, which it is; but a dict that
then fails `model_validate` is a model that did not hold its contract, and a
report built on the first measurement cannot tell you that. An HTTP timeout
is not a schema failure either, and collapsing both into one error type makes
a real-model evaluation unable to answer the question it was built to ask.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from agentic_analytics.llm.base import (
    BudgetError,
    FailureKind,
    LLMError,
    LLMProvider,
    LLMRequest,
)
from agentic_analytics.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class StructuredCall:
    """What happened on one agent call, by stage.

    `stage` is `"success"` or one of the `FailureKind` values, so a caller
    can say *where* a call broke: the transport, the JSON, or the schema.
    """

    role: str
    stage: str
    seconds: float
    error_type: str = ""
    detail: str = ""

    @property
    def transport_ok(self) -> bool:
        """The provider returned something."""
        return self.stage not in {"transport_error", "timeout", "budget_exhausted"}

    @property
    def json_object_ok(self) -> bool:
        """That something was a JSON object."""
        return self.transport_ok and self.stage != "json_parse_error"

    @property
    def schema_validation_ok(self) -> bool:
        """And it satisfied the role's schema."""
        return self.stage == "success"


CallObserver = Callable[[StructuredCall], None]

#: A ContextVar rather than a global: an evaluation installs it around one
#: question, and asyncio copies the context into the worker tasks the graph
#: fans out, so parallel workers are observed without any of them knowing.
_observer: ContextVar[CallObserver | None] = ContextVar("structured_call_observer", default=None)


@contextmanager
def observe_structured_calls(observer: CallObserver) -> Iterator[None]:
    """Record the outcome of every agent call made inside this block."""
    token = _observer.set(observer)
    try:
        yield
    finally:
        _observer.reset(token)


def _emit(call: StructuredCall) -> None:
    observer = _observer.get()
    if observer is None:
        return
    try:
        observer(call)
    except Exception:  # pragma: no cover - telemetry must never break a run
        log.warning("structured_call_observer_failed", role=call.role)


async def ask(
    provider: LLMProvider,
    *,
    role: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    context: dict[str, Any],
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """One structured provider call."""
    request = LLMRequest(
        role=role,
        system=system,
        user=user,
        schema=schema,
        context=context,
        max_tokens=max_tokens,
    )
    return await provider.complete_json(request)


async def ask_into[ModelT: BaseModel](
    provider: LLMProvider,
    model: type[ModelT],
    *,
    role: str,
    system: str,
    user: str,
    context: dict[str, Any],
    max_tokens: int = 2048,
) -> ModelT:
    """Ask, then validate, reporting which of the two failed.

    The one place a structured agent call happens, so the one place its
    outcome can be observed. Raises `LLMError` exactly as the two calls it
    replaces did; the only addition is that the failure carries a `kind` and
    an observer is told.
    """
    started = time.monotonic()

    def finish(stage: str, exc: BaseException | None = None) -> None:
        _emit(
            StructuredCall(
                role=role,
                stage=stage,
                seconds=round(time.monotonic() - started, 3),
                error_type=type(exc).__name__ if exc else "",
                detail=(str(exc)[:200] if exc else ""),
            )
        )

    try:
        payload = await ask(
            provider,
            role=role,
            system=system,
            user=user,
            schema=schema_of(model),
            context=context,
            max_tokens=max_tokens,
        )
    except BudgetError as exc:
        finish("budget_exhausted", exc)
        raise
    except LLMError as exc:
        finish(getattr(exc, "kind", "unknown"), exc)
        raise

    try:
        validated = model.model_validate(payload)
    except ValidationError as exc:
        # Valid JSON the schema refused. This is the case the previous
        # instrumentation could not see, and the one that says the most
        # about whether a model can hold an agent contract.
        log.warning("agent_output_invalid", role=role, errors=exc.error_count())
        finish("schema_validation_error", exc)
        raise LLMError(
            f"the {role} returned a response that did not match the expected shape",
            kind="schema_validation_error",
        ) from None

    finish("success")
    return validated


def parse_into[ModelT: BaseModel](
    model: type[ModelT], payload: dict[str, Any], role: str
) -> ModelT:
    """Validate a provider payload into a schema, or raise a clear error.

    Kept for callers that already hold a payload. Prefer `ask_into`, which
    is observable.
    """
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        log.warning("agent_output_invalid", role=role, errors=exc.error_count())
        kind: FailureKind = "schema_validation_error"
        raise LLMError(
            f"the {role} returned a response that did not match the expected shape",
            kind=kind,
        ) from None


def schema_of(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema for a model, for providers that constrain output."""
    return model.model_json_schema()


def bullet_list(items: list[str], empty: str = "(none)") -> str:
    return "\n".join(f"- {item}" for item in items) if items else empty
