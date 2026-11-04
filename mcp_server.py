"""One in-process MCP server exposing four small analytical capabilities."""
from __future__ import annotations

from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from engine import AnalysisError, Dataset, correlation, execute, schema


def build_server(sessions: dict[str, tuple[str, Dataset]]) -> MCPServer:
    server = MCPServer(
        name="agentic-analytics",
        version="1.0.0",
        description="Bounded analytical tools over one session dataset.",
        instructions="Use aggregate_question for calculated values. Cite result_id; never invent numbers.",
    )

    def session(session_id: str, session_key: str) -> Dataset:
        item = sessions.get(session_id)
        if item is None or item[0] != session_key:
            raise ToolError("unknown analytical session")
        return item[1]

    @server.tool(description="Describe the active table and inferred analytical roles.")
    def profile_dataset(session_id: str, session_key: str) -> dict[str, Any]:
        return schema(session(session_id, session_key))

    @server.tool(description="Execute a validated typed aggregate plan and retain the executed SQL.")
    def aggregate_question(session_id: str, session_key: str, plan: dict[str, Any]) -> dict[str, Any]:
        try:
            return execute(session(session_id, session_key), plan)
        except AnalysisError as exc:
            raise ToolError(str(exc)) from None

    @server.tool(description="Compute Pearson correlation for two numeric columns. Not a causal claim.")
    def correlation_test(session_id: str, session_key: str, x: str, y: str) -> dict[str, Any]:
        try:
            return correlation(session(session_id, session_key), x, y)
        except AnalysisError as exc:
            raise ToolError(str(exc)) from None

    @server.tool(description="Retrieve the exact rows and SQL for a previously computed result.")
    def get_result(session_id: str, session_key: str, result_id: str) -> dict[str, Any]:
        item = session(session_id, session_key).results.get(result_id)
        if item is None:
            raise ToolError("unknown result")
        return item

    return server
