"""Cloud provider adapter (OpenAI Responses API).

Configured only through the environment. Nothing here is reachable without
an explicit AI run: the web application builds a provider per run from a
validated mode, and every production path wraps this class in
:mod:`agentic_analytics.llm.governed`, which admits and charges each call
against a durable ledger. Constructing this class directly is a test-only
affordance, with one exception noted on `verify_model`.

Structured output is obtained with strict Structured Outputs -- a
`json_schema` response format with `strict: true` -- which is the supported
way to constrain the Responses API to a JSON schema. The engine's schemas
are rewritten into the strict dialect by
:mod:`agentic_analytics.llm.strict_schema`.

Three endpoints are used, and only these three:

* ``GET  /v1/models/{model}``        -- resolve the model, no generation
* ``POST /v1/responses/input_tokens`` -- exact input count, no generation
* ``POST /v1/responses``            -- the one billable call

Contracts reviewed 2026-09-27 against:
  https://developers.openai.com/api/docs/guides/text
  https://developers.openai.com/api/docs/guides/structured-outputs
  https://developers.openai.com/api/docs/guides/token-counting
  https://developers.openai.com/api/reference/typescript/resources/models/methods/retrieve
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx

from agentic_analytics.llm.base import (
    FailureKind,
    LLMError,
    LLMProvider,
    LLMRequest,
    sanitize_provider_error,
)
from agentic_analytics.llm.strict_schema import StrictSchemaError, to_strict
from agentic_analytics.logging import get_logger

log = get_logger(__name__)

#: The name attached to the strict schema. Required by the API and otherwise
#: inert: it identifies the format, not the model or the run.
RESPONSE_FORMAT_NAME = "analysis_response"

#: Reasoning effort values this model accepts.
#: https://developers.openai.com/api/docs/models/gpt-6-luna
REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")

#: Never sent. The engine does not depend on a particular sampling setting --
#: determinism comes from the analytics layer, not from the model's decoding,
#: and a reasoning model rejects or ignores them. Named here so a test can
#: assert their absence rather than trusting that nobody adds one.
FORBIDDEN_SAMPLING_FIELDS = ("temperature", "top_p", "top_k", "top_logprobs", "logprobs")

#: Billing refusals. Documented as 429s, like ordinary throttling, but they
#: mean the opposite: waiting will not help, because the limit is the
#: account's rather than the moment's.
#: https://developers.openai.com/api/docs/guides/spend-limits
SPEND_LIMIT_CODES = frozenset(
    {
        "project_spend_limit_exceeded",
        "organization_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
        "credit_balance_exhausted",
    }
)


def _status_message(status: int, code: str = "") -> str:
    """A user-safe description of an HTTP failure, keyed by status and code."""
    if code in SPEND_LIMIT_CODES:
        return "the language model account has reached its spending limit"
    if status in (401, 403):
        return "the language model rejected the configured credentials"
    if status == 429:
        return "the language model rate-limited this run"
    if status == 404:
        return "the configured language model was not found"
    if status == 400:
        return "the language model rejected the request as malformed"
    if status == 503:
        return "the language model service is temporarily unavailable"
    if 500 <= status < 600:
        return f"the language model service returned an error ({status})"
    return f"the language model call failed ({status})"


def is_retryable_status(status: int, code: str = "") -> bool:
    """Whether waiting could plausibly change the outcome.

    A spend-limit refusal arrives as 429 and must not be treated as
    throttling: retrying it burns the run's attempts against a limit that
    will not lift, and every attempt is another request to an account that
    has already said no.
    """
    if code in SPEND_LIMIT_CODES:
        return False
    if status == 429:
        return True
    return 500 <= status < 600


def _error_code(response: httpx.Response) -> str:
    """The provider's own short error code, and nothing else from the body.

    Only this one small field is read. The body may echo the request, which
    can hold a prompt or a cell of an uploaded file, so it is never logged
    and never surfaced.
    """
    try:
        payload = response.json()
    except ValueError:
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return ""
    code = error.get("code")
    return code if isinstance(code, str) else ""


class CloudProvider(LLMProvider):
    """OpenAI Responses API."""

    name = "cloud"
    requires_credentials = True
    remote_inference = True

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com",
        max_calls: int = 40,
        timeout_seconds: float = 120.0,
        reasoning_effort: str = "low",
    ) -> None:
        super().__init__(max_calls=max_calls)
        if not api_key:
            raise LLMError(
                "cloud provider selected but no API key is configured",
                kind="transport_error",
            )
        if reasoning_effort not in REASONING_EFFORTS:
            raise LLMError(
                f"unsupported reasoning effort {reasoning_effort!r}; "
                f"choose one of {', '.join(REASONING_EFFORTS)}",
                kind="transport_error",
            )
        self.model = model
        self.reasoning_effort = reasoning_effort
        #: Set by `verify_model`, so an artifact can record which model
        #: actually answered rather than which one was requested.
        self.resolved_model: dict[str, Any] | None = None
        #: Reported by the last response, for observability only. Reasoning
        #: tokens are already inside `output_tokens`; keeping them separate
        #: is how that stays visible without being added twice.
        self.last_reasoning_tokens = 0
        self.last_cached_tokens = 0
        self.last_cache_write_tokens = 0
        #: The last call's full record, including whether it was readable.
        #: Kept for observability; a caller that needs to charge a specific
        #: call takes the record `complete_with_usage` returns to it.
        self.last_usage = UsageRecord(False, reason="no call has been made")
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
                "authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
        )

    # ----------------------------------------------------------- request
    def build_payload(self, request: LLMRequest) -> dict[str, Any]:
        """The exact body that will be sent.

        Public and single-sourced so a token count is taken over the same
        model, instructions, input and schema that the generating request
        carries. Counting a differently-shaped payload would produce a
        number that bounds nothing.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": request.system,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": request.user}],
                }
            ],
            "max_output_tokens": request.max_tokens,
            "reasoning": {"effort": self.reasoning_effort},
            # Stated rather than inherited. A project-level automatic, Flex
            # or Priority setting would otherwise change what a call costs
            # without changing anything in this repository, and the ledger
            # prices exactly one tier.
            "service_tier": "default",
            # The prompt can contain schema and results derived from a
            # visitor's uploaded file. Nothing about this run is kept by the
            # provider for later retrieval.
            "store": False,
            "text": {"format": self._response_format(request)},
        }
        return payload

    def _response_format(self, request: LLMRequest) -> dict[str, Any]:
        """The strict Structured Outputs format block."""
        schema = request.schema_ or {"type": "object", "properties": {}}
        try:
            strict = to_strict(schema, name=request.role)
        except StrictSchemaError as exc:
            # A schema that cannot be expressed is a programming error, not
            # a provider failure, so it says so rather than being reported
            # as the model having misbehaved.
            raise LLMError(
                f"the {request.role} response schema cannot be expressed as a "
                f"strict structured output: {exc}",
                kind="json_parse_error",
            ) from None
        return {
            "type": "json_schema",
            "name": RESPONSE_FORMAT_NAME,
            "strict": True,
            "schema": strict,
        }

    #: Fields removed before counting, and the only ones that may be.
    #:
    #: The endpoint is documented as taking "the same payload you would send
    #: to responses.create", so a field is dropped only when it cannot
    #: affect the input the model receives: how much output it may generate,
    #: whether the exchange is stored, and which billing tier serves it.
    #:
    #: `reasoning` used to be in this list and should not have been.
    #: Reasoning effort can change the hidden instructions the model is
    #: given, which is input, so counting without it produced a number
    #: about a request the model never sees, and a count that bounds a
    #: different request bounds nothing.
    #:   https://developers.openai.com/api/docs/guides/token-counting
    NON_INPUT_FIELDS = ("max_output_tokens", "store", "service_tier")

    def count_payload(self, request: LLMRequest) -> dict[str, Any]:
        """The body sent to the token-count endpoint.

        Derived from `build_payload` by removing only `NON_INPUT_FIELDS`, so
        the two cannot drift in anything that bears tokens.
        """
        payload = self.build_payload(request)
        for field in self.NON_INPUT_FIELDS:
            payload.pop(field, None)
        return payload

    # ------------------------------------------------------------- calls
    async def count_input_tokens(self, request: LLMRequest) -> int:
        """Count the request's input tokens before creating a Response.

        Uses the provider's own counting endpoint rather than a character
        heuristic, because the number is a financial control: an estimate
        that is low under-reserves exactly when a prompt is unusual. The
        count includes the formatting tokens that represent message
        structure, which a local tokeniser cannot see.
        """
        body = await self._post(
            "/v1/responses/input_tokens",
            self.count_payload(request),
            role=request.role,
            event="cloud_token_count_failed",
        )
        counted = body.get("input_tokens")
        if not isinstance(counted, int) or isinstance(counted, bool) or counted < 0:
            raise LLMError(
                "the provider did not return a usable token count",
                kind="json_parse_error",
            )
        return counted

    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        payload, _ = await self.complete_with_usage(request)
        return payload

    async def complete_with_usage(self, request: LLMRequest) -> tuple[dict[str, Any], UsageRecord]:
        """The answer and what the call reported using.

        Returned together, per call, rather than left on the provider for a
        caller to read afterwards: two concurrent calls would each overwrite
        the other's totals, and the caller would charge one call for the
        other's tokens.

        A response that arrives and then fails to parse has still been
        billed, so its record is attached to the exception. A caller that
        dropped it would let a failed call spend from outside every ceiling.
        """
        self._check_budget(request.role)
        body = await self._post(
            "/v1/responses",
            self.build_payload(request),
            role=request.role,
            event="cloud_call_failed",
        )
        record = read_usage(body)
        self._record_usage(request.role, record)
        if not record.valid:
            log.warning("cloud_usage_unusable", role=request.role, reason=record.reason)
        try:
            return _parse_structured_output(body), record
        except LLMError as exc:
            exc.usage = record  # type: ignore[attr-defined]
            raise

    async def _post(
        self, path: str, payload: dict[str, Any], *, role: str, event: str
    ) -> dict[str, Any]:
        """One POST, with the whole failure taxonomy in a single place."""
        try:
            async with asyncio.timeout(self.call_timeout_seconds):
                response = await self._client.post(path, json=payload)
                if response.status_code >= 400:
                    # Classified from the status code and the provider's own
                    # `error.code`, never from the message. A 429 stringifies
                    # as "Too Many Requests" whether it is throttling or a
                    # spend limit, and those need opposite responses.
                    code = _error_code(response)
                    log.warning(
                        event,
                        role=role,
                        status=response.status_code,
                        provider_error_code=code or "(none)",
                        retryable=is_retryable_status(response.status_code, code),
                        model=self.model,
                    )
                    raise LLMError(
                        _status_message(response.status_code, code),
                        kind="transport_error",
                    )
                return dict(response.json())
        except LLMError:
            raise
        except Exception as exc:
            # The exception type carries more than the message does:
            # `httpx.ReadTimeout` stringifies to the empty string, so a log
            # line built from `str(exc)` alone reads `error=` and tells an
            # operator nothing. Never log the response body or the headers:
            # one of them is the credential.
            log.warning(
                event,
                role=role,
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

    def _record_usage(self, role: str, record: UsageRecord) -> None:
        """Add a call's reported usage to this provider's running totals.

        Recorded before the output is parsed. A response that arrived and
        cannot be understood has still been billed, and a run that forgot to
        count it would under-report its own spending.
        """
        self.last_usage = record
        self.last_cached_tokens = record.cached_tokens
        self.last_cache_write_tokens = record.cache_write_tokens
        self.last_reasoning_tokens = record.reasoning_tokens
        self.usage.record(
            role,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
        )

    # -------------------------------------------------------- preflight
    async def verify_model(self) -> dict[str, Any]:
        """Confirm the configured model exists, without generating anything.

        A wrong or retired model id otherwise surfaces as a failed response
        -- an attempt against the budget, and an error that looks like a
        model problem rather than a configuration one. The Models endpoint
        answers the question directly and creates nothing.
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
        # Only these fields, and all of them stringified. The registry
        # response is not a place to discover new keys to publish.
        resolved: dict[str, Any] = {
            "id": str(body.get("id", self.model)),
            "object": str(body.get("object", "")),
            "created": str(body.get("created", "")),
            "owned_by": str(body.get("owned_by", "")),
        }
        shutdown = body.get("shutdown_date")
        if shutdown:
            resolved["shutdown_date"] = str(shutdown)
        self.resolved_model = resolved
        log.info("cloud_model_verified", model=resolved["id"])
        return resolved

    async def aclose(self) -> None:
        await self._client.aclose()


def _token_count(value: Any) -> int | None:
    """A reported token count, or `None` when it cannot be read.

    `None` rather than zero, which is the distinction this replaces. An
    absent, negative, boolean or string count used to become zero, and zero
    is a coherent, cheap, entirely believable number -- so a response whose
    usage could not be read priced as a free call and the ledger refunded
    the reservation. "Unknown" and "none" are different facts and the
    accounting depends on telling them apart.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


@dataclass(frozen=True)
class UsageRecord:
    """What one response reported about its own size.

    Per response, and immutable. The previous shape was a pair of
    attributes overwritten on each call, which is wrong twice over: it
    cannot describe concurrent calls, and it cannot describe a call whose
    usage was unreadable.

    `valid` is the whole point. When it is false the numbers here mean
    nothing and the caller must fall back to what it reserved.
    """

    valid: bool
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    #: Observability only. Already inside `output_tokens`; adding it would
    #: charge the same generation twice.
    reasoning_tokens: int = 0
    reason: str = ""


def read_usage(body: dict[str, Any]) -> UsageRecord:
    """Read a response's usage, refusing anything that does not add up.

    Strict on purpose. Input and output totals must both be present and
    readable; the cache categories default to zero only when absent, and a
    present-but-unreadable one invalidates the record rather than being
    treated as zero. The categories must fit inside the total they are part
    of.
    """
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return UsageRecord(False, reason="no usage was reported")

    total_in = _token_count(usage.get("input_tokens"))
    total_out = _token_count(usage.get("output_tokens"))
    if total_in is None or total_out is None:
        return UsageRecord(False, reason="reported token totals could not be read")

    details = usage.get("input_tokens_details")
    details = details if isinstance(details, dict) else {}
    cached = 0 if "cached_tokens" not in details else _token_count(details["cached_tokens"])
    written = (
        0 if "cache_write_tokens" not in details else _token_count(details["cache_write_tokens"])
    )
    if cached is None or written is None:
        return UsageRecord(False, reason="reported cache token counts could not be read")
    if cached + written > total_in:
        return UsageRecord(False, reason="reported cache tokens exceed the input total")

    out_details = usage.get("output_tokens_details")
    out_details = out_details if isinstance(out_details, dict) else {}
    reasoning = _token_count(out_details.get("reasoning_tokens")) or 0

    return UsageRecord(
        True,
        input_tokens=total_in,
        output_tokens=total_out,
        cached_tokens=cached,
        cache_write_tokens=written,
        reasoning_tokens=reasoning,
    )


def _parse_structured_output(body: dict[str, Any]) -> dict[str, Any]:
    """Find the one structured answer in a Responses body.

    The output is a typed array, not a single message: a reasoning item
    comes first and the assistant message after it, so `output[0]` is
    routinely the wrong thing. Every failure below is a case that has to be
    told apart from the others, because they need different responses --
    a refusal is the model declining, an incomplete response is a ceiling
    being hit, and missing text is a contract violation.
    """
    status = body.get("status")
    if status == "incomplete":
        details = body.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, dict) else None
        if reason == "max_output_tokens":
            raise LLMError(
                "the language model ran out of output tokens before finishing its answer",
                kind="json_parse_error",
            )
        raise LLMError(
            f"the language model returned an incomplete response ({reason or 'unknown'})",
            kind="json_parse_error",
        )
    if status is not None and status != "completed":
        # `failed`, `cancelled`, or anything added later. The error object
        # carries a short code; the body it sits in is never surfaced.
        raise LLMError(
            f"the language model did not complete the request ({status})",
            kind="transport_error",
        )

    texts: list[str] = []
    for item in body.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "refusal":
                # Programmatically detectable, and deliberately not treated
                # as a parse failure: the model understood and declined.
                raise LLMError(
                    "the language model declined to answer this request",
                    kind="refused",
                )
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])

    if not texts:
        raise LLMError(
            "cloud provider did not return a structured answer",
            kind="json_parse_error",
        )
    if len(texts) > 1:
        # Strict Structured Outputs yields exactly one. More than one means
        # the response is not what the contract promised, and picking one
        # would be a guess about which.
        raise LLMError(
            "cloud provider returned more than one structured answer",
            kind="json_parse_error",
        )

    try:
        parsed = json.loads(texts[0])
    except ValueError:
        raise LLMError(
            "cloud provider returned output that was not valid JSON",
            kind="json_parse_error",
        ) from None
    if not isinstance(parsed, dict):
        raise LLMError(
            "cloud provider returned structured output that was not an object",
            kind="json_parse_error",
        )
    return parsed
