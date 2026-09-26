"""The real-model harness itself, tested without a real model.

The evaluation is opt-in and never runs in CI, because it needs a live model
and is explicitly non-deterministic. That is a reason not to run *the
evaluation* in CI, not a reason to ship its machinery untested: the datasets
must generate, the outcomes must aggregate, and the report must not carry
dataset contents. All of that is checkable with a scripted provider.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentic_analytics.config import Settings
from agentic_analytics.evaluation.datasets import (
    DATASETS,
    SEED,
    WAREHOUSE_QUESTIONS,
    build_all,
)
from agentic_analytics.evaluation.real_model import (
    QuestionOutcome,
    RecordingProvider,
    _median,
    _summarise,
    evaluate_question,
    write_report,
)
from agentic_analytics.llm.base import LLMRequest
from agentic_analytics.llm.fake import FakeProvider
from agentic_analytics.warehouse.session import open_upload_session


# ------------------------------------------------------------- datasets
def test_every_dataset_builds(tmp_path: Path) -> None:
    built = build_all(tmp_path)
    assert set(built) == {d.dataset_id for d in DATASETS}
    for dataset_id, path in built.items():
        assert path.exists(), dataset_id
        assert path.stat().st_size > 0, dataset_id


def test_generation_is_deterministic(tmp_path: Path) -> None:
    """A rerun must evaluate the same data, or a difference in outcome
    cannot be attributed to the model."""
    first = build_all(tmp_path / "a")
    second = build_all(tmp_path / "b")
    for dataset_id in first:
        assert first[dataset_id].read_bytes() == second[dataset_id].read_bytes(), dataset_id
    assert SEED == 4242


def test_every_dataset_loads_as_a_session(tmp_path: Path) -> None:
    """Generating a file nothing can open would waste a whole evaluation."""
    built = build_all(tmp_path)
    for dataset in DATASETS:
        path = built[dataset.dataset_id]
        session = open_upload_session(path, path.name, dataset.file_format)  # type: ignore[arg-type]
        try:
            info = session.tables["uploaded_data"]
            assert info.row_count > 0, dataset.dataset_id
            assert len(info.columns) >= 2, dataset.dataset_id
        finally:
            session.close()


def test_the_datasets_do_not_share_a_vocabulary(tmp_path: Path) -> None:
    """The point of the set. A model that only ever sees `revenue` tells you
    nothing about what happens when the column is `spend_usd`."""
    built = build_all(tmp_path)
    columns_by_dataset: dict[str, set[str]] = {}
    for dataset in DATASETS:
        path = built[dataset.dataset_id]
        session = open_upload_session(path, path.name, dataset.file_format)  # type: ignore[arg-type]
        try:
            columns_by_dataset[dataset.dataset_id] = {
                c["name"].lower() for c in session.tables["uploaded_data"].columns
            }
        finally:
            session.close()

    names = list(columns_by_dataset)
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            shared = columns_by_dataset[left] & columns_by_dataset[right]
            assert not shared, f"{left} and {right} share {shared}"


def test_the_question_set_covers_the_kinds_that_stress_planning() -> None:
    kinds = {q.kind for d in DATASETS for q in d.questions}
    kinds |= {q.kind for q in WAREHOUSE_QUESTIONS}
    assert {"aggregate", "grouped", "ranking", "trend", "statistical"} <= kinds
    # The two that matter most: a question with no clear target, and one the
    # data cannot answer. Both should be refused rather than guessed at.
    assert "ambiguous" in kinds
    assert "unsupported" in kinds


def test_a_parquet_dataset_is_included() -> None:
    assert any(d.file_format == "parquet" for d in DATASETS)


def test_the_messy_dataset_is_actually_messy(tmp_path: Path) -> None:
    built = build_all(tmp_path)
    path = built["messy"]
    header = path.read_text().splitlines()[0]
    assert " " in header and "#" in header, header


# ----------------------------------------------------- recording provider
async def test_the_recording_provider_records_shape_not_content() -> None:
    """The report may be committed or shared; it must not carry the data."""
    inner = FakeProvider()
    provider = RecordingProvider(inner)
    await provider.complete_json(
        LLMRequest(
            role="question_analyst",
            system="s",
            user="a secret value 8675309 appears here",
            context={"question": "q", "metrics": [], "dimensions": []},
        )
    )
    await provider.aclose()

    blob = json.dumps(provider.exchanges)
    assert "8675309" not in blob
    assert provider.exchanges[0]["role"] == "question_analyst"
    assert provider.exchanges[0]["ok"] is True
    assert "seconds" in provider.exchanges[0]


async def test_a_provider_failure_is_recorded_and_re_raised() -> None:
    from agentic_analytics.llm.base import LLMError, LLMProvider

    class Broken(LLMProvider):
        name = "broken"
        requires_credentials = False

        async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
            raise LLMError("nope")

    provider = RecordingProvider(Broken())
    with pytest.raises(LLMError):
        await provider.complete_json(LLMRequest(role="planner", system="s", user="u"))
    await provider.aclose()

    assert provider.exchanges[0]["ok"] is False
    assert provider.exchanges[0]["error_type"] == "LLMError"


def test_the_wrapper_reports_the_inner_providers_usage() -> None:
    inner = FakeProvider()
    provider = RecordingProvider(inner)
    assert provider.usage is inner.usage


# ------------------------------------------------------------ end to end
async def test_a_question_runs_end_to_end_with_the_scripted_provider(
    tmp_path: Path,
) -> None:
    """Exercises the harness path itself, model excluded."""
    from agentic_analytics.evaluation.datasets import EvalQuestion

    built = build_all(tmp_path)
    path = built["sales"]
    cfg = Settings(provider_mode="fake", live_analytics_enabled=True)

    outcome = await evaluate_question(
        EvalQuestion("What is the total net value by territory?", "grouped"),
        "sales",
        lambda: open_upload_session(path, path.name, "csv"),
        cfg,
    )

    assert outcome.dataset == "sales"
    assert outcome.error == "", outcome.error
    assert outcome.provider_calls > 0
    assert outcome.tool_calls > 0
    assert outcome.runtime_seconds >= 0


async def test_a_crash_is_recorded_rather_than_raised(tmp_path: Path) -> None:
    """Finding crashes is the point, so one must not end the evaluation."""
    from agentic_analytics.evaluation.datasets import EvalQuestion

    def explode() -> Any:
        raise RuntimeError("no session for you")

    outcome = await evaluate_question(
        EvalQuestion("q", "aggregate"),
        "broken",
        explode,
        Settings(provider_mode="fake", live_analytics_enabled=True),
    )
    assert "RuntimeError" in outcome.error
    assert outcome.completed is False


# ------------------------------------------------------------ summarising
def _outcome(**kwargs: Any) -> QuestionOutcome:
    base: dict[str, Any] = {
        "dataset": "d",
        "question": "q",
        "kind": "grouped",
        "expectation": "",
    }
    return QuestionOutcome(**(base | kwargs))


def test_the_summary_counts_each_outcome_once() -> None:
    outcomes = [
        _outcome(completed=True, published_findings=2, provider_calls=5),
        _outcome(completed=False, stopped_reason="stopped", provider_calls=3),
        _outcome(error="Boom: x", provider_calls=1),
        _outcome(completed=True, provider_format_failures=1, tool_call_failures=2),
    ]
    summary = _summarise(Settings(provider_mode="local"), outcomes, 12.5)

    assert summary["questions_asked"] == 4
    assert summary["runs_completed"] == 2
    assert summary["runs_stopped_early"] == 1
    assert summary["runs_crashed"] == 1
    assert summary["runs_publishing_at_least_one_finding"] == 1
    assert summary["runs_with_a_provider_format_failure"] == 1
    assert summary["runs_with_a_failed_tool_call"] == 1
    assert summary["total_provider_calls"] == 9
    assert summary["is_deterministic"] is False


def test_the_summary_reports_which_rule_withheld_what() -> None:
    """A withheld finding is the pipeline working, and which gate fired
    matters more than the count."""
    outcomes = [
        _outcome(withheld_findings=2, withheld_rules={"causal_from_observational": 2}),
        _outcome(withheld_findings=1, withheld_rules={"numeric_mismatch": 1}),
    ]
    summary = _summarise(Settings(), outcomes, 1.0)
    assert summary["withheld_by_rule"] == {
        "causal_from_observational": 2,
        "numeric_mismatch": 1,
    }
    assert summary["total_withheld_findings"] == 3


def test_the_summary_groups_by_question_kind() -> None:
    outcomes = [
        _outcome(kind="ambiguous", completed=True),
        _outcome(kind="ambiguous", error="x"),
        _outcome(kind="ranking", completed=True, published_findings=1),
    ]
    summary = _summarise(Settings(), outcomes, 1.0)
    assert summary["by_question_kind"]["ambiguous"] == {
        "asked": 2,
        "completed": 1,
        "published_any": 0,
        "crashed": 1,
    }
    assert summary["by_question_kind"]["ranking"]["published_any"] == 1


def test_the_summary_names_the_model_for_each_provider_mode() -> None:
    local = _summarise(Settings(provider_mode="local", ollama_model="qwen3:4b"), [], 0.0)
    assert local["model"] == "qwen3:4b"
    cloud = _summarise(Settings(provider_mode="cloud", cloud_model="claude-sonnet-5"), [], 0.0)
    assert cloud["model"] == "claude-sonnet-5"


def test_the_summary_says_it_is_not_a_benchmark() -> None:
    summary = _summarise(Settings(), [], 0.0)
    assert summary["evaluation_kind"] == "real-model behavioural evaluation"
    assert "not a pass/fail benchmark" in summary["note"]
    assert "withheld finding is the pipeline working" in summary["note"]


@pytest.mark.parametrize(
    ("values", "expected"),
    [([], 0.0), ([3.0], 3.0), ([1.0, 3.0], 2.0), ([5.0, 1.0, 3.0], 3.0)],
)
def test_the_median_handles_both_parities(values: list[float], expected: float) -> None:
    assert _median(values) == expected


def test_the_report_writes_and_round_trips(tmp_path: Path) -> None:
    summary = _summarise(Settings(), [_outcome(completed=True)], 1.0)
    path = write_report(summary, tmp_path / "nested" / "report.json")
    assert path.exists()
    assert json.loads(path.read_text())["questions_asked"] == 1
