"""A statistical test, presented as the answer to the question.

`detect_shape` has returned `PresentationShape.STATISTICAL_TEST` for a long
time and nothing ever selected a test to present, so the branch was
unreachable and there was nothing to render it. See `graph/relevance.py`
for how a relationship question ended up headlined with an unrelated
breakdown instead.

What these pin is the part that is easy to get subtly wrong: the figures,
their unit, and the refusal of the causal reading.
"""

from __future__ import annotations

import re

from agentic_analytics.analytics.results import ResultSnapshot, StatisticalResult
from agentic_analytics.graph.runner import _statistical_contract
from agentic_analytics.presentation import build_presentation
from agentic_analytics.presentation.summarize import format_p_value


def snapshot() -> ResultSnapshot:
    """The result a real run produced, numbers included."""
    return ResultSnapshot(
        result_id="res_test",
        tool_name="statistical_test",
        columns=["group", "n", "successes", "rate"],
        rows=[
            ["late", 3401, 1716.0, 0.5045574830932079],
            ["on_time", 26599, 18730.0, 0.7041618105943832],
        ],
        row_count=2,
        parameters={
            "test_type": "two_proportion_z",
            "variables": {
                "group_column": "first_delivery_status",
                "value_column": "is_repeat",
                "groups": ["late", "on_time"],
            },
            "filters": [],
        },
        statistical_result=StatisticalResult(
            test_name="two-proportion z-test",
            statistic=-23.527173440763832,
            p_value=2.1504784686352304e-122,
            sample_sizes={"late": 3401, "on_time": 26599},
            effect_size=-0.41150176507441105,
            effect_size_name="Cohen's h",
        ),
    )


METRICS = ("repeat_purchase_rate", "late_delivery_rate", "avg_delivery_days")


def presentation(metrics: tuple[str, ...] = METRICS):
    return build_presentation(
        mapping=_statistical_contract(snapshot(), None, metrics),
        snapshot=snapshot(),
        findings=[],
        question_coverage=None,
        chart_decision={},
        schema=None,
        planner_fallback=False,
        outcome="completed",
        stopped_reason="",
    )


class TestTheHeadlineStatesTheAssociation:
    def test_the_shape_is_a_statistical_test(self) -> None:
        assert presentation().shape.value == "statistical_test"

    def test_it_names_the_question_s_own_metric(self) -> None:
        """Not the test's indicator column.

        `value_column` is `is_repeat`, and humanising it produced the
        headline "Is repeat is 50.46% for late" -- a column name spoken
        aloud. The subject is the first metric the planner declared.
        """
        headline = presentation().headline
        assert headline.startswith("Repeat purchase rate is ")
        assert "Is repeat" not in headline

    def test_it_states_both_groups_with_their_figures(self) -> None:
        headline = presentation().headline
        assert "50.46% for late" in headline
        assert "70.42% for on_time" in headline

    def test_the_rate_is_written_as_a_percentage(self) -> None:
        """`rate` is `successes / n`, which is 0.5045 as stored.

        Not inferred from the magnitude: `stats.py:_require_binary` refuses
        any value column that is not an indicator, so the tool's contract
        guarantees the range and the scale is declared.
        """
        headline = presentation().headline
        assert "0.50" not in headline
        assert "0.5045" not in headline

    def test_no_float_tail_reaches_the_reader(self) -> None:
        text = f"{presentation().headline} {presentation().secondary_summary}"
        assert not re.search(r"\d+\.\d{6,}", text)


class TestItRefusesTheCausalReading:
    def test_the_secondary_line_says_association_not_cause(self) -> None:
        secondary = presentation().secondary_summary or ""
        assert "association, not a cause" in secondary

    def test_it_says_why_the_causal_reading_is_unavailable(self) -> None:
        assert "not assigned at random" in (presentation().secondary_summary or "")

    def test_the_refusal_travels_with_the_claim_not_as_a_caveat(self) -> None:
        """A reader takes the headline away; a caveat they may never open.

        The engine already withholds a *finding* that asserts causation.
        This is the same rule applied to the headline, and the test is that
        the correction is in the sentence beneath it rather than filed
        under "What to be careful about".
        """
        result = presentation()
        assert "association" in (result.secondary_summary or "")
        assert not any("association" in caveat.message for caveat in result.caveats), (
            "the causal refusal is a caveat; it must be in the summary"
        )

    def test_it_names_the_test_and_the_population(self) -> None:
        secondary = presentation().secondary_summary or ""
        assert "two-proportion z-test" in secondary
        assert "30,000 observations" in secondary
        assert "first delivery status" in secondary


class TestTheFiguresAreTraceable:
    def test_the_sample_total_is_allowed_because_the_test_reported_it(self) -> None:
        """The numeric guard refused the first version of this headline.

        It allowed cells, row counts, coverage counts and declared
        differences, and 30,000 is none of those -- it is the sum of the
        test's own `sample_sizes`. The guard was right about an incomplete
        list of sources, not about a false sentence, so the sources grew.
        """
        assert "30,000" in (presentation().secondary_summary or "")

    def test_a_scaled_cell_is_allowed_because_it_is_the_same_cell(self) -> None:
        """50.46% is 0.5045 in the unit the field declares."""
        assert "50.46%" in presentation().headline

    def test_every_highlight_cites_a_cell(self) -> None:
        for highlight in presentation().highlights:
            assert highlight.evidence_cells, highlight.highlight_id


class TestAPValueAReaderCanAct0n:
    def test_a_vanishing_p_is_a_bound_not_an_exponent(self) -> None:
        assert format_p_value(2.1504784686352304e-122) == "p < 0.001"

    def test_an_ordinary_p_is_stated(self) -> None:
        assert format_p_value(0.042) == "p = 0.042"

    def test_an_unreportable_p_says_so(self) -> None:
        """`nan < 0.001` is False, so it fell through and published "p = nan"."""
        for value in (float("nan"), float("inf"), float("-inf")):
            assert format_p_value(value) == "an unreported p-value"


class TestNoChartIsExplainedForAReader:
    def test_a_test_says_why_rather_than_naming_an_internal_record(self) -> None:
        """It said "no chart decision was recorded for this result"."""
        chart = presentation().chart
        assert chart is not None and chart.kind == "none"
        reason = chart.no_chart_reason or ""
        assert "decision was recorded" not in reason
        assert "comparison of two groups" in reason

    def test_and_says_it_once(self) -> None:
        """It was inline *and* a caveat, so a reader was told twice.

        "What to be careful about" is for things that qualify the answer. A
        missing chart does not qualify an answer.
        """
        result = presentation()
        assert not any(caveat.code == "no_chart" for caveat in result.caveats)


class TestTheVisualiserCannotChooseAnInvalidEncoding:
    """`categorical` is a reasonable word and not a Vega-Lite type.

    A live run's visualiser chose it, `verification/charts.py` refused the
    specification -- "encoding type 'categorical' is not allowed" -- and the
    chart was lost. The refusal was right; the loss was avoidable.

    `ChartChoice` now declares the allowed values as `Literal`, so the
    provider's structured output is constrained to them. The same rule
    `preflight` follows for tool arguments: refuse the mistake at the
    boundary rather than repair its output.
    """

    def test_the_literals_are_exactly_what_the_validator_allows(self) -> None:
        import typing

        from agentic_analytics.agents.visualizer import ChartMark, EncodingType
        from agentic_analytics.verification.charts import ALLOWED_MARKS, ALLOWED_TYPES

        assert set(typing.get_args(ChartMark)) == ALLOWED_MARKS
        assert set(typing.get_args(EncodingType)) == ALLOWED_TYPES

    def test_the_value_that_lost_a_chart_is_now_refused(self) -> None:
        import pytest as _pytest
        from pydantic import ValidationError

        from agentic_analytics.agents.visualizer import ChartChoice

        with _pytest.raises(ValidationError):
            ChartChoice(x="a", y="b", x_type="categorical")

    def test_an_invalid_mark_is_refused_too(self) -> None:
        import pytest as _pytest
        from pydantic import ValidationError

        from agentic_analytics.agents.visualizer import ChartChoice

        with _pytest.raises(ValidationError):
            ChartChoice(x="a", y="b", mark="pie")

    def test_a_valid_choice_still_builds(self) -> None:
        from agentic_analytics.agents.visualizer import ChartChoice

        choice = ChartChoice(x="region", y="revenue", mark="bar", x_type="nominal")
        assert choice.x_type == "nominal"
