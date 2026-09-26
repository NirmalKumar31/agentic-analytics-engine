"""Provider-neutral LLM interface.

Agents describe what they want as a :class:`LLMRequest` carrying a JSON schema
for the answer. Providers return parsed JSON. Nothing above this layer knows
whether the text came from a scripted stand-in, a local Ollama model, or a
cloud API.

``LLMRequest.context`` deserves an explanation. ``system`` and ``user`` are the
real prompt and are complete on their own -- a live provider sees only those.
``context`` is the same information in structured form, and only the scripted
provider reads it. Carrying it avoids having the stand-in re-parse a prompt it
already knows the shape of, and keeps the live prompt honest rather than
shaped around what a fake can parse.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

#: Why a structured call failed, as a stable identifier.
#:
#: The distinction is load-bearing for evaluation. "The model returned
#: something the schema rejected" and "the HTTP request timed out" are
#: completely different statements about a model, and collapsing both into
#: one error type -- which is what happened -- makes a report that cannot
#: answer the question it claims to answer.
FailureKind = Literal[
    "transport_error",
    "timeout",
    "json_parse_error",
    "schema_validation_error",
    "budget_exhausted",
    "unknown",
]


class LLMError(RuntimeError):
    """A provider failed. Messages are sanitised before they reach a user."""

    def __init__(self, message: str, kind: FailureKind = "unknown") -> None:
        super().__init__(message)
        self.kind: FailureKind = kind


class BudgetError(LLMError):
    """The run reached its LLM call ceiling."""

    def __init__(self, message: str) -> None:
        super().__init__(message, kind="budget_exhausted")


class LLMRequest(BaseModel):
    """One structured completion request."""

    role: str
    system: str
    user: str
    # JSON Schema the answer must satisfy.
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    context: dict[str, Any] = Field(default_factory=dict)
    max_tokens: int = 2048
    temperature: float = 0.0

    model_config = {"populate_by_name": True}


@dataclass
class LLMUsage:
    """Request accounting for one run.

    Attempts and successes are counted separately, and the ceiling is
    enforced on **attempts**. It used to be enforced on successes, which
    meant a failing provider consumed no budget at all: a run could time out
    against a paid endpoint indefinitely and never reach its limit. For an
    anonymous cloud deployment that is the difference between a budget and a
    suggestion.

    Tokens are recorded from what the provider actually reported, so they
    follow successes and stay zero for an attempt that returned nothing.
    """

    attempts: int = 0
    successes: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_role: dict[str, int] = field(default_factory=dict)

    @property
    def calls(self) -> int:
        """Successful responses. Kept for readers that predate `successes`."""
        return self.successes

    def start(self, role: str) -> None:
        """Reserve one attempt, before the request is dispatched."""
        self.attempts += 1
        self.by_role[role] = self.by_role.get(role, 0) + 1

    def record(self, role: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
        """A provider answered. Tokens are whatever it reported."""
        self.successes += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_request_attempts": self.attempts,
            "provider_successful_responses": self.successes,
            # Retained so existing readers and recordings keep working.
            "llm_calls": self.successes,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "by_role": dict(self.by_role),
        }


class LLMProvider(ABC):
    """What every provider must implement."""

    name: str = "base"
    #: True when calling this provider costs money or needs a credential.
    requires_credentials: bool = False

    def __init__(self, max_calls: int = 40) -> None:
        self.usage = LLMUsage()
        self.max_calls = max_calls

    def _check_budget(self, role: str = "unknown") -> None:
        """Reserve one attempt, or refuse before anything is dispatched.

        Counting the attempt here rather than on success is the whole point:
        a request that times out has still been made, has still cost the
        provider's time and possibly money, and must still consume budget.
        """
        if self.usage.attempts >= self.max_calls:
            raise BudgetError(f"run reached its ceiling of {self.max_calls} model request attempts")
        self.usage.start(role)

    @abstractmethod
    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        """Return parsed JSON satisfying ``request.schema_``."""

    async def aclose(self) -> None:
        """Release any transport resources."""
        return None


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a model response.

    Models wrap JSON in prose or code fences often enough that a bare
    ``json.loads`` is not a realistic parser. Failing loudly here is
    deliberate: a silently empty object would become a run with no plan.
    """
    candidates: list[str] = []
    stripped = text.strip()
    if stripped:
        candidates.append(stripped)
    for match in _JSON_BLOCK.finditer(text):
        candidates.append(match.group(1).strip())
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    raise LLMError("provider did not return a JSON object", kind="json_parse_error")


def sanitize_provider_error(exc: BaseException) -> str:
    """A user-safe description of a provider failure.

    Provider errors can carry request URLs, headers and occasionally key
    fragments. Only the exception type and a short generic reason are
    surfaced; the detail goes to the structured log.
    """
    name = type(exc).__name__
    text = str(exc).lower()
    # By type first, then by message. `asyncio.TimeoutError` and
    # `httpx.ReadTimeout` both stringify to the empty string, so a
    # message-only test classified them as a generic failure -- which is how
    # a run that hung for twenty minutes got reported as "call failed".
    if isinstance(exc, TimeoutError) or "timeout" in name.lower():
        return "the language model did not respond in time"
    if "timeout" in text or "timed out" in text:
        return f"the language model did not respond in time ({name})"
    if "connect" in text or "refused" in text:
        return f"the language model could not be reached ({name})"
    if "auth" in text or "401" in text or "403" in text or "api key" in text:
        return "the language model rejected the configured credentials"
    if "rate" in text and "limit" in text:
        return "the language model rate-limited this run"
    return f"the language model call failed ({name})"
