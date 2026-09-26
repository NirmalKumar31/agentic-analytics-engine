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
    _median,
    _summarise,
    evaluate_question,
    write_report,
)
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


# ----------------------------------------------------- call observation
# The provider-wrapping RecordingProvider is gone: it could only see whether
# the provider returned a dict, which is the measurement that made the first
# report untrue. Stage-accurate observation lives in `agents/base.py` and is
# tested in `tests/unit/test_structured_call_stages.py`.


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
        _outcome(completed=True, schema_validation_failures=1, tool_call_failures=2),
    ]
    summary = _summarise(Settings(provider_mode="local"), outcomes, 12.5)

    assert summary["questions_asked"] == 4
    assert summary["runs_completed"] == 2
    assert summary["runs_stopped_early"] == 1
    assert summary["runs_crashed"] == 1
    assert summary["runs_publishing_at_least_one_finding"] == 1
    assert summary["runs_with_a_schema_validation_failure"] == 1
    assert summary["runs_with_a_failed_tool_call"] == 1
    assert summary["total_provider_calls"] == 9
    assert summary["is_deterministic"] is False


def test_the_summary_separates_failure_stages() -> None:
    """A timeout is not a schema failure, and the report must not say it is."""
    outcomes = [
        _outcome(timeout_failures=2, failures_by_stage={"timeout": 2}),
        _outcome(schema_validation_failures=1, failures_by_stage={"schema_validation_error": 1}),
        _outcome(transport_failures=1, failures_by_stage={"transport_error": 1}),
        _outcome(question_timeout=True),
    ]
    summary = _summarise(Settings(), outcomes, 1.0)
    assert summary["runs_with_a_provider_timeout"] == 1
    assert summary["runs_with_a_schema_validation_failure"] == 1
    assert summary["runs_with_a_transport_failure"] == 1
    assert summary["runs_hitting_the_question_timeout"] == 1
    assert summary["failures_by_stage"] == {
        "schema_validation_error": 1,
        "timeout": 2,
        "transport_error": 1,
    }


def test_the_summary_separates_model_plans_from_engine_rescues() -> None:
    """A run the engine rescued is not evidence the model planned it."""
    outcomes = [
        _outcome(model_plan_directly_executable=True, outcome_flags=["direct_model_plan"]),
        _outcome(tasks_redirected_by_engine=2, outcome_flags=["engine_rescued"]),
        _outcome(fallback_plan_used=True, outcome_flags=["engine_rescued"]),
        _outcome(kind="unsupported", outcome_flags=["safe_refusal"]),
    ]
    summary = _summarise(Settings(), outcomes, 1.0)
    assert summary["runs_with_direct_model_plan"] == 1
    assert summary["runs_requiring_task_redirect"] == 1
    assert summary["runs_requiring_engine_fallback"] == 1
    assert summary["runs_safely_refusing"] == 1
    assert summary["outcome_flag_counts"]["engine_rescued"] == 2


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


# ---------------------------------------------- checkpointing and resume
def test_a_question_key_changes_when_the_question_changes() -> None:
    """Editing a question must invalidate its checkpoint.

    Resuming past a question whose text has changed would report a result
    for a question nobody asked.
    """
    from agentic_analytics.evaluation.real_model import question_key

    first = question_key("sales", 0, "What is the total?")
    assert first == question_key("sales", 0, "What is the total?")
    assert first != question_key("sales", 0, "What is the average?")
    assert first != question_key("sales", 1, "What is the total?")
    assert first != question_key("marketing", 0, "What is the total?")


def test_each_outcome_is_persisted_as_it_completes(tmp_path: Path) -> None:
    """The first sweep lost two hours because it wrote only at the end."""
    from agentic_analytics.evaluation.real_model import _append_outcome, _load_checkpoint

    path = tmp_path / "outcomes.jsonl"
    _append_outcome(path, "sales:00:abcd1234", _outcome(published_findings=3))
    assert path.exists(), "nothing was written after the first question"

    _append_outcome(path, "sales:01:beef5678", _outcome(withheld_findings=1))
    loaded = _load_checkpoint(path)
    assert set(loaded) == {"sales:00:abcd1234", "sales:01:beef5678"}
    assert loaded["sales:00:abcd1234"].published_findings == 3


def test_a_truncated_checkpoint_line_does_not_lose_the_rest(tmp_path: Path) -> None:
    """A process killed mid-write must not poison the resume."""
    from agentic_analytics.evaluation.real_model import _append_outcome, _load_checkpoint

    path = tmp_path / "outcomes.jsonl"
    _append_outcome(path, "a:00:1111", _outcome(published_findings=1))
    with path.open("a") as handle:
        handle.write('{"key": "b:00:2222", "outcome": {"dataset"\n')  # truncated
    _append_outcome(path, "c:00:3333", _outcome(published_findings=2))

    loaded = _load_checkpoint(path)
    assert set(loaded) == {"a:00:1111", "c:00:3333"}


def test_an_atomic_write_leaves_no_half_file(tmp_path: Path) -> None:
    from agentic_analytics.evaluation.real_model import _atomic_write

    path = tmp_path / "nested" / "meta.json"
    _atomic_write(path, '{"a": 1}')
    assert json.loads(path.read_text()) == {"a": 1}
    assert not list(path.parent.glob("*.tmp")), "a temp file was left behind"


async def test_resume_skips_completed_questions(tmp_path: Path) -> None:
    from agentic_analytics.evaluation.real_model import run_real_model_evaluation

    cfg = Settings(provider_mode="fake", live_analytics_enabled=True)
    checkpoint = tmp_path / "ckpt"

    first = await run_real_model_evaluation(
        cfg,
        tmp_path / "data",
        dataset_ids=["sales"],
        include_warehouse=False,
        max_questions=2,
        checkpoint_dir=checkpoint,
    )
    assert first["questions_asked"] == 2
    lines_after_first = (checkpoint / "outcomes.jsonl").read_text().splitlines()

    second = await run_real_model_evaluation(
        cfg,
        tmp_path / "data",
        dataset_ids=["sales"],
        include_warehouse=False,
        max_questions=2,
        checkpoint_dir=checkpoint,
    )
    assert second["questions_asked"] == 2
    # Nothing was re-run, so nothing new was appended.
    assert (checkpoint / "outcomes.jsonl").read_text().splitlines() == lines_after_first


async def test_resume_refuses_a_checkpoint_from_a_different_model(
    tmp_path: Path,
) -> None:
    """Silently mixing two models would produce a report about neither."""
    from agentic_analytics.evaluation.real_model import run_real_model_evaluation

    checkpoint = tmp_path / "ckpt"
    await run_real_model_evaluation(
        Settings(provider_mode="fake", live_analytics_enabled=True),
        tmp_path / "data",
        dataset_ids=["sales"],
        include_warehouse=False,
        max_questions=1,
        checkpoint_dir=checkpoint,
    )

    with pytest.raises(RuntimeError, match="different configuration"):
        await run_real_model_evaluation(
            Settings(provider_mode="local", ollama_model="other:1b"),
            tmp_path / "data",
            dataset_ids=["sales"],
            include_warehouse=False,
            max_questions=1,
            checkpoint_dir=checkpoint,
        )


async def test_a_status_file_makes_a_stalled_run_diagnosable(tmp_path: Path) -> None:
    from agentic_analytics.evaluation.real_model import run_real_model_evaluation

    checkpoint = tmp_path / "ckpt"
    await run_real_model_evaluation(
        Settings(provider_mode="fake", live_analytics_enabled=True),
        tmp_path / "data",
        dataset_ids=["sales"],
        include_warehouse=False,
        max_questions=1,
        checkpoint_dir=checkpoint,
    )
    status = json.loads((checkpoint / "status.json").read_text())
    assert status["finished"] is True
    assert status["total"] == 1
    assert "environment" in status


# ------------------------------------------------------ question timeout
async def test_a_question_that_runs_long_is_cut_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A per-call timeout does not bound a question.

    Forty calls at three minutes each is two hours for one answer, which is
    exactly how the first sweep became unreadable.
    """
    import asyncio

    from agentic_analytics.evaluation.datasets import EvalQuestion

    async def hang(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(30)

    monkeypatch.setattr("agentic_analytics.evaluation.real_model.run_analysis", hang)
    built = build_all(tmp_path)
    path = built["sales"]

    outcome = await evaluate_question(
        EvalQuestion("q", "aggregate"),
        "sales",
        lambda: open_upload_session(path, path.name, "csv"),
        Settings(provider_mode="fake", live_analytics_enabled=True),
        question_timeout_seconds=0.3,
    )

    assert outcome.question_timeout is True
    assert "question_timeout" in outcome.outcome_flags
    assert outcome.error == "", "a timeout is not a crash"
    assert outcome.runtime_seconds < 10


# ------------------------------------------------------------ provenance
def test_the_environment_fingerprint_records_what_was_evaluated() -> None:
    """`model = qwen3:4b` alone cannot be reproduced or trusted."""
    from agentic_analytics.evaluation.real_model import environment_fingerprint

    info = environment_fingerprint(
        Settings(provider_mode="local", ollama_model="qwen3:4b", ollama_think=False)
    )
    assert info["provider_mode"] == "local"
    assert info["model"] == "qwen3:4b"
    assert info["think"] is False
    assert info["dataset_seed"] == SEED
    assert info["harness_schema"] >= 2
    # Budgets are part of what was evaluated.
    assert "max_llm_calls" in info and "max_tool_calls_per_task" in info


def test_a_missing_optional_provenance_field_does_not_fail_the_run() -> None:
    """Best effort: no metadata lookup may abort an evaluation."""
    from agentic_analytics.evaluation.real_model import environment_fingerprint

    info = environment_fingerprint(Settings(provider_mode="cloud", cloud_model="m"))
    assert info["model"] == "m"
    # Ollama-only fields are simply absent rather than raising.
    assert "quantization" not in info or info["quantization"] is None
