"""Ollama-compatible local provider.

Talks to the native ``/api/chat`` endpoint with ``format`` set to the
requested JSON schema, which current Ollama honours as structured output. No
credential is involved; the only requirement is a reachable server.
"""

from __future__ import annotations

from typing import Any

import httpx

from agentic_analytics.llm.base import (
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
    ) -> None:
        super().__init__(max_calls=max_calls)
        self.base_url = base_url.rstrip("/")
        self.model = model
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

        try:
            response = await self._client.post("/api/chat", json=payload)
            response.raise_for_status()
            body = response.json()
        except Exception as exc:
            log.warning("ollama_call_failed", role=request.role, error=str(exc))
            raise LLMError(sanitize_provider_error(exc)) from None

        content = (body.get("message") or {}).get("content", "")
        self.usage.record(
            request.role,
            input_tokens=int(body.get("prompt_eval_count", 0)),
            output_tokens=int(body.get("eval_count", 0)),
        )
        return extract_json(content)

    async def aclose(self) -> None:
        await self._client.aclose()
