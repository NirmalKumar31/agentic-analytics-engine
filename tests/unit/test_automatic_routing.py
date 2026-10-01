"""The routing truth table: what gets asked, and what it costs.

Two properties carry the whole design and both are about spending.

An **exact** question must cost nothing. If automatic routing asks a model
to confirm a contract the rules already resolved, it is the AI mode with
extra steps and a worse story about cost. So the tests here count provider
constructions, not provider calls: constructing the governed cloud provider
*is* the ledger admission, so a construction that happens at all has
already taken a durable quota slot.

A **certain refusal** must also cost nothing. A question naming a column
the file does not have cannot be rescued by a model reading the sentence
more carefully, and spending a request to be told what the rules already
knew is the failure mode that makes automatic routing look expensive.

Between those sits the one case worth paying for: the dataset can answer
the question and the wording did not say how.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import pytest
from tests.corpus.runner import RemoteFakeProvider
from tests.fixtures.sleep_study import write_csv

from agentic_analytics.agents.analyst import (
    ROUTE_AI_RESOLVED,
    ROUTE_AI_UNAVAILABLE,
    ROUTE_REFUSED,
    ROUTE_RULES_EXACT,
    resolve_upload_query_automatically,
)
from agentic_analytics.analytics.resolution import (
    AI_ELIGIBLE_ISSUES,
    ResolutionIssue,
    ResolutionState,
    assess,
    state_for,
)
from agentic_analytics.analytics.semantic import infer_schema
from agentic_analytics.llm.fake import FakeProvider
from agentic_analytics.warehouse.session import SessionManager, open_upload_session


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    """A real profile of a real upload. Never hand-written."""
    directory = Path(tempfile.mkdtemp())
    dataset = write_csv(directory)
    manager = SessionManager()
    try:
        session = manager.add(open_upload_session(dataset, dataset.name, "csv"))
        return infer_schema(session, "uploaded_data").as_dict()
    finally:
        manager.close_all()


class _CountingPlanner:
    """A planner factory that records how often it was built.

    Construction is the thing worth counting: that is where the ledger
    slot is taken, so a run that builds one has already spent quota
    whether or not it goes on to issue a request.
    """

    def __init__(self, *, remote: bool = True) -> None:
        self.builds = 0
        self._remote = remote

    async def __call__(self) -> Any:
        self.builds += 1
        return RemoteFakeProvider() if self._remote else FakeProvider()


# ───────────────────────────────── the state machine


class TestStateClassification:
    def test_no_issues_is_exact(self) -> None:
        assert state_for(()) is ResolutionState.EXACT

    @pytest.mark.parametrize("issue", sorted(AI_ELIGIBLE_ISSUES))
    def test_every_ai_eligible_issue_is_ambiguous(self, issue: ResolutionIssue) -> None:
        assert state_for((issue,)) is ResolutionState.AMBIGUOUS

    @pytest.mark.parametrize(
        ("issue", "expected"),
        [
            (ResolutionIssue.UNRESOLVED_MEASURE, ResolutionState.UNRESOLVED),
            (ResolutionIssue.UNRESOLVED_DIMENSION, ResolutionState.UNRESOLVED),
            (ResolutionIssue.MISSING_PERIOD_FIELD, ResolutionState.UNRESOLVED),
            (ResolutionIssue.UNSUPPORTED_OPERATION, ResolutionState.UNSUPPORTED),
            (ResolutionIssue.ROLE_COLLISION, ResolutionState.UNSAFE),
            (ResolutionIssue.PRIVACY_RESTRICTED_GROUPING, ResolutionState.UNSAFE),
            (ResolutionIssue.RESULT_SHAPE_TOO_LARGE, ResolutionState.UNSAFE),
        ],
    )
    def test_issues_a_model_cannot_settle(
        self, issue: ResolutionIssue, expected: ResolutionState
    ) -> None:
        assert state_for((issue,)) is expected

    def test_a_stronger_issue_overrides_ambiguity(self) -> None:
        """Ambiguity is the weakest claim, so it never wins.

        A question that is both underspecified *and* names a column the
        file does not have is not worth a planning request: the missing
        column refuses it whatever the wording turns out to mean.
        """
        mixed = (
            ResolutionIssue.COMPETING_MEASURE_CANDIDATES,
            ResolutionIssue.UNRESOLVED_DIMENSION,
        )
        assert state_for(mixed) is ResolutionState.UNRESOLVED

    def test_unsafe_outranks_everything(self) -> None:
        mixed = (
            ResolutionIssue.COMPETING_MEASURE_CANDIDATES,
            ResolutionIssue.UNSUPPORTED_OPERATION,
            ResolutionIssue.ROLE_COLLISION,
        )
        assert state_for(mixed) is ResolutionState.UNSAFE

    def test_ai_eligible_requires_every_issue_to_be_eligible(self) -> None:
        assessment = assess("x", {"table": "t", "fields": []})
        # Whatever this classifies as, the property must hold for it.
        assert assessment.ai_eligible == (
            assessment.state is ResolutionState.AMBIGUOUS
            and all(issue in AI_ELIGIBLE_ISSUES for issue in assessment.issues)
        )


# ───────────────────────────────── classification on a real profile


class TestAssessmentOnARealUpload:
    def test_a_fully_named_question_is_exact_with_no_issues(self, schema: dict[str, Any]) -> None:
        assessment = assess("What is the average total_sleep_hours by chronotype?", schema)
        assert assessment.state is ResolutionState.EXACT
        assert assessment.issues == ()
        assert assessment.executable
        assert not assessment.ai_eligible

    def test_a_column_the_file_lacks_is_unresolved(self, schema: dict[str, Any]) -> None:
        assessment = assess("What is the total gross_margin by chronotype?", schema)
        assert assessment.state is ResolutionState.UNRESOLVED
        assert ResolutionIssue.UNRESOLVED_MEASURE in assessment.issues
        assert not assessment.ai_eligible, "no model invents a column that is not there"

    def test_an_unnamed_measure_with_several_candidates_is_ambiguous(
        self, schema: dict[str, Any]
    ) -> None:
        assessment = assess("What is the average by chronotype?", schema)
        assert assessment.state is ResolutionState.AMBIGUOUS
        assert ResolutionIssue.COMPETING_MEASURE_CANDIDATES in assessment.issues
        assert assessment.ai_eligible
        # The reader is shown what could have been meant, and told how to say it.
        assert assessment.candidates.get("measure")
        assert assessment.safe_reformulations

    def test_no_recognised_operation_is_unsupported(self, schema: dict[str, Any]) -> None:
        assessment = assess("Tell me something interesting", schema)
        assert assessment.state is ResolutionState.UNSUPPORTED
        assert not assessment.ai_eligible

    def test_a_period_with_no_date_column_is_unresolved(self, schema: dict[str, Any]) -> None:
        assessment = assess("What is the average total_sleep_hours in 2024?", schema)
        assert assessment.state is ResolutionState.UNRESOLVED
        assert ResolutionIssue.MISSING_PERIOD_FIELD in assessment.issues
        assert not assessment.ai_eligible

    def test_an_unbindable_restriction_is_ambiguous_not_silently_dropped(
        self, schema: dict[str, Any]
    ) -> None:
        """The restriction must survive as a reason to refuse.

        This question used to resolve as `exact` and be answered over every
        row: the equality clause named no column of the table, and the
        filter parser skipped it instead of recording that one had been
        stated. The engine answered for everyone and presented it as the
        answer to a question about a subset.
        """
        assessment = assess("What is the average total_sleep_hours where mood is good?", schema)
        assert assessment.state is ResolutionState.AMBIGUOUS
        assert ResolutionIssue.MISSING_FILTER_BINDING in assessment.issues
        assert not assessment.executable
        assert assessment.ai_eligible

    def test_a_bindable_restriction_still_resolves_exactly(self, schema: dict[str, Any]) -> None:
        """The fix above must not refuse questions that already worked."""
        for question in (
            "What is the average total_sleep_hours where chronotype is Night Owl?",
            "What is the average deep_sleep_pct where age is above 40?",
        ):
            assessment = assess(question, schema)
            assert assessment.state is ResolutionState.EXACT, question

    def test_a_table_with_no_measure_is_unresolved_not_ambiguous(self) -> None:
        """Nothing to choose from is not the same as too much to choose from.

        `_pick` returns a different issue for each, and the distinction is
        the whole routing decision: a table with no numeric column cannot
        answer "what is the total", and no planning request changes that.
        Reached through a real upload because the classification depends on
        what `infer_schema` makes of the columns.
        """
        directory = Path(tempfile.mkdtemp())
        dataset = directory / "text_only.csv"
        dataset.write_text(
            "region,segment,note\nNorth,Retail,alpha\nSouth,Services,beta\nEast,Retail,gamma\n",
            encoding="utf-8",
        )
        manager = SessionManager()
        try:
            session = manager.add(open_upload_session(dataset, dataset.name, "csv"))
            text_only = infer_schema(session, "uploaded_data").as_dict()
        finally:
            manager.close_all()
        assert not text_only.get("measures"), "fixture must have no numeric column"

        assessment = assess("What is the total by region?", text_only)
        assert assessment.state is ResolutionState.UNRESOLVED
        assert ResolutionIssue.UNRESOLVED_MEASURE in assessment.issues
        assert not assessment.ai_eligible, (
            "a table with no numeric column cannot be rescued by a planner"
        )

        # And the router must not build a provider for it.
        planner = _CountingPlanner()
        result = _route("What is the total by region?", text_only, planner)
        assert result.route == ROUTE_REFUSED
        assert planner.builds == 0

    def test_assess_makes_no_provider_call_and_needs_no_ledger(self) -> None:
        """Which is what lets the router use it to decide on a provider.

        Read from the syntax tree rather than the source text: the
        docstring explains that it touches no provider, and a substring
        scan matched its own explanation.
        """
        import ast
        import inspect
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(assess)))
        function = tree.body[0]
        assert isinstance(function, ast.FunctionDef), "assess must stay synchronous"

        awaits = [n for n in ast.walk(function) if isinstance(n, ast.Await)]
        assert not awaits, "assess awaits something, so it can do I/O"

        names = {n.id for n in ast.walk(function) if isinstance(n, ast.Name)}
        attributes = {n.attr for n in ast.walk(function) if isinstance(n, ast.Attribute)}
        for forbidden in ("provider", "ledger", "llm", "client"):
            assert forbidden not in names | attributes, f"assess references {forbidden!r} in code"


# ───────────────────────────────── the routing truth table


def _route(question: str, schema: dict[str, Any], planner: _CountingPlanner | None) -> Any:
    return asyncio.run(resolve_upload_query_automatically(question, schema, open_planner=planner))


class TestRoutingTruthTable:
    def test_an_exact_question_executes_with_no_provider_built(
        self, schema: dict[str, Any]
    ) -> None:
        planner = _CountingPlanner()
        result = _route("What is the average total_sleep_hours by chronotype?", schema, planner)
        assert result.route == ROUTE_RULES_EXACT
        assert result.planning_calls == 0
        assert planner.builds == 0, "an exact question took a ledger slot it did not need"
        assert result.mapping.confident

    def test_an_exact_question_costs_nothing_even_with_ai_available(
        self, schema: dict[str, Any]
    ) -> None:
        """No confirmation calls. A second opinion on a settled contract is
        a billable request that cannot change the answer."""
        planner = _CountingPlanner()
        for question in (
            "What is the average total_sleep_hours by chronotype?",
            "What is the average deep_sleep_pct by age?",
            "Which chronotype has the highest average total_sleep_hours?",
        ):
            result = _route(question, schema, planner)
            assert result.route == ROUTE_RULES_EXACT, question
        assert planner.builds == 0

    def test_a_missing_column_refuses_without_building_a_provider(
        self, schema: dict[str, Any]
    ) -> None:
        planner = _CountingPlanner()
        result = _route("What is the total gross_margin by chronotype?", schema, planner)
        assert result.route == ROUTE_REFUSED
        assert planner.builds == 0, "spent a quota slot on a certain refusal"
        assert not result.mapping.confident

    def test_an_unsupported_question_refuses_without_building_a_provider(
        self, schema: dict[str, Any]
    ) -> None:
        planner = _CountingPlanner()
        result = _route("Tell me something interesting", schema, planner)
        assert result.route == ROUTE_REFUSED
        assert planner.builds == 0

    def test_ambiguity_builds_exactly_one_provider(self, schema: dict[str, Any]) -> None:
        planner = _CountingPlanner()
        result = _route("What is the average by chronotype?", schema, planner)
        assert planner.builds == 1, "ambiguity must cost one planning request, not more"
        assert result.route in {ROUTE_AI_RESOLVED, ROUTE_REFUSED}

    def test_a_resolved_plan_is_recorded_as_ai_resolved(self, schema: dict[str, Any]) -> None:
        planner = _CountingPlanner(remote=True)
        result = _route("What is the average by chronotype?", schema, planner)
        if result.route == ROUTE_AI_RESOLVED:
            assert result.mapping.confident
            assert result.planning_calls >= 1
        else:
            # The stand-in planner declined; the refusal must be honest
            # rather than an execution of some other question.
            assert not result.mapping.confident

    def test_ambiguity_with_no_planner_is_a_refusal_not_a_failure(
        self, schema: dict[str, Any]
    ) -> None:
        """AI being unavailable is a deployment fact, not a broken run."""
        result = _route("What is the average by chronotype?", schema, None)
        assert result.route == ROUTE_AI_UNAVAILABLE
        assert not result.mapping.confident
        # The reason names what was underspecified, so the visitor can fix it.
        assert result.assessment.safe_reformulations

    def test_an_exact_question_still_executes_when_ai_is_unavailable(
        self, schema: dict[str, Any]
    ) -> None:
        """The property that makes `auto` safe to make the default."""
        result = _route("What is the average total_sleep_hours by chronotype?", schema, None)
        assert result.route == ROUTE_RULES_EXACT
        assert result.mapping.confident

    def test_a_refused_route_never_returns_a_confident_contract(
        self, schema: dict[str, Any]
    ) -> None:
        """A refusal that carries an executable contract would execute."""
        planner = _CountingPlanner()
        for question in (
            "What is the total gross_margin by chronotype?",
            "Tell me something interesting",
            "What is the average total_sleep_hours in 2024?",
        ):
            result = _route(question, schema, planner)
            assert result.route == ROUTE_REFUSED, question
            assert not result.mapping.confident, question


# ───────────────────────────────── what the planner may not change


class TestPlannerAuthority:
    """A plan is a reading of the sentence, never an override of it.

    These guards already exist in `mapping_from_plan`; they are asserted
    here against the automatic route because that route is the one that
    will issue most planning requests once it is the default, and a guard
    that holds for explicit AI mode but not for `auto` would be worse than
    no guard at all.
    """

    def test_an_exact_contract_is_never_sent_for_a_second_opinion(
        self, schema: dict[str, Any]
    ) -> None:
        planner = _CountingPlanner()
        _route("What is the average deep_sleep_pct by age?", schema, planner)
        assert planner.builds == 0

    def test_the_route_is_recorded_so_a_reader_knows_who_decided(
        self, schema: dict[str, Any]
    ) -> None:
        planner = _CountingPlanner()
        exact = _route("What is the average deep_sleep_pct by age?", schema, planner)
        assert exact.route == ROUTE_RULES_EXACT
        assert exact.assessment.state is ResolutionState.EXACT

    def test_the_assessment_carries_no_prompt_and_no_raw_cells(
        self, schema: dict[str, Any]
    ) -> None:
        """What goes into an event must disclose nothing it should not."""
        assessment = assess("What is the average by chronotype?", schema)
        payload = assessment.as_dict()
        flat = str(payload).lower()
        for forbidden in ("prompt", "system", "api_key", "participant"):
            assert forbidden not in flat
        assert set(payload) == {
            "state",
            "issues",
            "ai_eligible",
            "candidates",
            "reasons",
            "safe_reformulations",
        }
