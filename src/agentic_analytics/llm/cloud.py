"""Cloud provider adapter (Anthropic Messages API).

Configured only through the environment. Nothing here is reachable without
an explicit AI run: the web application builds a provider per run from a
validated mode, and every production path wraps this class in
:mod:`agentic_analytics.llm.governed`, which admits and charges each call
against a durable ledger. Constructing this class directly is a test-only
affordance.

Structured output is obtained with a single-tool forced tool call, which is the
supported way to constrain the Messages API to a JSON schema.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from agentic_analytics.llm.base import (
    FailureKind,
    LLMError,
    LLMProvider,
    LLMRequest,
    sanitize_provider_error,
)
from agentic_analytics.logging import get_logger

log = get_logger(__name__)

ANTHROPIC_VERSION = "2023-06-01"
RESPONSE_TOOL = "emit_structured_answer"

#: Sampling fields are omitted by default. Some current models reject a
#: non-default `temperature`, `top_p` or `top_k` outright, and the engine
#: does not depend on a particular sampling setting: determinism comes from
#: the analytics layer, not from the model's decoding. Sending nothing is
#: both more portable and closer to what the engine actually requires.
#:
#: `AAE_CLOUD_SEND_TEMPERATURE=true` restores the old behaviour for a model
#: whose documentation permits the exact value supplied.
SAMPLING_FIELDS = ("temperature", "top_p", "top_k")


def _status_message(status: int) -> str:
    """A user-safe description of an HTTP failure, keyed by status."""
    if status in (401, 403):
        return "the language model rejected the configured credentials"
    if status == 429:
        return "the language model rate-limited this run"
    if status == 404:
        return "the configured language model was not found"
    if status == 400:
        return "the language model rejected the request as malformed"
    if 500 <= status < 600:
        return f"the language model service returned an error ({status})"
    return f"the language model call failed ({status})"


class CloudProvider(LLMProvider):
    """Anthropic Messages API."""

    name = "cloud"
    requires_credentials = True
    remote_inference = True

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.anthropic.com",
        max_calls: int = 40,
        timeout_seconds: float = 120.0,
        send_temperature: bool = False,
    ) -> None:
        super().__init__(max_calls=max_calls)
        if not api_key:
            raise LLMError(
                "cloud provider selected but no API key is configured",
                kind="transport_error",
            )
        self.model = model
        self.send_temperature = send_temperature
        #: Set by `verify_model`, so an artifact can record which model
        #: actually answered rather than which one was requested.
        self.resolved_model: dict[str, Any] | None = None
        #: Hard ceiling on one call, enforced by us. The httpx timeout below
        #: is kept as well; this is the backstop for when it does not fire.
        #: Not hypothetical: an evaluation run against a local model wedged
        #: for twenty minutes on one call with the socket ESTABLISHED, no
        #: bytes moving and the client's read timeout never firing. The
        #: failure mode is a property of "connection open, nothing arrives",
        #: not of any one vendor, so the bound belongs on both providers.
        self.call_timeout_seconds = timeout_seconds
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
        )

    def build_payload(self, request: LLMRequest) -> dict[str, Any]:
        """The exact body that will be sent.

        Public and single-sourced so a token count is taken over the same
        system text, messages, tools, schema and tool choice that the
        Messages request carries. Counting a differently-shaped payload
        would produce a number that bounds nothing.
        """
        schema = request.schema_ or {"type": "object"}
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
            "tools": [
                {
                    "name": RESPONSE_TOOL,
                    "description": "Return the structured answer.",
                    "input_schema": schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": RESPONSE_TOOL},
        }
        if self.send_temperature:
            payload["temperature"] = request.temperature
        return payload

    async def count_input_tokens(self, request: LLMRequest) -> int:
        """Count the request's input tokens before creating a Message.

        Uses the provider's own counting endpoint rather than a character
        heuristic, because the number is a financial control: an estimate
        that is low under-reserves exactly when a prompt is unusual.
        `max_tokens` is not part of a count request.
        """
        payload = {k: v for k, v in self.build_payload(request).items() if k != "max_tokens"}
        try:
            async with asyncio.timeout(self.call_timeout_seconds):
                response = await self._client.post("/v1/messages/count_tokens", json=payload)
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as exc:
            log.warning(
                "cloud_token_count_failed",
                role=request.role,
                status=exc.response.status_code,
                model=self.model,
            )
            raise LLMError(
                _status_message(exc.response.status_code), kind="transport_error"
            ) from None
        except Exception as exc:
            log.warning(
                "cloud_token_count_failed",
                role=request.role,
                error_type=type(exc).__name__,
                model=self.model,
            )
            raise LLMError(sanitize_provider_error(exc), kind="transport_error") from None

        counted = body.get("input_tokens")
        if not isinstance(counted, int) or counted < 0:
            raise LLMError(
                "the provider did not return a usable token count",
                kind="json_parse_error",
            )
        return counted

    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        self._check_budget(request.role)
        payload = self.build_payload(request)
        try:
            async with asyncio.timeout(self.call_timeout_seconds):
                response = await self._client.post("/v1/messages", json=payload)
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as exc:
            # Classified from the status code rather than the message. A
            # 429 stringifies as "Too Many Requests", which no
            # string-matching sanitiser recognises as rate limiting, and
            # telling a rate limit from a generic failure is the difference
            # between backing off and retrying into a wall.
            log.warning(
                "cloud_call_failed",
                role=request.role,
                error_type=type(exc).__name__,
                status=exc.response.status_code,
                model=self.model,
            )
            raise LLMError(
                _status_message(exc.response.status_code), kind="transport_error"
            ) from None
        except Exception as exc:
            # The exception type carries more than the message does:
            # `httpx.ReadTimeout` stringifies to the empty string, so a log
            # line built from `str(exc)` alone reads `error=` and tells an
            # operator nothing. Never log the response body or the headers:
            # one of them is the API key.
            log.warning(
                "cloud_call_failed",
                role=request.role,
                error_type=type(exc).__name__,
                error=str(exc) or "(no message)",
                model=self.model,
            )
            kind: FailureKind = (
                "timeout"
                if isinstance(exc, TimeoutError | httpx.TimeoutException)
                else "transport_error"
            )
            raise LLMError(sanitize_provider_error(exc), kind=kind) from None

        usage = body.get("usage") or {}
        self.usage.record(
            request.role,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
        )
        for block in body.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == RESPONSE_TOOL:
                result = block.get("input")
                if isinstance(result, dict):
                    return result
        raise LLMError(
            "cloud provider did not return a structured answer",
            kind="json_parse_error",
        )

    async def verify_model(self) -> dict[str, Any]:
        """Confirm the configured model exists, without spending a completion.

        A wrong or retired model id otherwise surfaces as a failed completion
        -- an attempt against the budget, and an error that looks like a
        model problem rather than a configuration one. The Models endpoint
        answers the question directly and costs nothing.
        """
        try:
            async with asyncio.timeout(self.call_timeout_seconds):
                response = await self._client.get(f"/v1/models/{self.model}")
        except Exception as exc:
            log.warning(
                "cloud_model_preflight_failed",
                error_type=type(exc).__name__,
                model=self.model,
            )
            raise LLMError(sanitize_provider_error(exc), kind="transport_error") from None

        if response.status_code == 404:
            raise LLMError(
                f"the configured model {self.model!r} is not available to this "
                "credential; check AAE_CLOUD_MODEL",
                kind="transport_error",
            )
        if response.status_code in (401, 403):
            raise LLMError(
                "the language model rejected the configured credentials",
                kind="transport_error",
            )
        if response.status_code >= 400:
            raise LLMError(
                f"the model registry returned {response.status_code}",
                kind="transport_error",
            )

        body = response.json()
        self.resolved_model = {
            "id": str(body.get("id", self.model)),
            "display_name": str(body.get("display_name", "")),
            "created_at": str(body.get("created_at", "")),
            "type": str(body.get("type", "")),
        }
        log.info("cloud_model_verified", model=self.resolved_model["id"])
        return self.resolved_model

    async def aclose(self) -> None:
        await self._client.aclose()
