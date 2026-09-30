"""The upload acceptance corpus, run in CI without credentials.

The contract this enforces is not "every dataset gets a good answer" --
that is not achievable and the corpus does not pretend to test it. It is:

* accepted data stays inside the documented envelope;
* a published finding is numerically verified against its own cited cells
  and relevant to the question asked;
* an ambiguous schema, question, measure, dimension or time field produces
  a refusal or an empty report, never a guess;
* nothing fails unexpectedly.

So the release gate is about what must never happen. Three outcomes are
acceptable per case and the manifest says which; the counts that must be
zero are unsupported publications, irrelevant publications, silent
guesses, cross-run leaks and unexpected failures.
"""

from __future__ import annotations

import collections
from pathlib import Path

import pytest
from tests.corpus.kinds import Domain, Family, QuestionKind
from tests.corpus.manifest import cases
from tests.corpus.runner import Observation, report, run_case

CASES = cases()


# ──────────────────────────────────────────────────────────── coverage
def test_every_domain_is_represented() -> None:
    assert {c.domain for c in CASES} == set(Domain)


def test_every_structural_family_is_represented() -> None:
    assert {c.family for c in CASES} == set(Family)


def test_every_question_kind_is_exercised() -> None:
    assert {c.kind for c in CASES} == set(QuestionKind)


def test_each_family_appears_in_more_than_one_domain() -> None:
    """A shape seen in one subject area proves only that the engine copes
    with that vocabulary."""
    by_family: dict[Family, set[Domain]] = collections.defaultdict(set)
    for case in CASES:
        by_family[case.family].add(case.domain)
    thin = {
        f.value: sorted(d.value for d in domains)
        for f, domains in by_family.items()
        if len(domains) < 2
    }
    # `boundary_sized` is the one family defined by size rather than shape,
    # and one large fixture is enough to exercise the row ceiling.
    thin.pop(Family.BOUNDARY_SIZED.value, None)
    assert not thin, thin


def test_each_question_kind_appears_in_several_domains() -> None:
    by_kind: dict[QuestionKind, set[Domain]] = collections.defaultdict(set)
    for case in CASES:
        by_kind[case.kind].add(case.domain)
    assert all(len(domains) >= 3 for domains in by_kind.values()), {
        k.value: len(v) for k, v in by_kind.items() if len(v) < 3
    }


def test_both_file_formats_are_exercised() -> None:
    assert sum(1 for c in CASES if c.parquet) >= 20
    assert sum(1 for c in CASES if not c.parquet) >= 20


#: The kinds every dataset can support, whatever its columns.
#:
#: The row-restriction kinds are deliberately excluded: they need a
#: numeric or categorical column to restrict on, and a table with no
#: trustworthy measure cannot host one. Requiring them everywhere would
#: mean inventing columns to satisfy a checker.
CORE_KINDS = frozenset(
    {
        QuestionKind.ANSWERABLE,
        QuestionKind.AMBIGUOUS,
        QuestionKind.IRRELEVANT,
        QuestionKind.CAUSAL,
        QuestionKind.NEEDS_MEASURE_AND_DIMENSION,
        QuestionKind.PERIOD_SENSITIVE,
        QuestionKind.SYNONYM,
        QuestionKind.PHANTOM_COLUMN,
        QuestionKind.EMPTY_RESULT,
        QuestionKind.NULL_HEAVY,
    }
)


def test_several_domains_have_the_full_question_matrix() -> None:
    """Pairwise coverage catches shallow gaps; a full matrix catches the
    interactions between kinds on one schema."""
    by_dataset: dict[str, set[QuestionKind]] = collections.defaultdict(set)
    for case in CASES:
        by_dataset[case.dataset].add(case.kind)
    full = [name for name, kinds in by_dataset.items() if kinds >= CORE_KINDS]
    assert len(full) >= 6, full


def test_every_row_restriction_kind_is_exercised() -> None:
    """The filter kinds, which the released engine had no representation
    for at all. Each must reach several domains, like every other kind."""
    restriction_kinds = set(QuestionKind) - CORE_KINDS
    by_kind: dict[QuestionKind, set[object]] = collections.defaultdict(set)
    for case in CASES:
        by_kind[case.kind].add(case.domain)
    missing = sorted(k.value for k in restriction_kinds if len(by_kind[k]) < 3)
    assert not missing, missing


# ──────────────────────────────────────────── what the new shapes prove
#
# A domain that only passes adds nothing. These two assert the hazards the
# interval-grained family was added for, directly against the resolver,
# so "adds structural coverage" is a claim the suite checks rather than a
# claim the manifest asserts by listing a name.


def _uploaded_schema(tmp_path: Path, dataset: str) -> dict[str, object]:
    from tests.corpus.generators import build

    from agentic_analytics.analytics.semantic import infer_schema
    from agentic_analytics.warehouse.session import SessionManager, open_upload_session

    data = build(dataset)
    path = data.write_csv(tmp_path)
    manager = SessionManager()
    try:
        session = manager.add(open_upload_session(path, path.name, "csv"))
        return dict(infer_schema(session, "uploaded_data").as_dict())
    finally:
        manager.close_all()


@pytest.mark.parametrize(
    ("question", "answerable"),
    [
        # Names the one additive column: answerable.
        ("total interval_length_m by lithology_code", True),
        # "depth" is three columns -- two bounds and the span between
        # them. Picking one would be a silent guess about what was meant.
        ("total depth by lithology_code", False),
        # "grade" is two intensities.
        ("average grade by hole_id", False),
    ],
)
def test_an_interval_table_refuses_a_bound_it_would_have_to_choose(
    tmp_path: Path, question: str, answerable: bool
) -> None:
    from agentic_analytics.analytics.upload_plan import build_sql, resolve_question

    schema = _uploaded_schema(tmp_path, "geology_core_assays")
    mapping = resolve_question(question, schema)
    assert bool(build_sql(mapping)) is answerable, mapping.explanation


@pytest.mark.parametrize(
    ("question", "expected_dimension"),
    [
        ("total passengers by origin_airport", "origin_airport"),
        ("average delay_minutes by destination_airport", "destination_airport"),
        # One vocabulary, two columns: "airport" names neither.
        ("total passengers by airport", None),
    ],
)
def test_an_edge_table_will_not_pick_one_end_of_the_edge(
    tmp_path: Path, question: str, expected_dimension: str | None
) -> None:
    """The row is an edge -- a leg *from* somewhere *to* somewhere -- so a
    question naming one end must be answered with that end, and a question
    naming neither must not be answered with whichever came first in the
    schema."""
    from agentic_analytics.analytics.upload_plan import resolve_question

    schema = _uploaded_schema(tmp_path, "aviation_flight_legs")
    mapping = resolve_question(question, schema)
    assert mapping.dimension == expected_dimension, mapping.explanation


# ─────────────────────────────────────────────────────────── the matrix
@pytest.fixture(scope="module")
def observations(tmp_path_factory: pytest.TempPathFactory) -> list[Observation]:
    """Every case, run once. Fixtures are generated into a temporary
    directory rather than committed; only the generators are in the tree."""
    import asyncio

    tmp: Path = tmp_path_factory.mktemp("corpus")

    async def run_all() -> list[Observation]:
        return [await run_case(case, tmp) for case in CASES]

    return asyncio.run(run_all())


@pytest.fixture(scope="module")
def remote_observations(tmp_path_factory: pytest.TempPathFactory) -> list[Observation]:
    """Every case again, this time against a provider that declares itself
    remote and keeps its prompts.

    A second pass rather than a flag on the first: the disclosure question
    only exists once inference is remote, and the answer has to be measured
    over the whole corpus, not over the handful of schemas that look
    obviously sensitive.
    """
    import asyncio

    tmp: Path = tmp_path_factory.mktemp("corpus-remote")

    async def run_all() -> list[Observation]:
        return [await run_case(case, tmp, remote=True) for case in CASES]

    return asyncio.run(run_all())


def test_no_prompt_ever_contains_a_withheld_cell(
    remote_observations: list[Observation],
) -> None:
    """The privacy boundary, across all 266 cases.

    A grouping column's labels are part of any aggregate over it, so they
    are not counted. Identifiers, near-unique text and free text are: a
    text column with a distinct value per row was once treated as a
    grouping, which sent uploaded cells to a remote provider as group
    labels.
    """
    leaked = [
        (o.case.case_id, len(o.disclosed_cells)) for o in remote_observations if o.disclosed_cells
    ]
    assert not leaked, leaked


def test_the_remote_matrix_reports_no_disclosure(
    remote_observations: list[Observation],
) -> None:
    """The Phase 3 report needs this as a measured number, not a claim."""
    matrix = report(remote_observations)
    assert matrix["raw_cell_disclosures"] == 0, matrix["raw_cell_disclosures"]
    for key in (
        "unexpected_failures",
        "unsupported_published",
        "irrelevant_published",
        "silent_guesses",
        "cross_run_leaks",
        "outcome_not_allowed",
    ):
        assert matrix[key] == 0, (key, matrix[key])


def test_the_two_modes_reach_the_same_outcome_on_every_case(
    observations: list[Observation],
    remote_observations: list[Observation],
) -> None:
    """Planning through a cloud model must not change what is answerable.

    The two modes differ in who reads the wording, not in what the engine
    will execute, so a case answered in one and refused in the other is a
    defect in whichever path is wrong -- and it is the reader who pays,
    because Compare Both shows the two side by side and offers no way to
    tell a planner disagreement from a data problem.

    This stood at five divergent cases. All five had the same cause: the
    AI path recorded a period's column in `time_field`, where the rule
    path records it in `period_field` and reserves `time_field` for a
    trend's axis. That changed the canonical contract without changing the
    SQL, and the MCP boundary then refused the engine's own contract. It
    is zero now, and this is what keeps it there.

    Compared on outcome and publication count rather than on wording: the
    reporter is free to phrase a finding differently and this is not a
    text-equality test.
    """
    diverged = [
        {
            "case": d.case.case_id,
            "question": d.case.question,
            "deterministic": (d.outcome.name, d.published, d.withheld_rules),
            "ai": (a.outcome.name, a.published, a.withheld_rules),
        }
        for d, a in zip(observations, remote_observations, strict=True)
        if d.outcome is not a.outcome or d.published != a.published
    ]
    assert not diverged, diverged


def test_no_case_fails_unexpectedly(observations: list[Observation]) -> None:
    failures = [(o.case.case_id, o.failure) for o in observations if o.failure]
    assert not failures, failures


def test_no_published_finding_is_unsupported(observations: list[Observation]) -> None:
    """Re-checked here rather than trusted. The corpus exists partly to
    catch the case where the publication gate itself is wrong."""
    bad = [(o.case.case_id, p) for o in observations for p in o.unsupported]
    assert not bad, bad


def test_no_published_finding_is_irrelevant(observations: list[Observation]) -> None:
    bad = [(o.case.case_id, p) for o in observations for p in o.irrelevant]
    assert not bad, bad


def test_nothing_guesses_a_measure_dimension_or_period(
    observations: list[Observation],
) -> None:
    bad = [(o.case.case_id, p) for o in observations for p in o.silent_guesses]
    assert not bad, bad


def test_no_finding_cites_another_runs_result(observations: list[Observation]) -> None:
    bad = [(o.case.case_id, p) for o in observations for p in o.leaked_results]
    assert not bad, bad


def test_every_outcome_is_one_the_case_allows(observations: list[Observation]) -> None:
    """The per-kind contract. An ambiguous question may be refused or may
    publish nothing; it may not answer."""
    wrong = [
        (o.case.case_id, o.outcome.value, sorted(a.value for a in o.case.allowed))
        for o in observations
        if not o.failure and o.outcome not in o.case.allowed
    ]
    assert not wrong, wrong


def test_an_answerable_question_actually_answers(observations: list[Observation]) -> None:
    """The other direction. A corpus that only checked for unsafe output
    would pass if the engine refused everything."""
    answered = sum(
        1 for o in observations if o.case.kind is QuestionKind.ANSWERABLE and o.published > 0
    )
    total = sum(1 for o in observations if o.case.kind is QuestionKind.ANSWERABLE)
    assert answered == total, f"{answered} of {total} answerable questions produced findings"


def test_a_refusal_always_explains_itself(observations: list[Observation]) -> None:
    """A refusal a visitor cannot act on is not much better than a guess."""
    silent = [
        o.case.case_id
        for o in observations
        if o.outcome.value == "safe_refusal" and not o.refusal_reason
    ]
    assert not silent, silent


def test_the_matrix_is_reported(observations: list[Observation]) -> None:
    """Prints the coverage table the release checklist asks for."""
    matrix = report(observations)
    for key in (
        "unexpected_failures",
        "unsupported_published",
        "irrelevant_published",
        "silent_guesses",
        "cross_run_leaks",
        "outcome_not_allowed",
    ):
        assert matrix[key] == 0, (key, matrix[key])
    assert matrix["total_cases"] == len(CASES)
    assert matrix["verified_answers"] > 0
    assert matrix["safe_refusals"] > 0
    assert matrix["zero_finding_completions"] > 0
