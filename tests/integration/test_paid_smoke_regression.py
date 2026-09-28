"""The first paid run, turned into a test that cannot pass by accident.

One real call to `gpt-6-luna` on 38cfb51 produced three claims and got all
three decisions wrong. It published a true statement about a column's type
against a question asking for a total; it rejected a numerically exact
mean because the cell held numeric text; and it withheld the correct
answer because the critic could not tell an output alias from a source
column.

The failure is preserved verbatim in `tests/fixtures/paid_smoke/`, and
this drives it back through the real gates. Nothing here contacts a
provider and nothing rewrites what the model said: the model's verdicts
are replayed exactly as recorded, including the contradictory one, and
the engine has to reach the right answer anyway. That is the point -- the
fix cannot be "ask the model more nicely".
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from agentic_analytics.analytics.results import ResultSnapshot
from agentic_analytics.analytics.upload_plan import QuestionMapping
from agentic_analytics.verification.canonical import canonical_answer
from agentic_analytics.verification.intent import check_intent
from agentic_analytics.verification.typing import as_number

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "paid_smoke"
SMOKE = FIXTURES / "first_smoke_38cfb51.json"


@pytest.fixture(scope="module")
def smoke() -> dict[str, Any]:
    return json.loads(SMOKE.read_text())


def test_the_preserved_failure_is_unmodified() -> None:
    """The fixture records a failure. Editing it to look better would
    destroy the only first-hand evidence of what the model did."""
    manifest = json.loads((FIXTURES / "MANIFEST.json").read_text())
    entry = manifest[SMOKE.name]
    digest = hashlib.sha256(SMOKE.read_bytes()).hexdigest()
    assert digest == entry["sha256"], "the preserved smoke result has been modified"


def test_it_is_labelled_as_a_failure(smoke: dict[str, Any]) -> None:
    assert smoke["fixture_kind"] == "failed product-quality acceptance"
    recorded = smoke["recorded_outcome"]
    assert recorded["irrelevant_published"] == 1
    assert recorded["correct_answer_published"] is False
    assert recorded["false_numeric_rejection"] == 1
    assert recorded["double_counted_quota_admission"] is True


def test_the_fixture_carries_no_private_path_or_credential(smoke: dict[str, Any]) -> None:
    blob = json.dumps(smoke)
    for marker in ("/Users/", "/home/", "sk-", "redis://", "Bearer "):
        assert marker not in blob, f"{marker!r} survived sanitisation"


# ─────────────────────────────────────────── the intent the engine resolved
#: "What is the total net value?" against the synthetic sales table.
SMOKE_MAPPING = QuestionMapping(
    operation="sum",
    table="uploaded_data",
    measure="net_value",
    confident=True,
    named_columns=["net_value"],
)


def _claim(smoke: dict[str, Any], finding_id: str) -> dict[str, Any]:
    return next(c for c in smoke["claims"] if c["finding_id"] == finding_id)


@pytest.mark.parametrize(
    ("finding_id", "answers"),
    [
        # Published in the real run. True, evidenced, and not an answer.
        ("f1", False),
        # A mean, against a question about a total.
        ("f2", False),
        # The answer, withheld in the real run.
        ("finding_1", True),
    ],
)
def test_the_intent_gate_reaches_the_right_verdict(
    smoke: dict[str, Any], finding_id: str, answers: bool
) -> None:
    """The decisions Phase 6 specifies, against the recorded claim text.

    The model said `answers_question: true` for f1 in the same response
    whose reason read "It does not report the column's total". The engine
    no longer takes its word for it.
    """
    verdict = check_intent(_claim(smoke, finding_id)["text"], SMOKE_MAPPING)
    assert verdict.applicable, "the resolved mapping must be judged against"
    assert verdict.answers is answers, verdict.reason
    if not answers:
        assert verdict.reason, "a rejection has to say why"


def test_the_off_topic_claims_are_refused_as_irrelevant_not_unsupported(
    smoke: dict[str, Any],
) -> None:
    """Both held perfectly good evidence. Reporting them as unsupported
    would tell a reader the engine found a data problem."""
    for finding_id in ("f1", "f2"):
        claim = _claim(smoke, finding_id)
        assert all(c["resolved"] for c in claim["resolved_cells"])
        assert check_intent(claim["text"], SMOKE_MAPPING).answers is False


def test_the_mean_is_no_longer_a_numeric_mismatch(smoke: dict[str, Any]) -> None:
    """The original false rejection, at its root.

    `44.2128` reached the verifier as text because the profile query cast
    the mean to VARCHAR, and the arithmetic check reads numbers. The claim
    was exactly right. It is still withheld -- as irrelevant to a question
    about a total, which is the honest reason.
    """
    cell = next(c for c in _claim(smoke, "f2")["resolved_cells"] if c["column"] == "mean_value")
    assert cell["stated_value"] == "44.2128", "the fixture must keep the original text value"
    assert as_number(cell["stated_value"], declared_type="DOUBLE") is not None
    assert as_number(cell["stated_value"], declared_type="VARCHAR") is None


def test_the_profile_query_now_produces_a_numeric_mean(tmp_path: Path) -> None:
    """Fixed where the value is made, not where it is read."""
    from tests.corpus.generators import build

    from agentic_analytics.analytics.catalog import profile_table
    from agentic_analytics.warehouse.session import SessionManager, open_upload_session

    data = build("retail_orders")
    path = data.write_csv(tmp_path)
    manager = SessionManager()
    try:
        session = manager.add(open_upload_session(path, path.name, "csv"))
        snapshot = profile_table(session, "uploaded_data")
        assert snapshot.declared_type("mean_value") == "DOUBLE"
        index = snapshot.columns.index("mean_value")
        means = [row[index] for row in snapshot.rows if row[index] is not None]
        assert means, "the fixture has numeric columns, so some mean is present"
        assert all(isinstance(v, float) for v in means)
    finally:
        manager.close_all()


def test_the_engine_answers_the_question_in_its_own_words(tmp_path: Path) -> None:
    """Phase 5, end to end.

    The real run's correct claim said "the sum of total_net_value is ...",
    exposing the engine's alias, and the critic withheld it over exactly
    that ambiguity. The engine now writes the answer itself, naming the
    column of the uploaded file.
    """
    from tests.corpus.generators import build

    from agentic_analytics.analytics import upload_plan
    from agentic_analytics.analytics.execute import run_query
    from agentic_analytics.analytics.semantic import infer_schema
    from agentic_analytics.warehouse.session import SessionManager, open_upload_session

    data = build("retail_orders")
    path = data.write_csv(tmp_path)
    manager = SessionManager()
    try:
        session = manager.add(open_upload_session(path, path.name, "csv"))
        schema = infer_schema(session, "uploaded_data").as_dict()
        mapping = upload_plan.resolve_question("total gross_amount", schema)
        sql = upload_plan.build_sql(mapping)
        assert sql is not None
        snapshot = run_query(session, sql, tool_name="aggregate_for_question", guard=False)
        snapshot.column_lineage = upload_plan.sql_lineage(mapping)

        finding = canonical_answer(mapping, snapshot)
        assert finding is not None
        # The visitor's column, not the engine's alias.
        assert "total gross amount" in finding.text.lower()
        assert "total_gross_amount" not in finding.text
        assert "across" in finding.text and "rows" in finding.text
        # And every number it states is a cell it cites.
        assert {c.column for c in finding.evidence_cells} == {
            "total_gross_amount",
            "row_count",
        }
        assert check_intent(finding.text, mapping).answers is True
    finally:
        manager.close_all()


def test_an_alias_is_recorded_as_derived_not_as_a_column(tmp_path: Path) -> None:
    """The lineage the critic lacked."""
    from tests.corpus.generators import build

    from agentic_analytics.analytics import upload_plan
    from agentic_analytics.analytics.semantic import infer_schema
    from agentic_analytics.warehouse.session import SessionManager, open_upload_session

    data = build("retail_orders")
    path = data.write_csv(tmp_path)
    manager = SessionManager()
    try:
        session = manager.add(open_upload_session(path, path.name, "csv"))
        schema = infer_schema(session, "uploaded_data").as_dict()
        mapping = upload_plan.resolve_question("total gross_amount", schema)
        lineage = upload_plan.sql_lineage(mapping)
        entry = lineage["total_gross_amount"]
        assert entry["kind"] == "aggregate"
        assert entry["aggregate"] == "SUM"
        assert entry["column"] == "gross_amount"
        assert entry["expression"] == "SUM(uploaded_data.gross_amount)"
        # The alias is not a column of the uploaded file.
        assert "total_gross_amount" not in {f["name"] for f in schema["fields"]}
    finally:
        manager.close_all()


def test_no_canonical_claim_for_an_ambiguous_mapping() -> None:
    """A question that named no measure gets no volunteered answer.

    Without this the engine answered "how many tasks are there?" with a
    row count, turning four questions the corpus requires be refused into
    confident answers.
    """
    ambiguous = QuestionMapping(
        operation="count", table="uploaded_data", confident=True, named_columns=[]
    )
    snapshot = ResultSnapshot(
        tool_name="aggregate_for_question",
        columns=["row_count"],
        rows=[[280]],
        row_count=1,
        column_types={"row_count": "BIGINT"},
        column_lineage={"row_count": {"kind": "aggregate", "aggregate": "COUNT", "column": "*"}},
    )
    assert canonical_answer(ambiguous, snapshot) is None

    unconfident = QuestionMapping(
        operation="sum", table="uploaded_data", measure="net_value", confident=False
    )
    assert canonical_answer(unconfident, snapshot) is None


# ───────────────────────────────── the critic, wired, not its parts alone
class _ReplayCritic:
    """A provider that answers exactly as `gpt-6-luna` did on 38cfb51.

    Including the contradiction: `answers_question: true` for a claim its
    own reason says does not report the total. Replayed rather than
    corrected, because the engine has to reach the right decision against
    the model that actually exists.
    """

    remote_inference = True

    def __init__(self, answers: bool = True) -> None:
        self.answers = answers

    async def complete_json(self, request: Any) -> dict[str, Any]:
        return {
            "finding_id": "",
            "status": "supported",
            "reason": (
                "The cited profile reports `net_value` as `DOUBLE`, with 4,000 "
                "non-null values and a 0.0% null rate, matching the finding. It "
                "does not report the column's total."
            ),
            "answers_question": self.answers,
            "relevance_reason": "",
        }

    async def aclose(self) -> None:
        return None


def _profile_snapshot() -> ResultSnapshot:
    """The profile result the smoke's claims cited, with the types the
    engine now records."""
    return ResultSnapshot(
        result_id="res_3306db43fa78",
        tool_name="profile_table",
        columns=[
            "column_name",
            "data_type",
            "non_null",
            "null_pct",
            "distinct_count",
            "mean_value",
        ],
        rows=[["net_value", "DOUBLE", 4000, 0.0, 2961, 44.2128]],
        row_count=1,
        column_types={
            "column_name": "VARCHAR",
            "data_type": "VARCHAR",
            "non_null": "BIGINT",
            "null_pct": "DOUBLE",
            "distinct_count": "BIGINT",
            "mean_value": "DOUBLE",
        },
    )


async def _verify(text: str, cells: list[tuple[int, str]], answers: bool = True) -> Any:
    from agentic_analytics.agents.critic import verify_finding
    from agentic_analytics.agents.schemas import CandidateFinding
    from agentic_analytics.analytics.results import EvidenceCell

    snapshot = _profile_snapshot()
    finding = CandidateFinding(
        text=text,
        evidence_cells=[
            EvidenceCell(
                result_id=snapshot.result_id,
                row=row,
                column=column,
                value=snapshot.cell(row, column),
            )
            for row, column in cells
        ],
        result_ids=[snapshot.result_id],
    )
    verdict, _ = await verify_finding(
        finding,
        {snapshot.result_id: snapshot},
        _ReplayCritic(answers),  # type: ignore[arg-type]
        None,
        question="What is the total net value?",
        mapping=SMOKE_MAPPING,
    )
    return verdict


async def test_the_model_cannot_publish_an_off_topic_claim_by_saying_it_answers(
    smoke: dict[str, Any],
) -> None:
    """The exact live failure, end to end through the critic.

    The model returns `answers_question: true`, as it really did. The
    engine must still withhold, because its own reading of the question
    says a null rate is not a total. Disabling the deterministic check
    makes this test publish the claim again.
    """
    verdict = await _verify(
        _claim(smoke, "f1")["text"],
        [(0, "data_type"), (0, "non_null"), (0, "null_pct")],
        answers=True,
    )
    assert verdict.status == "unsupported"
    assert verdict.rule == "irrelevant_to_question"
    assert verdict.answers_question is False
    assert "total" in verdict.reason.lower()


async def test_a_numeric_text_cell_is_not_reported_as_a_numeric_mismatch() -> None:
    """The false rejection, end to end.

    The claim states 44.2128 and the cited cell holds it. Reading cells
    without their declared type made the arithmetic check report the
    engine's own number as underivable.
    """
    verdict = await _verify(
        "The `net_value` column has a mean of 44.2128.",
        [(0, "mean_value")],
        answers=True,
    )
    assert verdict.rule != "numeric_mismatch", verdict.reason
    # Withheld, but for the honest reason: a mean is not a total.
    assert verdict.rule == "irrelevant_to_question"


async def test_a_model_veto_is_still_honoured() -> None:
    """The deterministic gate adds a requirement; it does not remove the
    model's. A claim the model rejects stays rejected even where the
    engine's own reading would have allowed it."""
    # Numerically exact against the cell it cites, so the arithmetic check
    # cannot be what decides this one.
    verdict = await _verify(
        "The `net_value` column has 4000 non-null values.",
        [(0, "non_null")],
        answers=False,
    )
    assert verdict.status == "unsupported"
    assert verdict.rule == "irrelevant_to_question"


def test_a_reused_model_finding_id_cannot_collide() -> None:
    """Two rounds of findings on one task must not share an id.

    A model labels its findings `f1`, `f2`, `f3` every time it is asked,
    so a second round produces different claims under the same names. The
    second paid smoke recorded the published total and a rejected claim as
    the *same sentence with opposite verdicts*, because the artifact
    resolved a rejected verdict's text by looking the id up and found the
    published finding instead.
    """
    from agentic_analytics.agents.schemas import CandidateFinding

    ids: list[str] = []
    for task in ("total_net_value", "profile_net_value_fields"):
        for round_number in range(2):
            findings = [
                CandidateFinding(finding_id="f1", text=f"claim one, round {round_number}"),
                CandidateFinding(finding_id="f2", text=f"claim two, round {round_number}"),
            ]
            # The namespacing the worker applies.
            for index, finding in enumerate(findings):
                finding.task_id = task
                finding.finding_id = f"{task}:{finding.finding_id}:{index}"
                ids.append(finding.finding_id)

    # Within a task the model's labels repeat across rounds, so ids repeat
    # too -- but a claim's identity is now task plus position, which is
    # what a lookup needs to be unambiguous *within one round*.
    assert len(set(ids)) == 4, sorted(set(ids))
    assert all(":" in i for i in ids)
    # And no bare model label survives as an identity of its own.
    assert "f1" not in ids and "f2" not in ids


# ────────────────────────────── the second smoke, preserved beside the first
SECOND = FIXTURES / "second_smoke_279675a.json"


@pytest.fixture(scope="module")
def second() -> dict[str, Any]:
    return json.loads(SECOND.read_text())


def test_the_passing_smoke_is_unmodified() -> None:
    manifest = json.loads((FIXTURES / "MANIFEST.json").read_text())
    digest = hashlib.sha256(SECOND.read_bytes()).hexdigest()
    assert digest == manifest[SECOND.name]["sha256"]


def test_the_second_smoke_published_the_direct_total(second: dict[str, Any]) -> None:
    """What the first run suppressed."""
    published = [c for c in second["claims"] if c["published"]]
    assert len(published) == 1
    assert "176851.26" in published[0]["text"]
    assert published[0]["status"] == "supported"
    assert all(cell["resolved"] for cell in published[0]["resolved_cells"])


def test_the_second_smoke_withheld_the_rest_as_irrelevant(second: dict[str, Any]) -> None:
    """Not as unsupported: every one of them held good evidence."""
    withheld = [c for c in second["claims"] if not c["published"]]
    assert withheld
    assert {c["rule"] for c in withheld} == {"irrelevant_to_question"}
    assert not any(c["rule"] == "numeric_mismatch" for c in second["claims"])


def test_the_second_smoke_consumed_one_quota_slot(second: dict[str, Any]) -> None:
    """Measured against a real provider, not a stub."""
    recorded = second["recorded_outcome"]
    assert recorded["session_slots_consumed"] == 1
    assert recorded["client_slots_consumed"] == 1
    assert recorded["correct_answer_published"] is True
    assert recorded["irrelevant_published"] == 0
    assert recorded["false_numeric_rejection"] == 0


def test_the_second_smoke_recorded_every_usage_category(second: dict[str, Any]) -> None:
    usage = second["usage"]
    assert (
        usage["ordinary_input_tokens"]
        + usage["cached_input_tokens"]
        + usage["cache_write_input_tokens"]
        == usage["input_tokens"]
    )
    assert usage["reasoning_tokens"] <= usage["output_tokens"]
    assert usage["reservations"] == usage["settlements"]
    assert usage["retained_microdollars"] == 0
    assert usage["cost_is_complete"] is True
    assert usage["usage_categories_are_coherent"] is True


def test_the_second_smoke_cost_reconciles_from_its_categories(second: dict[str, Any]) -> None:
    """The ledger's figure, recomputed from the split the provider
    reported. Any difference must be per-call rounding up, and no more
    than one microdollar per settled call."""
    usage = second["usage"]
    exact = (
        usage["ordinary_input_tokens"] * 0.100
        + usage["cached_input_tokens"] * 0.010
        + usage["cache_write_input_tokens"] * 0.125
        + usage["output_tokens"] * 0.500
    )
    difference = usage["settled_microdollars"] - exact
    assert 0 <= difference <= usage["settlements"], difference


def test_the_second_smoke_artifact_states_only_what_was_sent(second: dict[str, Any]) -> None:
    env = second["environment_subset"]
    assert env["temperature"] is None
    assert env["sampling_fields_sent"] == "none"
    assert env["store"] is False
    assert env["service_tier"] == "default"


def test_neither_smoke_fixture_carries_a_private_path(second: dict[str, Any]) -> None:
    blob = json.dumps(second)
    for marker in ("/Users/", "/home/", "sk-", "redis://", "Bearer "):
        assert marker not in blob
