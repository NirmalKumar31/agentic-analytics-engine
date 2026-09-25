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

import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any

from mcp import Client
from mcp.server import MCPServer

from agentic_analytics.events import EventBus, EventType
from agentic_analytics.logging import get_logger

log = get_logger(__name__)

# Arguments that are never echoed into the trace shown to a user.
# Never echoed into the trace shown to a user. The capability is a bearer
# secret; the handle is not secret but is noise in a trace.
_REDACT_KEYS = frozenset({"session_id", "session_key"})


class BudgetExceeded(RuntimeError):
    """A tool-call ceiling was reached."""


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
    ) -> None:
        self._target = target
        self.session_id = session_id
        # Application-controlled. A model never sees this and never chooses
        # it; it is injected on every call and redacted from the trace.
        self._session_key = session_key
        self.budget = budget or ToolBudget()
        self.events = events
        self._read_timeout = read_timeout_seconds
        self._client: Client | None = None
        self._stack: AsyncExitStack | None = None
        self.trace: list[ToolCall] = []
        self.available_tools: list[str] = []

    @property
    def transport(self) -> str:
        return "http" if isinstance(self._target, str) else "in-process"

    async def __aenter__(self) -> AnalyticsToolset:
        self._stack = AsyncExitStack()
        client = Client(self._target, read_timeout_seconds=self._read_timeout)
        self._client = await self._stack.enter_async_context(client)
        listing = await self._client.list_tools()
        self.available_tools = sorted(t.name for t in listing.tools)
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
        # Injected here rather than trusted from the caller, so an agent can
        # neither address another session nor supply its own capability.
        args["session_id"] = self.session_id
        args["session_key"] = self._session_key
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
            record.error = f"{type(exc).__name__}: {exc}"
            record.duration_ms = (time.perf_counter() - started) * 1000
            self.trace.append(record)
            if self.events:
                self.events.emit(EventType.MCP_TOOL_FAILED, **record.public())
            raise ToolCallFailed(record.error) from None

        record.duration_ms = (time.perf_counter() - started) * 1000

        if result.is_error:
            message = _first_text(result) or "tool returned an error"
            record.ok = False
            record.error = message
            self.trace.append(record)
            if self.events:
                self.events.emit(EventType.MCP_TOOL_FAILED, **record.public())
            raise ToolCallFailed(message)

        payload = result.structured_content or {}
        record.result_id = payload.get("result_id")
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


def _first_text(result: Any) -> str | None:
    for content in getattr(result, "content", []) or []:
        text = getattr(content, "text", None)
        if isinstance(text, str) and text.strip():
            return text
    return None
