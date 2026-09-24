"""Shared plumbing for agent calls."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from agentic_analytics.llm.base import LLMError, LLMProvider, LLMRequest
from agentic_analytics.logging import get_logger

log = get_logger(__name__)


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


def parse_into[ModelT: BaseModel](
    model: type[ModelT], payload: dict[str, Any], role: str
) -> ModelT:
    """Validate a provider payload into a schema, or raise a clear error."""
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        log.warning("agent_output_invalid", role=role, errors=exc.error_count())
        raise LLMError(
            f"the {role} returned a response that did not match the expected shape"
        ) from None


def schema_of(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema for a model, for providers that constrain output."""
    return model.model_json_schema()


def bullet_list(items: list[str], empty: str = "(none)") -> str:
    return "\n".join(f"- {item}" for item in items) if items else empty
