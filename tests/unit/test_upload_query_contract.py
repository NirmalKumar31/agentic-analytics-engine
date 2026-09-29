"""The cloud planner interprets language; the engine owns execution."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from agentic_analytics.agents.analyst import resolve_upload_query
from agentic_analytics.analytics.upload_plan import (
    mapping_from_contract,
    mapping_from_plan,
    resolve_question,
)
from agentic_analytics.llm.base import LLMRequest
from agentic_analytics.llm.fake import FakeProvider

SCHEMA: dict[str, Any] = {
    "table": "uploaded_data",
    "fields": [
        {"name": "age", "data_type": "BIGINT"},
        {"name": "annual_revenue", "data_type": "DOUBLE"},
        {"name": "region", "data_type": "VARCHAR"},
    ],
    "measures": ["annual_revenue"],
    "dimensions": ["region"],
    "time_fields": [],
    "aggregatable_if_named": ["age"],
}
QUESTION = "What is the average annual revenue of people aged 30 to 40 by region?"


class RemoteFake(FakeProvider):
    remote_inference = True


async def test_remote_and_rule_plans_have_the_same_canonical_contract() -> None:
    rules = resolve_question(QUESTION, SCHEMA)
    remote = await resolve_upload_query(RemoteFake(), QUESTION, SCHEMA)

    assert remote.confident
    assert remote.interpretation == "ai-grounded"
    assert remote.canonical_dict() == rules.canonical_dict()
    assert remote.contract_hash == rules.contract_hash


class CapturingRemote(RemoteFake):
    def __init__(self) -> None:
        super().__init__()
        self.request: LLMRequest | None = None

    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        self.request = request
        return await super().complete_json(request)


async def test_remote_planner_sees_schema_shape_but_no_raw_cell_ranges() -> None:
    schema = deepcopy(SCHEMA)
    schema["fields"][0] |= {"min_value": "18", "max_value": "91"}
    provider = CapturingRemote()

    await resolve_upload_query(provider, QUESTION, schema)

    assert provider.request is not None
    assert "18" not in provider.request.user
    assert "91" not in provider.request.user
    assert "min_value" not in provider.request.context["schema"]
    assert "max_value" not in provider.request.context["schema"]


def _plan(**changes: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "table": "uploaded_data",
        "operation": "average",
        "operation_source": "average",
        "measure": "annual_revenue",
        "measure_source": "annual revenue",
        "dimension": "region",
        "dimension_source": "region",
        "filters": [
            {"column": "age", "operator": ">=", "value": "30", "source_text": "aged 30"},
            {"column": "age", "operator": "<=", "value": "40", "source_text": "40"},
        ],
        "time_field": None,
        "period_start": None,
        "period_end": None,
        "ascending": False,
        "confident": True,
        "ambiguity": "",
    }
    plan.update(changes)
    return plan


def test_ai_plan_cannot_omit_a_rule_detectable_filter() -> None:
    mapping = mapping_from_plan(QUESTION, SCHEMA, _plan(filters=[]))
    assert not mapping.confident
    assert "omitted a row restriction" in mapping.explanation


def test_ai_plan_cannot_invent_a_column_or_source_excerpt() -> None:
    invented = _plan(measure="lifetime_value")
    mapping = mapping_from_plan(QUESTION, SCHEMA, invented)
    assert not mapping.confident
    assert "not aggregatable" in mapping.explanation

    ungrounded = _plan(dimension_source="customer segment")
    mapping = mapping_from_plan(QUESTION, SCHEMA, ungrounded)
    assert not mapping.confident
    assert "ground its grouping" in mapping.explanation


def test_mcp_revalidation_rejects_a_changed_or_unhashed_contract() -> None:
    accepted = resolve_question(QUESTION, SCHEMA).as_dict()
    assert mapping_from_contract(QUESTION, SCHEMA, accepted).confident

    changed = deepcopy(accepted)
    changed["filters"][0]["operator"] = ">"
    assert not mapping_from_contract(QUESTION, SCHEMA, changed).confident

    unhashed = deepcopy(accepted)
    del unhashed["contract_hash"]
    assert not mapping_from_contract(QUESTION, SCHEMA, unhashed).confident


def test_filter_order_does_not_change_contract_identity() -> None:
    first = resolve_question(QUESTION, SCHEMA)
    contract = first.as_dict()
    contract["filters"] = list(reversed(contract["filters"]))
    assert mapping_from_contract(QUESTION, SCHEMA, contract).contract_hash == first.contract_hash
