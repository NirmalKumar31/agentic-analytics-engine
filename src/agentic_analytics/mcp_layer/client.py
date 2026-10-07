"""The MCP client the agents use to reach analytics.

Agents never import the analytics package. They call tools through this
toolset, which owns a real ``mcp.Client`` -- connected either in-process to a
server object or over Streamable HTTP to a URL -- and which enforces the
per-task and per-run tool-call budgets.

Routing every capability through one client is what makes the MCP trace in the
UI real: the trace is assembled from the calls that actually crossed this
boundary, not from a narration of them.
"""

from __future__ import annotations

import re
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any

from mcp import Client
from mcp.server import MCPServer

from agentic_analytics.agents.execution import ToolContract, build_tool_contracts
from agentic_analytics.events import EventBus, EventType
from agentic_analytics.logging import get_logger

log = get_logger(__name__)

#: `Error executing tool compute_metric: ...`, as the SDK phrases it.
_SDK_ERROR_PREFIX = re.compile(r"^Error executing tool [A-Za-z0-9_]+:\s*")

# Arguments that are never echoed into the trace shown to a user.
# Never echoed into the trace shown to a user. The capability is a bearer
# secret; the handle is not secret but is noise in a trace.
_REDACT_KEYS = frozenset({"session_id", "session_key", "remote_inference"})

#: Pydantic reports a validation failure with the offending input echoed
#: back: `input_value={'question': '...', 'session_key': '...'}`. Every tool
#: call carries the capability, so that clause is a copy of the secret in a
#: string that is stored in evaluation artifacts, shown in the run trace and
#: -- now that failures are fed back -- put into the next prompt. Observed
#: for real: a warehouse run produced 36 of these, each ending in a fragment
#: of the session key. The useful half of the message is the part before it.
_INPUT_VALUE = re.compile(r"input_value=.*?(?=,\s*input_type=|\]|$)", re.DOTALL)
#: Tool errors are written for a model to act on, not to carry a stack.
MAX_TOOL_ERROR_CHARS = 400


class BudgetExceeded(RuntimeError):
    """A tool-call ceiling was reached."""


def _strip_sdk_prefix(message: str | None) -> str:
    """Drop the SDK's `Error executing tool <name>:` wrapper.

    The message ends up in a report's limitations, where the wrapper is
    noise: a reader needs to know the question could not be mapped, not
    which layer formatted the sentence.
    """
    if not message:
        return ""
    match = _SDK_ERROR_PREFIX.match(message)
    return message[match.end() :].strip() if match else message


class ToolCallFailed(RuntimeError):
    """The server returned an error result. The message came from the server."""


@dataclass
class ToolCall:
    """One call across the MCP boundary, as shown in the trace."""

    tool_name: str
    arguments: dict[str, Any]
    task_id: str | None = None
    agent: str = "analysis_worker"
    duration_ms: float = 0.0
    result_id: str | None = None
    row_count: int | None = None
    ok: bool = True
    error: str | None = None

    def public(self) -> dict[str, Any]:
        """Redacted view, safe to show in the UI."""
        return {
            "tool_name": self.tool_name,
            "agent": self.agent,
            "task_id": self.task_id,
            "arguments": {k: v for k, v in self.arguments.items() if k not in _REDACT_KEYS},
            "duration_ms": round(self.duration_ms, 2),
            "result_id": self.result_id,
            "row_count": self.row_count,
            "ok": self.ok,
            "error": self.error,
        }


@dataclass
class ToolBudget:
    """Ceilings on tool use for one run."""

    max_total: int = 48
    max_per_task: int = 6
    total_used: int = 0
    per_task: dict[str, int] = field(default_factory=dict)

    def check(self, task_id: str | None) -> None:
        if self.total_used >= self.max_total:
            raise BudgetExceeded(f"run reached its ceiling of {self.max_total} MCP tool calls")
        if task_id is not None and self.per_task.get(task_id, 0) >= self.max_per_task:
            raise BudgetExceeded(
                f"task {task_id} reached its ceiling of {self.max_per_task} tool calls"
            )

    def record(self, task_id: str | None) -> None:
        self.total_used += 1
        if task_id is not None:
            self.per_task[task_id] = self.per_task.get(task_id, 0) + 1

    def remaining_for(self, task_id: str) -> int:
        return min(
            self.max_total - self.total_used,
            self.max_per_task - self.per_task.get(task_id, 0),
        )


class AnalyticsToolset:
    """A connected MCP client plus the budget and trace for one run."""

    def __init__(
        self,
        target: MCPServer | str,
        *,
        session_id: str,
        session_key: str,
        budget: ToolBudget | None = None,
        events: EventBus | None = None,
        read_timeout_seconds: float = 60.0,
        remote_inference: bool = False,
    ) -> None:
        self._target = target
        self.session_id = session_id
        # Application-controlled. A model never sees this and never chooses
        # it; it is injected on every call and redacted from the trace.
        self._session_key = session_key
        #: Whether this run's prompts leave the machine. Engine-owned and
        #: injected on every call, exactly like the capability: a model that
        #: could set it could talk its way into another disclosure policy.
        self._remote_inference = remote_inference
        self.budget = budget or ToolBudget()
        self.events = events
        self._read_timeout = read_timeout_seconds
        self._client: Client | None = None
        self._stack: AsyncExitStack | None = None
        self.trace: list[ToolCall] = []
        #: The results this run produced, in order. A session is shared --
        #: Compare Both puts a deterministic run and an AI run on one
        #: connection, so "every result on the session" is the wrong set
        #: for any one run to read: it would let the cloud run cite, and be
        #: shown, rows the local run computed under a laxer policy.
        self._result_order: list[str] = []
        self._result_ids: set[str] = set()
        self.available_tools: list[str] = []
        #: Public argument contracts, derived from the server's own listing.
        #: Held so a worker can be told what a tool takes without anyone
        #: transcribing the signature into a prompt, where the copy would
        #: drift from the server the moment a tool changed.
        self.tool_contracts: list[ToolContract] = []

    @property
    def transport(self) -> str:
        return "http" if isinstance(self._target, str) else "in-process"

    @property
    def result_ids(self) -> list[str]:
        """Result ids this run produced, oldest first."""
        return list(self._result_order)

    async def __aenter__(self) -> AnalyticsToolset:
        self._stack = AsyncExitStack()
        client = Client(self._target, read_timeout_seconds=self._read_timeout)
        self._client = await self._stack.enter_async_context(client)
        listing = await self._client.list_tools()
        self.available_tools = sorted(t.name for t in listing.tools)
        self.tool_contracts = build_tool_contracts(listing)
        log.info(
            "mcp_connected",
            transport=self.transport,
            tools=len(self.available_tools),
            protocol=self._client.protocol_version,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._client = None

    async def call(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        task_id: str | None = None,
        agent: str = "analysis_worker",
    ) -> dict[str, Any]:
        """Call a tool. Returns the structured payload, or raises.

        The session id is injected here rather than trusted from the caller,
        so an agent cannot address another session's data.
        """
        if self._client is None:
            raise RuntimeError("AnalyticsToolset used outside its async context")
        if tool_name not in self.available_tools:
            raise ToolCallFailed(f"unknown tool {tool_name!r}; available: {self.available_tools}")
        self.budget.check(task_id)

        args = {k: v for k, v in (arguments or {}).items() if v is not None}
        if tool_name == "get_result":
            # A stored snapshot keeps every value it was computed with, and
            # the id space is shared across the runs on this session. A run
            # may therefore re-read only what it computed itself; anything
            # else is a sibling run's result, fetched under whatever policy
            # that run had.
            wanted = args.get("result_id")
            if not isinstance(wanted, str) or wanted not in self._result_ids:
                raise ToolCallFailed(
                    f"unknown result_id {wanted!r}; cite a result this analysis produced"
                )
        # Injected here rather than trusted from the caller, so an agent can
        # neither address another session nor supply its own capability.
        args["session_id"] = self.session_id
        args["session_key"] = self._session_key
        args["remote_inference"] = self._remote_inference
        if task_id is not None and tool_name in _TASK_AWARE_TOOLS:
            args.setdefault("task_id", task_id)

        record = ToolCall(tool_name=tool_name, arguments=dict(args), task_id=task_id, agent=agent)
        if self.events:
            self.events.emit(EventType.MCP_TOOL_CALLED, **record.public(), transport=self.transport)

        started = time.perf_counter()
        self.budget.record(task_id)
        try:
            result = await self._client.call_tool(tool_name, args)
        except Exception as exc:
            record.ok = False
            record.error = _sanitize_tool_error(
                f"{type(exc).__name__}: {exc}",
                session_id=self.session_id,
                session_key=self._session_key,
            )
            record.duration_ms = (time.perf_counter() - started) * 1000
            self.trace.append(record)
            if self.events:
                self.events.emit(EventType.MCP_TOOL_FAILED, **record.public())
            raise ToolCallFailed(record.error) from None

        record.duration_ms = (time.perf_counter() - started) * 1000

        if result.is_error:
            message = _sanitize_tool_error(
                _strip_sdk_prefix(_first_text(result)) or "tool returned an error",
                session_id=self.session_id,
                session_key=self._session_key,
            )
            record.ok = False
            record.error = message
            self.trace.append(record)
            if self.events:
                self.events.emit(EventType.MCP_TOOL_FAILED, **record.public())
            raise ToolCallFailed(message)

        payload = result.structured_content or {}
        record.result_id = payload.get("result_id")
        if isinstance(record.result_id, str) and record.result_id not in self._result_ids:
            self._result_ids.add(record.result_id)
            self._result_order.append(record.result_id)
        row_count = payload.get("row_count")
        record.row_count = int(row_count) if isinstance(row_count, int) else None
        self.trace.append(record)
        if self.events:
            self.events.emit(EventType.MCP_TOOL_COMPLETED, **record.public())
        return dict(payload)

    async def read_resource(self, uri: str) -> str:
        """Read an MCP resource and return its text."""
        if self._client is None:
            raise RuntimeError("AnalyticsToolset used outside its async context")
        result = await self._client.read_resource(uri)
        for content in result.contents:
            text = getattr(content, "text", None)
            if isinstance(text, str):
                return text
        return ""

    def public_trace(self) -> list[dict[str, Any]]:
        return [c.public() for c in self.trace]


# Tools that accept a `task_id` so the result snapshot records which analysis
# task produced it.
_TASK_AWARE_TOOLS = frozenset(
    {
        "compute_metric",
        "compare_segments",
        "analyze_timeseries",
        "correlation_matrix",
        "statistical_test",
    }
)


def _sanitize_tool_error(message: str, *, session_id: str, session_key: str) -> str:
    """A tool failure a model may read and an artifact may keep.

    Removing the echoed input is what actually closes the leak: the key
    appears inside it, and often only as a fragment, so replacing the exact
    value would miss it. The literal values are replaced as well, for the
    case where some other layer spells one out in full.
    """
    text = _INPUT_VALUE.sub("input_value=[omitted]", message)
    for secret in (session_key, session_id):
        if secret:
            text = text.replace(secret, "[redacted]")
    text = " ".join(text.split())
    return text[:MAX_TOOL_ERROR_CHARS]


def _first_text(result: Any) -> str | None:
    for content in getattr(result, "content", []) or []:
        text = getattr(content, "text", None)
        if isinstance(text, str) and text.strip():
            return text
    return None
