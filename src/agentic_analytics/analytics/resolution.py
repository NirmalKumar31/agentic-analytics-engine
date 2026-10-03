"""Why the rule resolver is uncertain, as something a router can act on.

The resolver already knows the difference between the two kinds of failure
that matter, and it has always known it. `_pick` says either

    "the table has no column that looks like a measure"

or

    "the question does not name which measure to use, and the table has
     3 to choose from"

Those are not degrees of the same problem. The first is a question this
dataset cannot answer, and no amount of model assistance invents a column
that is not there. The second is a question the dataset *can* answer where
the wording did not say which column to use -- exactly, and only, the case
where asking a model to read the sentence is worth a billable request.

Both collapsed into one `confident=False` boolean, so the only available
policies were "never ask a model" and "always ask a model". A deployment
that wanted the second spent a request on every unanswerable question, and
one that wanted the first refused questions it could have resolved.

This module gives those outcomes names. It adds no inference: every issue
here is raised at a site that already refused for that reason. What is new
is that the reason survives in a form the router can branch on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ResolutionIssue(StrEnum):
    """One specific reason the rule resolver could not settle a question.

    Stable identifiers: the router branches on them, the UI maps them to
    copy, and a visitor never sees an exception string.
    """

    #: No column of this table looks like the quantity being asked about.
    UNRESOLVED_MEASURE = "unresolved_measure"
    #: A grouping was asked for and no column matches it.
    UNRESOLVED_DIMENSION = "unresolved_dimension"
    #: The question names a column whose role is genuinely unclear from the
    #: data -- an integer that could be a code or a count.
    AMBIGUOUS_COLUMN_ROLE = "ambiguous_column_role"
    #: Several columns could be the measure and the question picked none.
    COMPETING_MEASURE_CANDIDATES = "competing_measure_candidates"
    #: Several columns could be the grouping and the question picked none.
    COMPETING_DIMENSION_CANDIDATES = "competing_dimension_candidates"
    #: The question restricts which rows to include and the restriction
    #: could not be bound to a column and a finite value.
    MISSING_FILTER_BINDING = "missing_filter_binding"
    #: A period was named and the table has no date column to apply it to.
    MISSING_PERIOD_FIELD = "missing_period_field"
    #: A period was named, but the table does not establish that its date
    #: column is the business clock for the requested measure.  A signup
    #: date beside annual revenue is the motivating case: filtering accounts
    #: by signup year is not the same claim as revenue earned in that year.
    AMBIGUOUS_PERIOD_SEMANTICS = "ambiguous_period_semantics"
    #: The question does not ask for an operation this engine implements.
    UNSUPPORTED_OPERATION = "unsupported_operation"
    #: The same column was read as both the value and the grouping.
    ROLE_COLLISION = "role_collision"
    #: The executed contract would not cover what the question fixed.
    INCOMPLETE_QUESTION_COVERAGE = "incomplete_question_coverage"
    #: The grouping would return more rows than the engine will return.
    RESULT_SHAPE_TOO_LARGE = "result_shape_too_large"
    #: Grouping by this column would disclose individual records.
    PRIVACY_RESTRICTED_GROUPING = "privacy_restricted_grouping"


#: Issues a typed planning request can plausibly settle.
#:
#: Every one of these is a question the dataset can answer where the
#: wording did not say which column to use. A model reading the sentence
#: may resolve it, and its proposal is then validated against the schema
#: and against what the question fixed, exactly as an explicit AI run is.
AI_ELIGIBLE_ISSUES: frozenset[ResolutionIssue] = frozenset(
    {
        ResolutionIssue.AMBIGUOUS_COLUMN_ROLE,
        ResolutionIssue.COMPETING_MEASURE_CANDIDATES,
        ResolutionIssue.COMPETING_DIMENSION_CANDIDATES,
        ResolutionIssue.MISSING_FILTER_BINDING,
    }
)


class ResolutionState(StrEnum):
    """What the rule resolver concluded, as a routing decision."""

    #: One complete contract, no choice made on the reader's behalf.
    EXACT = "exact"
    #: The dataset can answer this; the wording did not say how.
    AMBIGUOUS = "ambiguous"
    #: This dataset cannot answer this question.
    UNRESOLVED = "unresolved"
    #: The engine does not implement what was asked.
    UNSUPPORTED = "unsupported"
    #: Answering would disclose or compute something it must not.
    UNSAFE = "unsafe"


#: How each issue classifies when it is the only thing standing in the way.
_STATE_OF: dict[ResolutionIssue, ResolutionState] = {
    ResolutionIssue.UNRESOLVED_MEASURE: ResolutionState.UNRESOLVED,
    ResolutionIssue.UNRESOLVED_DIMENSION: ResolutionState.UNRESOLVED,
    ResolutionIssue.MISSING_PERIOD_FIELD: ResolutionState.UNRESOLVED,
    ResolutionIssue.AMBIGUOUS_PERIOD_SEMANTICS: ResolutionState.AMBIGUOUS,
    ResolutionIssue.AMBIGUOUS_COLUMN_ROLE: ResolutionState.AMBIGUOUS,
    ResolutionIssue.COMPETING_MEASURE_CANDIDATES: ResolutionState.AMBIGUOUS,
    ResolutionIssue.COMPETING_DIMENSION_CANDIDATES: ResolutionState.AMBIGUOUS,
    ResolutionIssue.MISSING_FILTER_BINDING: ResolutionState.AMBIGUOUS,
    ResolutionIssue.UNSUPPORTED_OPERATION: ResolutionState.UNSUPPORTED,
    ResolutionIssue.INCOMPLETE_QUESTION_COVERAGE: ResolutionState.UNSUPPORTED,
    ResolutionIssue.ROLE_COLLISION: ResolutionState.UNSAFE,
    ResolutionIssue.PRIVACY_RESTRICTED_GROUPING: ResolutionState.UNSAFE,
    ResolutionIssue.RESULT_SHAPE_TOO_LARGE: ResolutionState.UNSAFE,
}


def state_for(issues: tuple[ResolutionIssue, ...]) -> ResolutionState:
    """The state a set of issues amounts to.

    Ambiguity is the weakest claim here, so a question that is *also*
    unresolved, unsupported or unsafe takes the stronger reading. Spending
    a planning request on a question whose column does not exist buys
    nothing, and spending one on an unsafe grouping buys a refusal that was
    already certain.
    """
    if not issues:
        return ResolutionState.EXACT
    states = {_STATE_OF.get(issue, ResolutionState.UNRESOLVED) for issue in issues}
    for candidate in (
        ResolutionState.UNSAFE,
        ResolutionState.UNSUPPORTED,
        ResolutionState.UNRESOLVED,
    ):
        if candidate in states:
            return candidate
    return ResolutionState.AMBIGUOUS


@dataclass(frozen=True)
class ResolutionAssessment:
    """What the rules concluded about one question, and what to do next.

    Carries the contract when there is one, the reasons when there is not,
    and nothing a caller has to re-derive. `ai_eligible` is the routing
    answer; `issues` is why.
    """

    state: ResolutionState
    #: The rule resolver's contract. Present and usable only when `state`
    #: is `EXACT`; otherwise it is the refusing mapping, kept so the reason
    #: and the partial interpretation remain inspectable.
    deterministic_contract: Any | None = None
    #: What the question itself fixed, independent of any contract.
    requirements: Any | None = None
    issues: tuple[ResolutionIssue, ...] = ()
    #: Columns that could have filled a role the wording left open, per
    #: issue. Shown to the reader so a reformulation is obvious, and never
    #: used to pick one.
    candidates: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Human-readable reasons, in the resolver's own words.
    reasons: tuple[str, ...] = ()
    #: Reformulations that would be resolvable, derived from the candidates.
    safe_reformulations: tuple[str, ...] = ()

    @property
    def ai_eligible(self) -> bool:
        """Whether a typed planning request could settle this.

        True only for ambiguity: a question this dataset can answer whose
        wording did not say how. Never for a missing column, an operation
        the engine does not implement, or an unsafe grouping -- those are
        certain refusals, and a model cannot overturn them.
        """
        return self.state is ResolutionState.AMBIGUOUS and all(
            issue in AI_ELIGIBLE_ISSUES for issue in self.issues
        )

    @property
    def executable(self) -> bool:
        """Whether the rules alone produced something to run."""
        return self.state is ResolutionState.EXACT

    def as_dict(self) -> dict[str, Any]:
        """For events and the planning audit. No raw cells, no prompts."""
        return {
            "state": str(self.state),
            "issues": [str(issue) for issue in self.issues],
            "ai_eligible": self.ai_eligible,
            "candidates": {key: list(value) for key, value in self.candidates.items()},
            "reasons": list(self.reasons),
            "safe_reformulations": list(self.safe_reformulations),
        }


def _reformulations(
    issues: tuple[ResolutionIssue, ...], candidates: dict[str, tuple[str, ...]]
) -> tuple[str, ...]:
    """Rewordings that would resolve, built from the candidates themselves.

    Offered because a bare refusal leaves a reader guessing at the grammar.
    Only columns this table actually has are named, so a suggestion is
    never advice to ask about data that is not here.
    """
    out: list[str] = []
    for role, columns in candidates.items():
        for column in columns[:3]:
            out.append(f"name the {role} explicitly, for example {column!r}")
    if ResolutionIssue.MISSING_FILTER_BINDING in issues:
        out.append(
            "state the restriction as a column and a bound, for example 'age between 30 and 40'"
        )
    return tuple(dict.fromkeys(out))


def assess(question: str, schema: dict[str, Any]) -> ResolutionAssessment:
    """What the rules make of this question, as a routing decision.

    Runs the deterministic resolver and nothing else. It makes no provider
    call, consults no ledger and has no side effects, which is what lets
    the router use it to decide whether a provider is needed at all.
    """
    from agentic_analytics.analytics.upload_plan import (
        question_requirements,
        resolve_question,
    )

    requirements = question_requirements(question, schema)
    mapping = resolve_question(question, schema)

    if getattr(mapping, "confident", False):
        return ResolutionAssessment(
            state=ResolutionState.EXACT,
            deterministic_contract=mapping,
            requirements=requirements,
        )

    issues = tuple(getattr(mapping, "issues", ()) or ())
    reason = str(getattr(mapping, "explanation", "") or "")

    # Which columns could have filled a role the wording left open. Shown
    # so a reformulation is obvious; never used to pick one, because
    # picking from several is the guess the resolver refused to make.
    candidates: dict[str, tuple[str, ...]] = {}
    if ResolutionIssue.COMPETING_MEASURE_CANDIDATES in issues:
        candidates["measure"] = tuple(str(c) for c in schema.get("measures", []))
    if ResolutionIssue.COMPETING_DIMENSION_CANDIDATES in issues:
        candidates["grouping"] = tuple(str(c) for c in schema.get("dimensions", []))

    return ResolutionAssessment(
        state=state_for(issues),
        deterministic_contract=mapping,
        requirements=requirements,
        issues=issues,
        candidates=candidates,
        reasons=(reason,) if reason else (),
        safe_reformulations=_reformulations(issues, candidates),
    )
