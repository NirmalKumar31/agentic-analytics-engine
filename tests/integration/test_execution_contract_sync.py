"""The contract shown to a worker must be the server's, not a copy of it.

The worker used to be told tool *names* and nothing else, so its arguments
were guesses. Telling it the arguments fixes that only if the description
stays true: a signature transcribed into a prompt drifts the moment a tool
changes, and the worker is then confidently told about a contract that no
longer exists -- which is worse than being told nothing, because it will not
doubt it.

So these tests run against the real MCP server over the real client. Nothing
here is mocked.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from agentic_analytics.agents.execution import (
    ENGINE_OWNED_ARGUMENTS,
    SEMANTIC_TOOLS,
    build_execution_contract,
    render_contract,
    select_tools,
)
from agentic_analytics.agents.preflight import preflight
from agentic_analytics.agents.schemas import AnalysisTask
from agentic_analytics.mcp_layer.client import AnalyticsToolset
from agentic_analytics.mcp_layer.server import build_server
from agentic_analytics.warehouse.metrics import VALID_GRAINS
from agentic_analytics.warehouse.session import SessionManager, open_demo_session


@pytest.fixture
def demo(warehouse_dir: Path) -> Iterator[tuple[SessionManager, str, str]]:
    manager = SessionManager()
    session = manager.add(open_demo_session(warehouse_dir))
    try:
        yield manager, session.session_id, session.session_key
    finally:
        manager.close_all()


async def test_every_advertised_tool_gets_a_contract(
    demo: tuple[SessionManager, str, str],
) -> None:
    manager, session_id, session_key = demo
    async with AnalyticsToolset(
        build_server(manager), session_id=session_id, session_key=session_key
    ) as ts:
        assert [c.name for c in ts.tool_contracts] == sorted(ts.available_tools)


async def test_the_contract_matches_the_servers_own_schema(
    demo: tuple[SessionManager, str, str],
) -> None:
    """Argument for argument, against `list_tools()` itself."""
    manager, session_id, session_key = demo
    server = build_server(manager)
    async with AnalyticsToolset(server, session_id=session_id, session_key=session_key) as ts:
        listing = await ts._client.list_tools()  # type: ignore[union-attr]
        by_name = {t.name: t for t in listing.tools}

        for contract in ts.tool_contracts:
            schema = by_name[contract.name].input_schema or {}
            properties = set(schema.get("properties") or {})
            required = set(schema.get("required") or {})

            expected_public = properties - ENGINE_OWNED_ARGUMENTS
            assert contract.argument_names == expected_public, contract.name
            assert set(contract.required) == (required - ENGINE_OWNED_ARGUMENTS), contract.name


async def test_no_engine_owned_argument_survives_into_any_contract(
    demo: tuple[SessionManager, str, str],
) -> None:
    """Every tool takes session_id and session_key. None may expose them."""
    manager, session_id, session_key = demo
    async with AnalyticsToolset(
        build_server(manager), session_id=session_id, session_key=session_key
    ) as ts:
        assert ts.tool_contracts
        for contract in ts.tool_contracts:
            assert not (contract.argument_names & ENGINE_OWNED_ARGUMENTS), contract.name


async def test_the_rendered_contract_never_carries_the_capability(
    demo: tuple[SessionManager, str, str],
) -> None:
    manager, session_id, session_key = demo
    async with AnalyticsToolset(
        build_server(manager), session_id=session_id, session_key=session_key
    ) as ts:
        session = manager.get(session_id, session_key)
        contract = build_execution_contract(
            catalog=session.catalog(),
            registry=session.registry,
            tool_contracts=ts.tool_contracts,
            grains=sorted(VALID_GRAINS),
        )
        rendered = render_contract(contract, AnalysisTask(objective="o"))
        assert session_key not in rendered
        assert session_id not in rendered


async def test_a_real_metric_call_passes_preflight_against_the_real_catalog(
    demo: tuple[SessionManager, str, str],
) -> None:
    """The warehouse question that used to fail 36 times, checked for real.

    `revenue by acquisition_channel` is exactly what the model asked for,
    and what the engine refused because it was addressed as a column of
    `orders`. Named semantically, against the real registry, it is valid --
    and it then actually runs.
    """
    manager, session_id, session_key = demo
    async with AnalyticsToolset(
        build_server(manager), session_id=session_id, session_key=session_key
    ) as ts:
        session = manager.get(session_id, session_key)
        contract = build_execution_contract(
            catalog=session.catalog(),
            registry=session.registry,
            tool_contracts=ts.tool_contracts,
            grains=sorted(VALID_GRAINS),
        )

        assert contract.has_metrics
        revenue = contract.metric("revenue")
        assert revenue is not None
        assert "acquisition_channel" in revenue.valid_dimensions

        arguments = {"metric": "revenue", "dimension": "acquisition_channel"}
        assert preflight("compare_segments", arguments, contract) is None

        payload = await ts.call("compare_segments", arguments, task_id="task_1")
        assert payload["row_count"] > 0


async def test_the_failing_shape_is_refused_before_it_reaches_mcp(
    demo: tuple[SessionManager, str, str],
) -> None:
    """And the refusal names what the worker should have used."""
    manager, session_id, session_key = demo
    async with AnalyticsToolset(
        build_server(manager), session_id=session_id, session_key=session_key
    ) as ts:
        session = manager.get(session_id, session_key)
        contract = build_execution_contract(
            catalog=session.catalog(),
            registry=session.registry,
            tool_contracts=ts.tool_contracts,
            grains=sorted(VALID_GRAINS),
        )

        # A table the model invented. The old code raised this inside the
        # MCP client *before* it recorded a trace entry, so the run showed
        # zero tool calls while the worker burned its whole budget, which
        # is why two questions in the diagnostic run reported 0 calls and a
        # 600-second timeout.
        rejection = preflight("profile_table", {"table": "sales"}, contract)
        assert rejection is not None
        assert rejection.category == "unknown_table"
        assert "orders" in rejection.message and "customers" in rejection.message

        # An argument the tool does not take, named against the real schema.
        rejection = preflight(
            "profile_table", {"table": "orders", "columns": ["revenue"]}, contract
        )
        assert rejection is not None
        assert rejection.category == "unknown_argument"
        assert "table" in rejection.message

        # And a metric that does not exist is refused with the ones that do.
        rejection = preflight(
            "compare_segments", {"metric": "sales", "dimension": "region"}, contract
        )
        assert rejection is not None
        assert rejection.category == "unknown_metric"
        assert "revenue" in rejection.message


async def test_the_contract_shown_for_one_task_stays_small(
    demo: tuple[SessionManager, str, str],
) -> None:
    """Measured against the real warehouse, not a two-table fixture."""
    manager, session_id, session_key = demo
    async with AnalyticsToolset(
        build_server(manager), session_id=session_id, session_key=session_key
    ) as ts:
        session = manager.get(session_id, session_key)
        task = AnalysisTask(objective="o", preferred_tool="compare_segments")
        wanted = select_tools(ts.available_tools, task, has_metrics=True)
        contract = build_execution_contract(
            catalog=session.catalog(),
            registry=session.registry,
            tool_contracts=[c for c in ts.tool_contracts if c.name in wanted],
            grains=sorted(VALID_GRAINS),
        )
        rendered = render_contract(contract, task)
        assert len(rendered) < 8000, f"contract is {len(rendered)} characters"
        # It is not small because it is empty.
        assert "revenue" in rendered
        assert "compare_segments" in rendered


async def test_every_semantic_tool_named_in_the_prompt_actually_exists(
    demo: tuple[SessionManager, str, str],
) -> None:
    """A prompt naming a tool the server does not have teaches a wrong move."""
    manager, session_id, session_key = demo
    async with AnalyticsToolset(
        build_server(manager), session_id=session_id, session_key=session_key
    ) as ts:
        unknown = SEMANTIC_TOOLS - set(ts.available_tools)
        assert not unknown, f"SEMANTIC_TOOLS names tools the server does not expose: {unknown}"


async def test_a_real_failed_call_never_echoes_the_capability(
    demo: tuple[SessionManager, str, str],
) -> None:
    """End to end, against the real server, with the real key.

    The reproduction that found this made the exact call below thirty-six
    times: `aggregate_for_question` with the required `table` omitted. The
    server's validation error echoed the arguments, and the arguments always
    contain the capability.
    """
    manager, session_id, session_key = demo
    from agentic_analytics.mcp_layer.client import ToolCallFailed

    async with AnalyticsToolset(
        build_server(manager), session_id=session_id, session_key=session_key
    ) as ts:
        with pytest.raises(ToolCallFailed) as raised:
            await ts.call("aggregate_for_question", {"question": "revenue by channel"})

        message = str(raised.value)
        assert session_key not in message
        assert session_id not in message
        # Even a fragment: the key is random, so check a slice of it.
        assert session_key[8:24] not in message
        # And the actionable part is still there.
        assert "table" in message.lower()

        # The trace entry, which the UI renders, is sanitised too.
        entry = ts.public_trace()[-1]
        assert entry["ok"] is False
        assert session_key not in str(entry)
        assert session_key[8:24] not in str(entry)


async def test_the_preflight_now_catches_that_call_before_it_is_made(
    demo: tuple[SessionManager, str, str],
) -> None:
    """The same call, refused with the argument list the model needed."""
    manager, session_id, session_key = demo
    async with AnalyticsToolset(
        build_server(manager), session_id=session_id, session_key=session_key
    ) as ts:
        session = manager.get(session_id, session_key)
        contract = build_execution_contract(
            catalog=session.catalog(),
            registry=session.registry,
            tool_contracts=ts.tool_contracts,
            grains=sorted(VALID_GRAINS),
            available_tools=list(ts.available_tools),
        )
        rejection = preflight(
            "aggregate_for_question", {"question": "revenue by channel"}, contract
        )
        assert rejection is not None
        assert rejection.category == "missing_required_argument"
        assert "table" in rejection.message
        assert session_key not in rejection.message
