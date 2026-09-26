"""Ollama-compatible local provider.

Talks to the native ``/api/chat`` endpoint with ``format`` set to the
requested JSON schema, which current Ollama honours as structured output. No
credential is involved; the only requirement is a reachable server.
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
    extract_json,
    sanitize_provider_error,
)
from agentic_analytics.logging import get_logger

log = get_logger(__name__)


class OllamaProvider(LLMProvider):
    """Local model over HTTP."""

    name = "local"
    requires_credentials = False

    def __init__(
        self,
        base_url: str,
        model: str,
        max_calls: int = 40,
        timeout_seconds: float = 180.0,
        think: bool = False,
    ) -> None:
        super().__init__(max_calls=max_calls)
        self.base_url = base_url.rstrip("/")
        self.model = model
        #: Whether a reasoning-capable model may emit its thinking.
        #:
        #: Off by default, and this is not a preference. Every role here asks
        #: for a JSON object against a schema and has a bounded output
        #: budget. A thinking model spends that budget on reasoning first:
        #: qwen3:4b answered a trivial question in 91 seconds, used all 512
        #: permitted tokens, and returned JSON that was cut off mid-value --
        #: an unparseable answer, not a slow one. The same call with
        #: thinking off took 2.2 seconds and returned clean JSON.
        #:
        #: Ollama accepts the field for models without the capability and
        #: ignores it, so this is safe to send unconditionally.
        self.think = think
        #: Hard ceiling on one call, enforced by us. The httpx timeout is
        #: kept as well; this is the backstop for when it does not fire.
        self.call_timeout_seconds = timeout_seconds
        self._client = httpx.AsyncClient(
            base_url=self.base_url, timeout=httpx.Timeout(timeout_seconds)
        )

    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        self._check_budget()
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            },
        }
        if request.schema_:
            payload["format"] = request.schema_
        payload["think"] = self.think

        try:
            # Bounded here rather than trusted to the HTTP client. A run of
            # this evaluation wedged for twenty minutes on a single call:
            # the socket to Ollama stayed ESTABLISHED with no bytes moving,
            # the server was idle, and httpx's read timeout never fired. An
            # agent loop that can block forever is worse than one that
            # fails, because nothing downstream gets a chance to react.
            async with asyncio.timeout(self.call_timeout_seconds):
                response = await self._client.post("/api/chat", json=payload)
                response.raise_for_status()
                body = response.json()
        except Exception as exc:
            # The type matters as much as the message: `httpx.ReadTimeout`
            # stringifies to the empty string, so logging `str(exc)` alone
            # produced `error=` and told an operator nothing at all.
            log.warning(
                "ollama_call_failed",
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

        content = (body.get("message") or {}).get("content", "")
        self.usage.record(
            request.role,
            input_tokens=int(body.get("prompt_eval_count", 0)),
            output_tokens=int(body.get("eval_count", 0)),
        )
        return extract_json(content)

    async def aclose(self) -> None:
        await self._client.aclose()
