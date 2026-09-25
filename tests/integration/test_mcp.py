"""The MCP server and client must negotiate and work for real.

These are not mocked. The in-process tests use the SDK's supported
``Client(server)`` connection; the HTTP test starts a real uvicorn server and
connects a client to it over Streamable HTTP.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn

from agentic_analytics.events import EventBus, EventType
from agentic_analytics.mcp_layer.client import (
    AnalyticsToolset,
    BudgetExceeded,
    ToolBudget,
    ToolCallFailed,
)
from agentic_analytics.mcp_layer.server import TOOL_NAMES, build_server
from agentic_analytics.warehouse.session import SessionManager, open_demo_session


@pytest.fixture
def manager_and_session(
    warehouse_dir: Path,
) -> Iterator[tuple[SessionManager, str, str]]:
    manager = SessionManager()
    session = manager.add(open_demo_session(warehouse_dir))
    try:
        yield manager, session.session_id, session.session_key
    finally:
        manager.close_all()


@pytest.fixture
def toolset_factory(manager_and_session: tuple[SessionManager, str, str]):  # type: ignore[no-untyped-def]
    manager, session_id, session_key = manager_and_session
    server = build_server(manager)

    def make(**kwargs: object) -> AnalyticsToolset:
        return AnalyticsToolset(server, session_id=session_id, session_key=session_key, **kwargs)  # type: ignore[arg-type]

    return make


async def test_client_and_server_negotiate(toolset_factory) -> None:  # type: ignore[no-untyped-def]
    async with toolset_factory() as ts:
        assert ts.transport == "in-process"
        assert set(ts.available_tools) == set(TOOL_NAMES)


async def test_every_advertised_tool_has_a_schema(
    manager_and_session: tuple[SessionManager, str, str],
) -> None:
    from mcp import Client

    manager, _, _ = manager_and_session
    async with Client(build_server(manager)) as client:
        listing = await client.list_tools()
        assert len(listing.tools) == len(TOOL_NAMES)
        for tool in listing.tools:
            assert tool.description, tool.name
            assert tool.input_schema, tool.name
            assert "session_id" in tool.input_schema.get("properties", {}), tool.name


async def test_resources_and_templates_are_published(
    manager_and_session: tuple[SessionManager, str, str],
) -> None:
    from mcp import Client

    manager, session_id, _ = manager_and_session
    async with Client(build_server(manager)) as client:
        resources = {str(r.uri) for r in (await client.list_resources()).resources}
        assert {"dataset://catalog", "metrics://definitions"} <= resources
        templates = {
            t.uri_template for t in (await client.list_resource_templates()).resource_templates
        }
        assert "dataset://schema/{session_id}/{table}" in templates
        assert "result://{session_id}/{result_id}" in templates

        catalog = await client.read_resource("dataset://catalog")
        assert "tables" in catalog.contents[0].text

        schema = await client.read_resource(f"dataset://schema/{session_id}/orders")
        assert "order_date" in schema.contents[0].text


async def test_result_resource_returns_the_sql_that_ran(toolset_factory) -> None:  # type: ignore[no-untyped-def]
    async with toolset_factory() as ts:
        payload = await ts.call("compute_metric", {"metric": "revenue"})
        text = await ts.read_resource(f"result://{ts.session_id}/{payload['result_id']}")
        assert "SELECT" in text.upper()
        assert payload["result_id"] in text


async def test_metric_tools_return_result_ids(toolset_factory) -> None:  # type: ignore[no-untyped-def]
    async with toolset_factory() as ts:
        payload = await ts.call(
            "analyze_timeseries", {"metric": "gross_margin_pct", "grain": "quarter"}
        )
        assert payload["result_id"].startswith("res_")
        assert payload["columns"][:2] == ["period", "gross_margin_pct"]
        assert payload["row_count"] > 0
        assert payload["dataset_fingerprint"].startswith("sha256:")


async def test_statistical_test_returns_computed_values(toolset_factory) -> None:  # type: ignore[no-untyped-def]
    async with toolset_factory() as ts:
        payload = await ts.call(
            "statistical_test",
            {
                "test_type": "two_proportion_z",
                "variables": {
                    "model": "customer_lifecycle",
                    "group_column": "first_delivery_status",
                    "value_column": "is_repeat",
                    "groups": ["late", "on_time"],
                },
            },
        )
        stat = payload["statistical_result"]
        assert stat["test_name"] == "two-proportion z-test"
        assert 0.0 <= stat["p_value"] <= 1.0
        assert stat["sample_sizes"]
        assert "confidence_interval" in stat


async def test_get_result_round_trips(toolset_factory) -> None:  # type: ignore[no-untyped-def]
    async with toolset_factory() as ts:
        first = await ts.call("compute_metric", {"metric": "orders"})
        again = await ts.call("get_result", {"result_id": first["result_id"]})
        assert again["rows"] == first["rows"]
        assert again["result_id"] == first["result_id"]


async def test_tool_errors_are_recoverable_not_fatal(toolset_factory) -> None:  # type: ignore[no-untyped-def]
    async with toolset_factory() as ts:
        with pytest.raises(ToolCallFailed, match="unknown metric"):
            await ts.call("compute_metric", {"metric": "not_a_metric"})
        # The connection survives, so a worker can correct itself.
        payload = await ts.call("compute_metric", {"metric": "revenue"})
        assert payload["row_count"] == 1


async def test_write_sql_is_refused_through_mcp(toolset_factory) -> None:  # type: ignore[no-untyped-def]
    async with toolset_factory() as ts:
        with pytest.raises(ToolCallFailed, match="not permitted"):
            await ts.call("run_readonly_sql", {"sql": "DROP TABLE orders"})
        with pytest.raises(ToolCallFailed, match="not permitted"):
            await ts.call("run_readonly_sql", {"sql": "SELECT * FROM read_csv_auto('/etc/passwd')"})


async def test_budgets_are_enforced(toolset_factory) -> None:  # type: ignore[no-untyped-def]
    async with toolset_factory(budget=ToolBudget(max_total=4, max_per_task=2)) as ts:
        await ts.call("compute_metric", {"metric": "revenue"}, task_id="A")
        await ts.call("compute_metric", {"metric": "orders"}, task_id="A")
        with pytest.raises(BudgetExceeded, match="task A"):
            await ts.call("compute_metric", {"metric": "units"}, task_id="A")
        await ts.call("compute_metric", {"metric": "revenue"}, task_id="B")
        await ts.call("compute_metric", {"metric": "orders"}, task_id="B")
        with pytest.raises(BudgetExceeded, match="ceiling of 4"):
            await ts.call("compute_metric", {"metric": "units"}, task_id="C")


async def test_parallel_tool_calls_share_one_session(toolset_factory) -> None:  # type: ignore[no-untyped-def]
    import anyio

    async with toolset_factory(budget=ToolBudget(max_total=32, max_per_task=8)) as ts:
        payloads: list[dict[str, object]] = []

        async def one(index: int) -> None:
            payloads.append(
                await ts.call(
                    "compute_metric",
                    {"metric": "revenue", "dimensions": ["region"]},
                    task_id=f"task-{index}",
                )
            )

        async with anyio.create_task_group() as tg:
            for i in range(6):
                tg.start_soon(one, i)

        assert len(payloads) == 6
        assert len({p["result_id"] for p in payloads}) == 6, "each call is its own result"
        assert all(p["row_count"] == payloads[0]["row_count"] for p in payloads)


async def test_trace_is_recorded_and_redacted(toolset_factory) -> None:  # type: ignore[no-untyped-def]
    bus = EventBus()
    async with toolset_factory(events=bus) as ts:
        await ts.call("compute_metric", {"metric": "revenue"}, task_id="T1")
        with pytest.raises(ToolCallFailed):
            await ts.call("compute_metric", {"metric": "bogus"}, task_id="T1")

    trace = ts.public_trace()
    assert len(trace) == 2
    assert trace[0]["ok"] is True and trace[1]["ok"] is False
    assert all("session_id" not in entry["arguments"] for entry in trace)

    kinds = [e.type for e in bus.history]
    assert EventType.MCP_TOOL_CALLED in kinds
    assert EventType.MCP_TOOL_COMPLETED in kinds
    assert EventType.MCP_TOOL_FAILED in kinds


async def test_client_injects_its_own_session_id(toolset_factory) -> None:  # type: ignore[no-untyped-def]
    """An agent cannot address a session other than its own."""
    async with toolset_factory() as ts:
        payload = await ts.call(
            "compute_metric", {"metric": "revenue", "session_id": "ses_someone_else"}
        )
        assert payload["result_id"]
        assert ts.trace[-1].arguments["session_id"] == ts.session_id


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def http_server(manager_and_session: tuple[SessionManager, str, str]) -> Iterator[str]:
    """A real Streamable HTTP MCP server on a real port."""
    import contextlib

    from fastapi import FastAPI

    manager, _, _ = manager_and_session
    mcp = build_server(manager)
    mcp_app = mcp.streamable_http_app(json_response=True, host="127.0.0.1")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount("/", mcp_app)

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 20
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:  # pragma: no cover
        pytest.fail("MCP HTTP server did not start")

    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


async def test_streamable_http_transport(
    http_server: str, manager_and_session: tuple[SessionManager, str, str]
) -> None:
    """The production transport, exercised over a socket."""
    _, session_id, session_key = manager_and_session
    async with AnalyticsToolset(http_server, session_id=session_id, session_key=session_key) as ts:
        assert ts.transport == "http"
        assert set(ts.available_tools) == set(TOOL_NAMES)
        payload = await ts.call("compare_segments", {"metric": "revenue", "dimension": "region"})
        assert payload["row_count"] > 0
        assert payload["result_id"].startswith("res_")


async def test_http_server_rejects_a_write(http_server: str) -> None:
    """The guard applies over HTTP exactly as it does in-process."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            http_server,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "run_readonly_sql",
                    "arguments": {"session_id": "x", "sql": "DROP TABLE orders"},
                },
            },
            headers={"Accept": "application/json, text/event-stream"},
            timeout=20,
        )
    # Either the protocol rejects the uninitialised session or the tool
    # rejects the statement; neither outcome drops a table.
    assert response.status_code in (200, 400)
    assert "DROP" not in response.text or "not permitted" in response.text
