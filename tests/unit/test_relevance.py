"""Which result answers the question.

The defect these pin is the one that took longest to find, because nothing
about it looked like a fault: the report was well formed, the numbers were
right, the labels were clean. It answered a different question.

Asked "do shipping delays appear to affect repeat purchasing?", a run
computed a two-proportion z-test showing a 50.5% repeat rate after a late
delivery against 70.4% after an on-time one, p = 2e-122, and published

    "Repeat purchase rate is highest for social, at 69.81%,
     and lowest for affiliate, at 65.58%."

Three reported faults came out of that one cause, and all three are
asserted here: a statistical test could never be selected, the two
planning strategies looked like they disagreed when only their tool mix
differed, and the headline's result happened to be the one with no chart.
"""

from __future__ import annotations

import pytest

from agentic_analytics.analytics.results import ResultSnapshot, StatisticalResult
from agentic_analytics.graph import relevance


def breakdown(metric: str, dimension: str, *, rows: int = 4, rid: str = "") -> ResultSnapshot:
    return ResultSnapshot(
        result_id=rid or f"res_{metric}_{dimension}",
        tool_name="compare_segments",
        columns=[dimension, metric],
        rows=[[f"g{i}", 1.0 + i] for i in range(rows)],
        row_count=rows,
        parameters={"metric": metric, "dimension": dimension, "filters": []},
    )


def series(metric: str, *, rid: str = "") -> ResultSnapshot:
    return ResultSnapshot(
        result_id=rid or f"res_{metric}_series",
        tool_name="analyze_timeseries",
        columns=["period", metric],
        rows=[["2025-01-01", 1.0], ["2025-02-01", 2.0]],
        row_count=2,
        parameters={"metric": metric, "grain": "month", "filters": []},
    )


def wide(metric: str, dimensions: list[str], *, rid: str = "") -> ResultSnapshot:
    return ResultSnapshot(
        result_id=rid or f"res_{metric}_wide",
        tool_name="compute_metric",
        columns=[*dimensions, metric],
        rows=[["a", "b", 1.0]],
        row_count=1,
        parameters={"metric": metric, "dimensions": dimensions, "filters": []},
    )


def proportions_test(group_column: str = "first_delivery_status") -> ResultSnapshot:
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
                "group_column": group_column,
                "value_column": "is_repeat",
                "groups": ["late", "on_time"],
            },
            "filters": [],
        },
        statistical_result=StatisticalResult(
            test_name="two-proportion z-test",
            statistic=-23.5,
            p_value=2.15e-122,
            sample_sizes={"late": 3401, "on_time": 26599},
            effect_size=-0.41,
            effect_size_name="Cohen's h",
        ),
    )


# ------------------------------------------------- the shipping question

#: What the planner declared for "do shipping delays appear to affect
#: repeat purchasing?", verified against a real run and not invented.
SHIPPING = {
    "analysis_type": "correlation",
    "metrics": ("repeat_purchase_rate", "late_delivery_rate", "avg_delivery_days"),
    "dimensions": (),
}


#: And the five results it actually produced.
def shipping_results() -> list[ResultSnapshot]:
    return [
        series("repeat_purchase_rate"),
        series("late_delivery_rate"),
        breakdown("repeat_purchase_rate", "acquisition_channel"),
        breakdown("repeat_purchase_rate", "region"),
        proportions_test(),
    ]


class TestARelationshipQuestionGetsItsTest:
    def test_the_test_is_chosen_over_every_breakdown(self) -> None:
        chosen = relevance.best(
            shipping_results(),
            analysis_type=SHIPPING["analysis_type"],
            target_metrics=SHIPPING["metrics"],
            target_dimensions=SHIPPING["dimensions"],
        )
        assert chosen is not None
        assert chosen.tool_name == "statistical_test"

    def test_the_breakdown_that_shipped_is_not_chosen(self) -> None:
        """`repeat_purchase_rate by acquisition_channel` was the headline."""
        chosen = relevance.best(
            shipping_results(),
            analysis_type=SHIPPING["analysis_type"],
            target_metrics=SHIPPING["metrics"],
            target_dimensions=SHIPPING["dimensions"],
        )
        assert relevance.dimensions_of(chosen) != ("acquisition_channel",)

    def test_a_test_is_reachable_at_all(self) -> None:
        """It was not. It carries no `metric`, and the old selector required one."""
        assert relevance.metric_of(proportions_test()) == ""
        ranked = relevance.rank(
            shipping_results(),
            analysis_type="correlation",
            target_metrics=SHIPPING["metrics"],
        )
        assert any(s.tool_name == "statistical_test" for s in ranked)

    def test_its_grouping_is_read_from_its_own_parameters(self) -> None:
        """A test has no `dimension`; it names the cut in `variables`."""
        assert relevance.grouping_of(proportions_test()) == "first_delivery_status"
        assert relevance.value_column_of(proportions_test()) == "is_repeat"


class TestTheStrategiesNeverDisagreed:
    """Two runs, same question, different tool mixes.

    The deterministic run produced no `compute_metric` and the AI run did.
    `compute_metric` sorted first in the old tool-name preference, so the
    two picked unrelated results and the report presented that as a
    divergence of planning. Both had computed the delay analysis.
    """

    def test_both_tool_mixes_select_the_same_answer(self) -> None:
        deterministic = shipping_results()
        ai = [*shipping_results(), wide("repeat_purchase_rate", ["customer_segment", "region"])]

        picks = [
            relevance.best(
                results,
                analysis_type=SHIPPING["analysis_type"],
                target_metrics=SHIPPING["metrics"],
                target_dimensions=SHIPPING["dimensions"],
            )
            for results in (deterministic, ai)
        ]
        assert picks[0] is not None and picks[1] is not None
        assert picks[0].tool_name == picks[1].tool_name == "statistical_test"

    def test_the_extra_tool_no_longer_changes_the_answer(self) -> None:
        """`compute_metric` appearing is not a reason to answer differently."""
        without = relevance.best(
            shipping_results(), analysis_type="correlation", target_metrics=SHIPPING["metrics"]
        )
        with_extra = relevance.best(
            [*shipping_results(), wide("repeat_purchase_rate", ["customer_segment", "region"])],
            analysis_type="correlation",
            target_metrics=SHIPPING["metrics"],
        )
        assert without is not None and with_extra is not None
        assert without.result_id == with_extra.result_id


class TestABreakdownQuestionKeepsItsBreakdown:
    """The other direction, which a first attempt broke.

    Penalising any dimension the question's *metrics* did not name demoted
    `customer_segment` on a question that had declared it, and
    "which customer segment has the highest return rate?" became
    "the analysis produced a result that could not be summarised".
    """

    SEGMENTATION = {
        "analysis_type": "segmentation",
        "metrics": ("return_rate",),
        "dimensions": ("customer_segment",),
    }

    def results(self) -> list[ResultSnapshot]:
        return [
            series("return_rate"),
            breakdown("return_rate", "customer_segment"),
            proportions_test("customer_segment"),
        ]

    def test_the_declared_cut_wins(self) -> None:
        chosen = relevance.best(
            self.results(),
            analysis_type=self.SEGMENTATION["analysis_type"],
            target_metrics=self.SEGMENTATION["metrics"],
            target_dimensions=self.SEGMENTATION["dimensions"],
        )
        assert chosen is not None
        assert chosen.tool_name == "compare_segments"
        assert relevance.dimensions_of(chosen) == ("customer_segment",)

    def test_a_test_does_not_outrank_it(self) -> None:
        """A test is the answer to a relationship question and not to this one."""
        chosen = relevance.best(
            self.results(),
            analysis_type="segmentation",
            target_metrics=self.SEGMENTATION["metrics"],
            target_dimensions=self.SEGMENTATION["dimensions"],
        )
        assert chosen is not None and chosen.statistical_result is None

    def test_an_undeclared_cut_is_demoted_when_one_was_declared(self) -> None:
        chosen = relevance.best(
            [
                breakdown("return_rate", "acquisition_channel"),
                breakdown("return_rate", "customer_segment"),
            ],
            analysis_type="segmentation",
            target_metrics=("return_rate",),
            target_dimensions=("customer_segment",),
        )
        assert relevance.dimensions_of(chosen) == ("customer_segment",)

    def test_but_not_when_the_question_declared_none(self) -> None:
        """The planner chose the cut because the question named none.

        Penalising it there would punish the planner for answering. The
        shipping question declares `dimensions: []`, so its breakdowns must
        not be demoted *for having a dimension at all*.

        Asserted as a comparison, not as "the score is positive". A first
        version checked only that the score stayed above zero, and the
        penalty still left it there, so the mutation that reintroduced the
        defect passed. The claim is relative: a breakdown of the subject
        metric must rank at or above a bare series of the same metric, and
        that is what breaks when the penalty is unconditional.
        """
        cut = breakdown("repeat_purchase_rate", "acquisition_channel", rid="res_cut")
        bare = series("repeat_purchase_rate", rid="res_bare")
        ranked = relevance.rank(
            [bare, cut],
            analysis_type="correlation",
            target_metrics=("repeat_purchase_rate",),
            target_dimensions=(),
        )
        assert ranked[0].result_id == "res_cut", (
            "a breakdown was demoted for having a dimension on a question that declared none"
        )

    def test_and_the_penalty_does_apply_once_a_cut_is_named(self) -> None:
        """The other side of the same rule, so neither can drift alone."""
        wrong = breakdown("return_rate", "acquisition_channel", rid="res_wrong")
        bare = series("return_rate", rid="res_bare")
        ranked = relevance.rank(
            [wrong, bare],
            analysis_type="segmentation",
            target_metrics=("return_rate",),
            target_dimensions=("customer_segment",),
        )
        assert ranked[0].result_id == "res_bare", (
            "a breakdown by a cut the question did not ask for outranked a "
            "result with no cut at all"
        )


class TestWhatIsNeverSelected:
    def test_a_profile_is_not_an_answer(self) -> None:
        profile = ResultSnapshot(
            result_id="res_profile",
            tool_name="profile_table",
            columns=["column"],
            rows=[["a"]],
            row_count=1,
        )
        assert relevance.best([profile], analysis_type="segmentation") is None

    def test_an_empty_result_cannot_be_one(self) -> None:
        empty = ResultSnapshot(
            result_id="res_empty",
            tool_name="compare_segments",
            columns=["region", "revenue"],
            rows=[],
            row_count=0,
            parameters={"metric": "revenue", "dimension": "region"},
        )
        assert relevance.best([empty], analysis_type="segmentation") is None

    def test_nothing_at_all_is_not_an_error(self) -> None:
        assert relevance.best([], analysis_type="segmentation") is None


class TestAChartBreaksATieAndNeverMore:
    def test_a_charted_result_wins_between_equals(self) -> None:
        plain = breakdown("return_rate", "customer_segment", rid="res_plain")
        same = breakdown("return_rate", "customer_segment", rid="res_charted")
        chosen = relevance.best(
            [plain, same],
            analysis_type="segmentation",
            target_metrics=("return_rate",),
            target_dimensions=("customer_segment",),
            charted=frozenset({"res_charted"}),
        )
        assert chosen is not None and chosen.result_id == "res_charted"

    def test_but_never_outranks_relevance(self) -> None:
        """A charted irrelevance is still an irrelevance.

        This is the term that, left too large, would reintroduce the whole
        defect: the headline was wrong *because* the chosen result happened
        to be presentable.
        """
        charted_wrong = breakdown("return_rate", "acquisition_channel", rid="res_wrong")
        uncharted_right = breakdown("return_rate", "customer_segment", rid="res_right")
        chosen = relevance.best(
            [charted_wrong, uncharted_right],
            analysis_type="segmentation",
            target_metrics=("return_rate",),
            target_dimensions=("customer_segment",),
            charted=frozenset({"res_wrong"}),
        )
        assert chosen is not None and chosen.result_id == "res_right"


def test_the_subject_metric_outranks_a_companion() -> None:
    """The first declared metric is what the question was about."""
    chosen = relevance.best(
        [series("late_delivery_rate"), series("repeat_purchase_rate")],
        analysis_type="timeseries",
        target_metrics=("repeat_purchase_rate", "late_delivery_rate"),
    )
    assert chosen is not None
    assert relevance.metric_of(chosen) == "repeat_purchase_rate"


@pytest.mark.parametrize("kind", sorted(relevance.RELATIONSHIP_TYPES))
def test_every_relationship_type_promotes_a_test(kind: str) -> None:
    chosen = relevance.best(
        shipping_results(), analysis_type=kind, target_metrics=SHIPPING["metrics"]
    )
    assert chosen is not None and chosen.tool_name == "statistical_test"


# ------------------------------------- the cut a question did not name


#: The AI run's own candidate set for the shipping question, reconstructed
#: from its activity trace: fifteen calls, no statistical test, and five
#: breakdowns of the subject metric by five different cuts. The whole
#: defect is visible in this list: one of these is the answer and
#: nothing in the old scoring could tell which.
def ai_shipping_candidates() -> list[ResultSnapshot]:
    return [
        breakdown("repeat_purchase_rate", "customer_segment", rid="res_segment"),
        wide(
            "repeat_purchase_rate",
            ["customer_segment", "acquisition_channel", "region"],
            rid="res_crosstab",
        ),
        breakdown("repeat_purchase_rate", "first_delivery_status", rows=2, rid="res_delay"),
        breakdown("repeat_purchase_rate", "first_carrier", rid="res_carrier"),
        breakdown("repeat_purchase_rate", "acquisition_channel", rows=6, rid="res_channel"),
    ]


def shipping_rank(
    results: list[ResultSnapshot], charted: frozenset[str] = frozenset()
) -> list[ResultSnapshot]:
    return relevance.rank(
        results,
        analysis_type=SHIPPING["analysis_type"],
        target_metrics=SHIPPING["metrics"],
        target_dimensions=SHIPPING["dimensions"],
        charted=charted,
    )


class TestACutIsReadAgainstTheCompanionMetrics:
    """The gap that survived the first fix, and it was exact.

    Where the interpretation declares no dimensions, the dimension term
    contributes nothing either way -- by design, so the planner is not
    punished for choosing a cut the question left open. The consequence
    was that it contributed nothing *to telling the cuts apart*: on the
    shipping question five breakdowns scored within half a point and the
    headline was decided by the chart bonus and the tool tie-break.

    `first_delivery_status` is the delay cut. The signal saying so was
    already declared -- `late_delivery_rate` and `avg_delivery_days` are
    the question's companion metrics and share the word `delivery` with it,
    and was not being read.
    """

    def test_the_delay_cut_is_chosen_over_four_rivals(self) -> None:
        ranked = shipping_rank(ai_shipping_candidates())
        assert ranked[0].result_id == "res_delay"

    def test_it_wins_on_relevance_and_not_on_a_tie_break(self) -> None:
        """The margin is the claim.

        Asserting only the winner would pass again the moment the term is
        removed and the tie-break happens to favour the right result. What
        has to hold is that relevance *separates* them: the gap must be
        larger than everything the tie-breaks can contribute, which is the
        chart bonus plus the whole tool-order range.
        """
        tie_break_range = 0.5 + 0.1 * len(relevance.DEFAULT_TOOL_ORDER)
        scored = [
            relevance.score(
                snapshot,
                analysis_type="correlation",
                target_metrics=SHIPPING["metrics"],
                target_dimensions=(),
                charted=frozenset(),
            )
            for snapshot in ai_shipping_candidates()
        ]
        best, *rest = sorted(scored, reverse=True)
        assert best - max(rest) > tie_break_range

    def test_a_chart_on_a_rival_does_not_take_it_back(self) -> None:
        """The answer-bearing result is allowed to be the unchartable one."""
        ranked = shipping_rank(
            ai_shipping_candidates(),
            charted=frozenset({"res_segment", "res_carrier", "res_channel", "res_crosstab"}),
        )
        assert ranked[0].result_id == "res_delay"

    def test_the_term_reads_the_companions_and_not_every_metric(self) -> None:
        """Asserted on the term, because the total cannot witness it.

        With cuts declared the +3-per-match term is four times this one,
        so a mutation here changes no ordering and a test written against
        `score` passes. Both conditions of this term are pinned directly.
        """
        companions = ("repeat_purchase_rate", "late_delivery_rate", "avg_delivery_days")
        assert (
            relevance.companion_bonus(("first_delivery_status",), companions)
            == relevance.COMPANION_OVERLAP
        )
        # Named after the subject, not a companion: the same quantity twice.
        assert relevance.companion_bonus(("repeat_purchase_band",), companions) == 0.0
        # A single-metric question has no companions and so no signal here.
        assert relevance.companion_bonus(("first_delivery_status",), ("return_rate",)) == 0.0
        # No cut at all.
        assert relevance.companion_bonus((), companions) == 0.0

    def test_the_subject_metric_is_not_a_companion(self) -> None:
        """Only the metrics the question related the subject *to* count.

        A cut named after the metric being measured says nothing about a
        relationship. It is the same quantity twice, so the term reads
        `target_metrics[1:]`, and a single-metric question has no
        companions and gets no bonus from this term at all.
        """
        one_metric = relevance.score(
            breakdown("repeat_purchase_rate", "purchase_region"),
            analysis_type="correlation",
            target_metrics=("repeat_purchase_rate",),
            target_dimensions=(),
            charted=frozenset(),
        )
        plain = relevance.score(
            breakdown("repeat_purchase_rate", "region"),
            analysis_type="correlation",
            target_metrics=("repeat_purchase_rate",),
            target_dimensions=(),
            charted=frozenset(),
        )
        assert one_metric == plain

    def test_a_declared_cut_still_outranks_a_merely_overlapping_one(self) -> None:
        """The companion term sits below the cut the question actually named."""
        ranked = relevance.rank(
            [
                breakdown("repeat_purchase_rate", "first_delivery_status", rid="res_overlap"),
                breakdown("repeat_purchase_rate", "customer_segment", rid="res_declared"),
            ],
            analysis_type="correlation",
            target_metrics=SHIPPING["metrics"],
            target_dimensions=("customer_segment",),
        )
        assert ranked[0].result_id == "res_declared"


class TestStructuralWordsDoNotMatch:
    """A name match on a unit or a schema word is a false signal.

    `late_delivery_rate` is two subject words and a unit. If `rate`
    counted, every `*_rate` column in the file would look like an answer
    to every question with a rate in it, which is the failure mode of
    name matching, and the reason the list is generous rather than minimal.
    """

    @pytest.mark.parametrize(
        "name",
        ["late_delivery_rate", "avg_delivery_days", "total_order_count", "first_carrier_name"],
    )
    def test_no_unit_or_structural_word_survives_tokenising(self, name: str) -> None:
        assert relevance.subject_tokens(name) <= frozenset({"late", "delivery", "order", "carrier"})

    def test_a_shared_unit_alone_earns_nothing(self) -> None:
        """`refund_rate` and `return_rate` share only `rate`."""
        shared_unit = relevance.score(
            breakdown("refund_rate", "delivery_rate_band"),
            analysis_type="correlation",
            target_metrics=("refund_rate", "return_rate"),
            target_dimensions=(),
            charted=frozenset(),
        )
        unrelated = relevance.score(
            breakdown("refund_rate", "store"),
            analysis_type="correlation",
            target_metrics=("refund_rate", "return_rate"),
            target_dimensions=(),
            charted=frozenset(),
        )
        assert shared_unit == unrelated


class TestFewerCutsWhereNoneWasNamed:
    """A 120-row three-way cross-tab was one tie-break from the headline.

    Each further cut narrows the claim and multiplies the groups. On a
    question that named no breakdown, the coarsest result that still says
    something is the one to lead with; a segment-by-channel-by-region
    cross-tab answers a far more specific question that nobody asked.
    """

    def test_the_cross_tab_ranks_last_of_the_candidates(self) -> None:
        ranked = shipping_rank(ai_shipping_candidates())
        assert ranked[-1].result_id == "res_crosstab"

    def test_a_single_cut_outranks_a_cross_tab_of_the_same_metric(self) -> None:
        ranked = shipping_rank(
            [
                wide("repeat_purchase_rate", ["a_region", "b_channel", "c_segment"], rid="res_3"),
                breakdown("repeat_purchase_rate", "store", rid="res_1"),
            ]
        )
        assert ranked[0].result_id == "res_1"

    def test_but_three_declared_cuts_are_not_demoted(self) -> None:
        """A question that asked for all three must get all three.

        Asserted on the term rather than on an ordering, and the reason is
        the finding that made it a term: with cuts declared, the
        +3-per-match and -2-per-mismatch weights are large enough that
        dropping this guard reorders nothing anywhere, so a test written
        against `rank` passes while the guard is gone. Mutating
        `if target_dimensions: return 0.0` now fails here instead.
        """
        declared = ("customer_segment", "acquisition_channel", "region")
        assert relevance.extra_cut_penalty(declared, declared) == 0.0
        # Only one of the three named, and still no demerit for the others:
        # this term is about what the question *asked for*, not about how
        # well the cuts match, because that is the dimension term's business.
        assert relevance.extra_cut_penalty(declared, ("region",)) == 0.0
        # Nothing named: each cut past the first is docked.
        assert relevance.extra_cut_penalty(declared, ()) == relevance.EXTRA_CUT * 2
        assert relevance.extra_cut_penalty(("region",), ()) == 0.0
        assert relevance.extra_cut_penalty((), ()) == 0.0

    def test_the_ordering_still_holds_through_the_aggregate(self) -> None:
        """And the term is wired into `score`, not merely defined."""
        dimensions = ("customer_segment", "acquisition_channel", "region")
        ranked = relevance.rank(
            [
                breakdown("repeat_purchase_rate", "customer_segment", rid="res_1"),
                wide("repeat_purchase_rate", list(dimensions), rid="res_3"),
            ],
            analysis_type="segmentation",
            target_metrics=("repeat_purchase_rate",),
            target_dimensions=dimensions,
        )
        assert ranked[0].result_id == "res_3"
