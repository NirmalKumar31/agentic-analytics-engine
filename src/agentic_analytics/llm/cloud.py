"""Cloud provider adapter (Anthropic Messages API).

Configured only through the environment. This module is never imported unless
``AAE_PROVIDER_MODE=cloud``, and the default configuration never selects it, so
no test, CI job or demo can reach a paid endpoint by accident.

Structured output is obtained with a single-tool forced tool call, which is the
supported way to constrain the Messages API to a JSON schema.
"""

from __future__ import annotations

from typing import Any

import httpx

from agentic_analytics.llm.base import (
    LLMError,
    LLMProvider,
    LLMRequest,
    sanitize_provider_error,
)
from agentic_analytics.logging import get_logger

log = get_logger(__name__)

ANTHROPIC_VERSION = "2023-06-01"
RESPONSE_TOOL = "emit_structured_answer"


class CloudProvider(LLMProvider):
    """Anthropic Messages API."""

    name = "cloud"
    requires_credentials = True

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.anthropic.com",
        max_calls: int = 40,
        timeout_seconds: float = 120.0,
    ) -> None:
        super().__init__(max_calls=max_calls)
        if not api_key:
            raise LLMError("cloud provider selected but no API key is configured")
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
        )

    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        self._check_budget()
        schema = request.schema_ or {"type": "object"}
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
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
        try:
            response = await self._client.post("/v1/messages", json=payload)
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            log.warning("cloud_call_failed", role=request.role, error=type(exc).__name__)
            raise LLMError(sanitize_provider_error(exc)) from None

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
        raise LLMError("cloud provider did not return a structured answer")

    async def aclose(self) -> None:
        await self._client.aclose()
