"""A routine question must not fail because a model chose another operation.

Production evidence: asked `total Weekly_Sales by Store` on a 45-store
sales table, the cloud planner returned `profile` with no measure and no
grouping. The deterministic pane answered; the AI pane rendered nothing.

Refusing is right when the rules cannot resolve the question either.
There the model's reason is the only information available. It is wrong
when the rules resolved it unambiguously, because the arithmetic was never
the model's to own, so refusing protects nothing and costs the answer.
"""

from __future__ import annotations

from typing import Any

from agentic_analytics.agents.analyst import resolve_upload_query
from agentic_analytics.analytics.upload_plan import resolve_question
from agentic_analytics.llm.base import LLMRequest
from agentic_analytics.llm.fake import FakeProvider

SCHEMA: dict[str, Any] = {
    "table": "uploaded_data",
    "fields": [
        {"name": "Store", "data_type": "BIGINT"},
        {"name": "Weekly_Sales", "data_type": "DOUBLE"},
        {"name": "Holiday_Flag", "data_type": "BIGINT"},
    ],
    "measures": ["Weekly_Sales"],
    "dimensions": ["Store", "Holiday_Flag"],
    "time_fields": [],
    "aggregatable_if_named": ["Store", "Holiday_Flag"],
}
EXPLICIT = "What is the total Weekly_Sales by Store?"


class ProfileProposingRemote(FakeProvider):
    """Returns the plan production actually returned."""

    remote_inference = True

    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        if request.role == "upload_query_planner":
            return {
                "table": "uploaded_data",
                "operation": "profile",
                "operation_source": "total",
                "measure": None,
                "measure_source": "",
                "dimension": None,
                "dimension_source": "",
                "filters": [],
                "time_field": None,
                "period_start": None,
                "period_end": None,
                "ascending": False,
                "confident": True,
                "ambiguity": "",
            }
        return await super().complete_json(request)


class UnusableRemote(ProfileProposingRemote):
    """Declines outright, with a reason."""

    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        if request.role == "upload_query_planner":
            plan = await ProfileProposingRemote.complete_json(self, request)
            return plan | {"confident": False, "ambiguity": "the wording is ambiguous to me"}
        return await FakeProvider.complete_json(self, request)


async def test_a_profile_plan_does_not_lose_an_unambiguous_answer() -> None:
    rules = resolve_question(EXPLICIT, SCHEMA)
    assert rules.confident and rules.operation == "sum"

    provider = ProfileProposingRemote()
    try:
        mapping = await resolve_upload_query(provider, EXPLICIT, SCHEMA)
    finally:
        await provider.aclose()

    assert mapping.confident
    assert mapping.operation == "sum"
    assert mapping.measure == "Weekly_Sales"
    assert mapping.dimension == "Store"
    # The same governed interpretation, so Compare Both may say so.
    assert mapping.contract_hash == rules.contract_hash


async def test_the_fallback_is_recorded_rather_than_passed_off_as_the_plan() -> None:
    """A substitution presented as the planner's own choice would make the
    mode's provenance label false."""
    provider = ProfileProposingRemote()
    try:
        mapping = await resolve_upload_query(provider, EXPLICIT, SCHEMA)
    finally:
        await provider.aclose()

    assert mapping.interpretation == "rule-based"
    assert "did not return a usable contract" in mapping.planner_note
    assert "planner_note" in mapping.as_dict()
    # Provenance, not semantics: it must not change the contract identity.
    assert "planner_note" not in mapping.canonical_dict()


async def test_an_ambiguous_question_still_refuses_with_the_models_reason() -> None:
    """Where the rules cannot resolve it either, the model's reason is the
    only information there is, and falling back would invent an answer."""
    ambiguous = "What about the numbers?"
    assert not resolve_question(ambiguous, SCHEMA).confident

    provider = UnusableRemote()
    try:
        mapping = await resolve_upload_query(provider, ambiguous, SCHEMA)
    finally:
        await provider.aclose()

    assert not mapping.confident
    assert "ambiguous to me" in mapping.explanation


async def test_a_usable_plan_is_still_the_planners_own() -> None:
    """Without this the fallback could be satisfied by ignoring the model."""
    provider = FakeProvider()
    provider.remote_inference = True  # type: ignore[misc]
    try:
        mapping = await resolve_upload_query(provider, EXPLICIT, SCHEMA)
    finally:
        await provider.aclose()

    assert mapping.confident
    assert mapping.interpretation == "ai-grounded"
    assert mapping.planner_note == ""
