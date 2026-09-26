"""The worker-findings probe: does it measure what it claims to measure?

The probe itself is run against a real model by hand. These tests run it
against the scripted provider, and against deliberately broken ones, because
a diagnostic that reports "all checks passed" whatever it is given would have
let the original empty-sweep mystery stand.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agentic_analytics.analytics.results import ResultSnapshot
from agentic_analytics.evaluation.worker_probe import (
    ProbeCase,
    probe_cases,
    run_probe,
    write_probe_report,
)
from agentic_analytics.llm.base import LLMProvider, LLMRequest
from agentic_analytics.llm.fake import FakeProvider


class Scripted(LLMProvider):
    """Returns a fixed findings payload, and approves everything as critic."""

    name = "scripted"
    requires_credentials = False

    def __init__(self, findings: list[dict[str, Any]]) -> None:
        super().__init__(max_calls=64)
        self.findings = findings

    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        self._check_budget(request.role)
        self.usage.record(request.role)
        if request.role == "worker_findings":
            return {"findings": self.findings}
        return {"status": "supported", "reason": "The wording matches the cited result."}


SIMPLE = ResultSnapshot(
    result_id="res_x",
    tool_name="aggregate_for_question",
    columns=["region", "revenue"],
    rows=[["North", 52100.0], ["South", 38200.5]],
    row_count=2,
)


def _case(**kwargs: Any) -> ProbeCase:
    base: dict[str, Any] = {
        "case_id": "X",
        "title": "t",
        "objective": "o",
        "results": [SIMPLE],
        "expectation": "e",
    }
    return ProbeCase(**(base | kwargs))


# -------------------------------------------------------------- the cases


def test_the_five_cases_cover_what_the_probe_claims_to_cover() -> None:
    cases = probe_cases()
    assert [c.case_id for c in cases] == ["A", "B", "C", "D", "E"]
    by_id = {c.case_id: c for c in cases}

    # D must carry a real statistical result, or a significance claim can
    # never be legitimately tested.
    assert by_id["D"].results[0].statistical_result is not None
    # E must not, or "do not claim significance" is untestable there.
    assert by_id["E"].results[0].statistical_result is None
    assert by_id["E"].substantive_conclusion_available is False
    assert all(c.substantive_conclusion_available for c in cases if c.case_id != "E")
    # C is a time series: more than two ordered periods.
    assert len(by_id["C"].results[0].rows) >= 3


def test_the_fixed_results_are_internally_consistent() -> None:
    """Every case's rows must match its declared columns and row_count.

    A probe handing the model a malformed table would measure the table.
    """
    for case in probe_cases():
        for result in case.results:
            assert result.rows, case.case_id
            assert result.row_count == len(result.rows), case.case_id
            for row in result.rows:
                assert len(row) == len(result.columns), case.case_id


def test_the_values_are_not_round_enough_to_guess() -> None:
    """A model reproducing 100000 may have guessed; 128450.75 it read."""
    numbers = [
        value
        for case in probe_cases()
        for result in case.results
        for row in result.rows
        for value in row
        if isinstance(value, float)
    ]
    assert numbers
    assert any(abs(n - round(n)) > 0.001 for n in numbers)


# ------------------------------------------------------------- the checks


async def test_the_probe_runs_end_to_end_against_the_scripted_provider() -> None:
    provider = FakeProvider(max_calls=200)
    report = await run_probe(provider, probe_cases())

    assert report["cases"] == 5
    assert report["schema_valid_cases"] == 5
    assert report["total_findings_emitted"] > 0
    assert len(report["case_reports"]) == 5
    # Serialisable: the artifact is the point.
    assert json.loads(json.dumps(report, default=str))


async def test_a_claim_citing_a_result_that_does_not_exist_is_caught() -> None:
    provider = Scripted(
        [
            {
                "text": "Revenue was 52100.00 in the North.",
                "kind": "calculated_fact",
                "result_ids": ["res_does_not_exist"],
                "evidence_cells": [
                    {"result_id": "res_does_not_exist", "row": 0, "column": "revenue"}
                ],
            }
        ]
    )
    report = await run_probe(provider, [_case()])
    claim = report["case_reports"][0]["claims"][0]

    assert claim["result_ids_valid"] is False
    assert claim["evidence_rows_valid"] is False
    assert claim["published"] is False
    assert claim["verdict_rule"] == "missing_result"
    assert report["diagnosis"]["claims_citing_an_unknown_result"] == 1


async def test_a_claim_pointing_at_a_row_that_is_not_there_is_caught() -> None:
    provider = Scripted(
        [
            {
                "text": "Revenue was 52100.00 in the North.",
                "kind": "calculated_fact",
                "result_ids": ["res_x"],
                "evidence_cells": [{"result_id": "res_x", "row": 9, "column": "revenue"}],
            }
        ]
    )
    report = await run_probe(provider, [_case()])
    claim = report["case_reports"][0]["claims"][0]

    assert claim["result_ids_valid"] is True
    assert claim["evidence_rows_valid"] is False
    assert claim["evidence_columns_valid"] is True
    assert report["diagnosis"]["claims_with_an_invalid_row"] == 1


async def test_a_claim_naming_a_column_that_is_not_there_is_caught() -> None:
    provider = Scripted(
        [
            {
                "text": "Revenue was 52100.00 in the North.",
                "kind": "calculated_fact",
                "result_ids": ["res_x"],
                "evidence_cells": [{"result_id": "res_x", "row": 0, "column": "turnover"}],
            }
        ]
    )
    report = await run_probe(provider, [_case()])
    claim = report["case_reports"][0]["claims"][0]

    assert claim["evidence_columns_valid"] is False
    assert report["diagnosis"]["claims_with_an_invalid_column"] == 1


async def test_a_miscopied_evidence_value_is_caught() -> None:
    """The check that distinguishes reading a table from paraphrasing one."""
    provider = Scripted(
        [
            {
                "text": "Revenue was 52100.00 in the North.",
                "kind": "calculated_fact",
                "result_ids": ["res_x"],
                "evidence_cells": [
                    {"result_id": "res_x", "row": 0, "column": "revenue", "value": 52000.0}
                ],
            }
        ]
    )
    report = await run_probe(provider, [_case()])
    claim = report["case_reports"][0]["claims"][0]

    assert claim["evidence_values_copied"] == 1
    assert claim["evidence_values_correct"] == 0
    assert report["diagnosis"]["claims_that_miscopied_a_value"] == 1


async def test_a_correctly_copied_value_is_not_reported_as_miscopied() -> None:
    """52100 and 52100.0 are the same number; a probe saying otherwise lies."""
    provider = Scripted(
        [
            {
                "text": "Revenue was 52100.00 in the North.",
                "kind": "calculated_fact",
                "result_ids": ["res_x"],
                "evidence_cells": [
                    {"result_id": "res_x", "row": 0, "column": "revenue", "value": 52100},
                    {"result_id": "res_x", "row": 0, "column": "region", "value": "North"},
                ],
            }
        ]
    )
    report = await run_probe(provider, [_case()])
    claim = report["case_reports"][0]["claims"][0]

    assert claim["evidence_values_copied"] == 2
    assert claim["evidence_values_correct"] == 2
    assert claim["evidence_rows_valid"] is True
    assert claim["evidence_columns_valid"] is True
    assert report["diagnosis"]["claims_that_miscopied_a_value"] == 0


async def test_the_probe_does_not_weaken_the_significance_gate() -> None:
    """Case E with no test: "significantly" must still be refused.

    If the probe ran a relaxed pipeline, a model failing here would look
    like it passed, which is the exact failure the probe exists to avoid.
    """
    provider = Scripted(
        [
            {
                "text": "Pro converts significantly faster at 14.20 days against 14.26.",
                "kind": "statistical_result",
                "result_ids": ["res_x"],
                "evidence_cells": [{"result_id": "res_x", "row": 0, "column": "revenue"}],
            }
        ]
    )
    report = await run_probe(provider, [_case()])
    claim = report["case_reports"][0]["claims"][0]

    assert claim["claim_shape_ok"] is False
    assert claim["claim_shape_rule"] == "significance_without_test"
    assert claim["published"] is False
    assert report["claim_shape_failures"] == 1


async def test_the_probe_does_not_weaken_the_causal_gate() -> None:
    provider = Scripted(
        [
            {
                "text": "The North's revenue of 52100.00 was caused by the new campaign.",
                "kind": "interpretation",
                "result_ids": ["res_x"],
                "evidence_cells": [{"result_id": "res_x", "row": 0, "column": "revenue"}],
            }
        ]
    )
    report = await run_probe(provider, [_case()])
    claim = report["case_reports"][0]["claims"][0]

    assert claim["claim_shape_rule"] == "causal_from_observational"
    assert claim["published"] is False


async def test_a_number_that_is_in_no_cited_cell_fails_numeric_verification() -> None:
    provider = Scripted(
        [
            {
                "text": "Revenue in the North was 61000.00.",
                "kind": "calculated_fact",
                "result_ids": ["res_x"],
                "evidence_cells": [{"result_id": "res_x", "row": 0, "column": "revenue"}],
            }
        ]
    )
    report = await run_probe(provider, [_case()])
    claim = report["case_reports"][0]["claims"][0]

    assert claim["claim_shape_ok"] is True
    assert claim["numeric_ok"] is False
    assert claim["published"] is False
    assert claim["verdict_rule"] == "numeric_mismatch"


async def test_a_well_formed_claim_reaches_the_critic_and_publishes() -> None:
    """The probe must be capable of reporting success, or it proves nothing."""
    provider = Scripted(
        [
            {
                "text": "Revenue in the North was 52100.00.",
                "kind": "calculated_fact",
                "result_ids": ["res_x"],
                "evidence_cells": [
                    {"result_id": "res_x", "row": 0, "column": "revenue", "value": 52100.0}
                ],
            }
        ]
    )
    report = await run_probe(provider, [_case()])
    claim = report["case_reports"][0]["claims"][0]

    assert claim["claim_shape_ok"] is True
    assert claim["numeric_ok"] is True
    assert claim["verdict_status"] == "supported"
    assert claim["verdict_rule"] == "critic"
    assert claim["published"] is True
    assert report["total_published"] == 1


# ------------------------------------------------------- the three causes


async def test_a_provider_that_never_answers_is_reported_as_a_call_failure() -> None:
    """Cause 1 and cause 3 must not look alike in the artifact."""
    from agentic_analytics.llm.base import LLMError

    class Broken(LLMProvider):
        name = "broken"
        requires_credentials = False

        async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
            self._check_budget(request.role)
            raise LLMError("the language model did not respond in time", kind="timeout")

    report = await run_probe(Broken(), [_case()])
    case = report["case_reports"][0]

    assert case["schema_valid"] is False
    assert case["stage"] == "timeout"
    assert case["error"]
    assert case["findings_emitted"] == 0
    assert report["diagnosis"]["cases_where_the_call_itself_failed"] == 1
    # Not counted as "answered cleanly and had nothing to say".
    assert report["diagnosis"]["cases_with_a_valid_response_and_no_finding"] == 0


async def test_a_clean_empty_response_is_reported_separately() -> None:
    """A valid, empty answer is a different finding about the model."""
    report = await run_probe(Scripted([]), [_case()])
    case = report["case_reports"][0]

    assert case["schema_valid"] is True
    assert case["stage"] == "success"
    assert case["findings_emitted"] == 0
    assert report["diagnosis"]["cases_with_a_valid_response_and_no_finding"] == 1
    assert report["diagnosis"]["cases_where_the_call_itself_failed"] == 0
    assert report["diagnosis"]["answerable_cases_producing_nothing"] == 1


async def test_case_e_emptiness_is_not_counted_against_the_model() -> None:
    """Silence on case E is the correct answer, not a failure to produce one."""
    case = _case(case_id="E", substantive_conclusion_available=False)
    report = await run_probe(Scripted([]), [case])

    assert report["diagnosis"]["cases_with_a_valid_response_and_no_finding"] == 1
    assert report["diagnosis"]["answerable_cases_producing_nothing"] == 0
    assert report["diagnosis"]["answerable_cases_producing_a_published_finding"] == 0


# ------------------------------------------------------------- the report


async def test_the_report_keeps_the_text_of_every_claim() -> None:
    """The counts say a claim failed; only the wording says why."""
    text = "Revenue in the North was 61000.00, which proves the campaign worked."
    provider = Scripted(
        [
            {
                "text": text,
                "kind": "interpretation",
                "result_ids": ["res_x"],
                "evidence_cells": [{"result_id": "res_x", "row": 0, "column": "revenue"}],
            }
        ]
    )
    report = await run_probe(provider, [_case()])
    claim = report["case_reports"][0]["claims"][0]

    assert claim["text"] == text
    assert claim["verdict_reason"]
    assert claim["kind"] == "interpretation"


async def test_the_report_records_which_model_produced_it(tmp_path: Any) -> None:
    provider = FakeProvider(max_calls=64)
    report = await run_probe(provider, [_case()])

    assert report["probe"] == "worker_findings"
    assert report["provider"] == "fake"
    assert report["generated_at"].endswith("Z")
    assert report["usage"]["provider_request_attempts"] >= 1

    path = write_probe_report(report, tmp_path / "nested" / "probe.json")
    assert json.loads(path.read_text())["probe"] == "worker_findings"


async def test_the_probe_calls_the_same_prompt_the_engine_uses() -> None:
    """A probe of a copied prompt measures the copy."""
    seen: dict[str, str] = {}

    class Recording(LLMProvider):
        name = "recording"
        requires_credentials = False

        async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
            self._check_budget(request.role)
            self.usage.record(request.role)
            seen[request.role] = request.system
            return {"findings": []}

    await run_probe(Recording(), [_case()])

    from agentic_analytics.agents.prompts import WORKER_FINDINGS

    assert seen["worker_findings"] == WORKER_FINDINGS


def test_the_probe_is_not_reachable_with_the_scripted_provider_from_the_cli() -> None:
    """Running it against `fake` would produce a meaningless artifact."""
    from typer.testing import CliRunner

    from agentic_analytics.cli import app

    result = CliRunner().invoke(app, ["probe-worker-findings"], env={"AAE_PROVIDER_MODE": "fake"})
    assert result.exit_code == 2
    assert "fake" in result.stdout


def test_an_unknown_case_id_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from agentic_analytics.cli import app

    monkeypatch.setenv("AAE_PROVIDER_MODE", "local")
    result = CliRunner().invoke(app, ["probe-worker-findings", "--cases", "Z"])
    assert result.exit_code == 2


async def test_citing_a_p_value_is_not_recorded_as_an_invented_column() -> None:
    """The probe found a gap in the contract, not a model mistake.

    qwen2.5:7b cited `res_stats[0].p_value` for a significance claim. There
    is no `p_value` column -- the number lives in `statistical_result` -- so
    no cell reference can be valid for a claim about one, however careful
    the model is. Recording that as "invented a column name" would blame the
    model for a limit of `EvidenceCell`.
    """
    from agentic_analytics.analytics.results import StatisticalResult

    result = ResultSnapshot(
        result_id="res_s",
        tool_name="compare_segments",
        columns=["cohort", "mean_basket"],
        rows=[["returning", 84.32], ["new", 71.08]],
        row_count=2,
        statistical_result=StatisticalResult(
            test_name="welch_t_test", statistic=6.41, p_value=0.00000019
        ),
    )
    provider = Scripted(
        [
            {
                "text": "The difference is statistically significant (p-value = 1.9e-07).",
                "kind": "statistical_result",
                "result_ids": ["res_s"],
                "evidence_cells": [{"result_id": "res_s", "row": 0, "column": "p_value"}],
            }
        ]
    )
    report = await run_probe(provider, [_case(results=[result])])
    claim = report["case_reports"][0]["claims"][0]

    assert claim["statistical_field_references"] == 1
    assert claim["evidence_columns_valid"] is True
    assert report["diagnosis"]["claims_with_an_invalid_column"] == 0
    assert report["diagnosis"]["claims_citing_a_statistical_field_as_a_column"] == 1


async def test_an_invented_column_is_still_recorded_as_one() -> None:
    """The distinction must not become an excuse for any bad reference."""
    provider = Scripted(
        [
            {
                "text": "Revenue was 52100.00.",
                "kind": "calculated_fact",
                "result_ids": ["res_x"],
                "evidence_cells": [{"result_id": "res_x", "row": 0, "column": "p_value"}],
            }
        ]
    )
    report = await run_probe(provider, [_case()])
    # `res_x` has no statistical result, so `p_value` names nothing at all.
    assert report["diagnosis"]["claims_with_an_invalid_column"] == 1
    assert report["diagnosis"]["claims_citing_a_statistical_field_as_a_column"] == 0
