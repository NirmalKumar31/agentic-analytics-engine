"""Which result answers the question.

A run produces several results. One of them becomes the headline, the chart
and the table; the rest are supporting detail. Choosing badly does not look
like a bug -- the report is well formed, the numbers are right, the labels
are clean -- it just answers a different question than the one asked.

**That is what was happening.** The choice was made by tool name:

    ("compute_metric", "compare_segments", "analyze_timeseries")

first match wins. Asked "do shipping delays appear to affect repeat
purchasing?", a run computed five results, including a two-proportion
z-test showing a 50.5% repeat rate after a late delivery against 70.4%
after an on-time one, p = 2e-122. The headline it published was

    "Repeat purchase rate is highest for social, at 69.81%,
     and lowest for affiliate, at 65.58%."

Acquisition channel. The question was about shipping.

Two consequences followed from the same cause, and both were reported as
separate faults:

* **A statistical test could never be the headline.** It carries no
  `metric` parameter, so the old selector skipped it outright -- and a
  relationship question's answer *is* a statistical test. The presentation
  layer has known how to render one (`PresentationShape.STATISTICAL_TEST`)
  the whole time; nothing ever handed it one.

* **The two planning strategies appeared to disagree.** They did not. One
  run happened to produce a `compute_metric` and the other did not, so the
  tool-name order picked a different result in each, and two unrelated
  headlines were presented as a divergence of opinion. Both strategies had
  computed the delay analysis.

So selection is ranked against what the question declared it was about,
and the tool order survives only as the tie-break. Nothing here reads the
question's raw text: the signals are the planner's own interpretation --
`analysis_type` and `target_metrics` -- and the result's own parameters.
Guessing from wording is how a selector starts answering questions nobody
asked.
"""

from __future__ import annotations

from typing import Any

from agentic_analytics.analytics.results import ResultSnapshot

#: The order to fall back on when relevance cannot separate two results.
#: This was the whole rule; it is now the last word rather than the first.
TOOL_ORDER: dict[str, tuple[str, ...]] = {
    "timeseries": ("analyze_timeseries", "compute_metric", "compare_segments"),
    "segmentation": ("compare_segments", "compute_metric", "analyze_timeseries"),
}
DEFAULT_TOOL_ORDER = ("compute_metric", "compare_segments", "analyze_timeseries")

#: Analysis types whose answer is a relationship between two quantities
#: rather than a breakdown of one. For these a statistical test is not a
#: supporting detail; it is the answer.
RELATIONSHIP_TYPES = frozenset({"correlation", "causal", "driver_analysis"})


def _parameters(snapshot: ResultSnapshot) -> dict[str, Any]:
    return dict(snapshot.parameters or {})


def metric_of(snapshot: ResultSnapshot) -> str:
    return str(_parameters(snapshot).get("metric") or "")


def dimensions_of(snapshot: ResultSnapshot) -> tuple[str, ...]:
    parameters = _parameters(snapshot)
    declared = parameters.get("dimensions")
    if declared is None:
        # `compare_segments` names one cut in the singular.
        single = parameters.get("dimension")
        declared = [single] if single else []
    return tuple(str(d) for d in declared if d)


def grouping_of(snapshot: ResultSnapshot) -> str:
    """What a statistical test grouped by, from its own parameters.

    A test states its grouping in `variables.group_column`; it has no
    `dimension`. Without this the grouping is invisible to the ranking and
    to the headline, which then cannot say what the two groups are.
    """
    variables = _parameters(snapshot).get("variables")
    if isinstance(variables, dict):
        return str(variables.get("group_column") or "")
    return ""


def value_column_of(snapshot: ResultSnapshot) -> str:
    """The indicator a proportion test measured, from its own parameters."""
    variables = _parameters(snapshot).get("variables")
    if isinstance(variables, dict):
        return str(variables.get("value_column") or "")
    return ""


def score(
    snapshot: ResultSnapshot,
    *,
    analysis_type: str,
    target_metrics: tuple[str, ...],
    target_dimensions: tuple[str, ...],
    charted: frozenset[str],
) -> float:
    """How well this result answers the question, from declared signals only.

    Returns a number whose scale means nothing on its own; only the order
    matters. Every term is explained where it is added, because an
    unexplained weight in a ranking function is how a selector drifts.

    The signals are the planner's interpretation -- `metrics`, `dimensions`
    and `analysis_type`, which is what `planner_interpretation` actually
    carries -- and the result's own parameters. Nothing reads the question's
    wording.
    """
    total = 0.0
    tool = snapshot.tool_name
    metric = metric_of(snapshot)
    dimensions = dimensions_of(snapshot)
    relationship = analysis_type in RELATIONSHIP_TYPES

    if snapshot.statistical_result is not None:
        # A test is a statement about a difference or an association, which
        # is the shape of a relationship question and nothing else. Large
        # enough to be decisive there, and zero everywhere else: on a
        # segmentation question the breakdown is the answer and a test
        # beside it is supporting evidence.
        total += 10.0 if relationship else 0.0
        # Its grouping counts the way a dimension does, below.
        grouping = grouping_of(snapshot)
        dimensions = (grouping,) if grouping else dimensions
        # The indicator it measured stands in for its metric.
        metric = metric or value_column_of(snapshot)

    if metric and metric in target_metrics:
        total += 4.0
        # The first metric is the subject of the question; the rest are
        # companions the analyst added. Answering about the subject is
        # worth more than answering about a companion.
        if metric == target_metrics[0]:
            total += 1.0

    # A result relating two of the question's metrics is closer to a
    # relationship question than one reporting a single metric. Read from
    # the result's own columns, so a joined result counts.
    if relationship and sum(1 for t in target_metrics if t in snapshot.columns) >= 2:
        total += 3.0

    for dimension in dimensions:
        if not dimension:
            continue
        if dimension in target_dimensions:
            # The cut the question asked for.
            total += 3.0
        elif target_dimensions:
            # A cut the question named a *different* one of. This is the
            # term that demotes "repeat purchase rate by acquisition
            # channel" when the question asked about customer segment: the
            # breakdown is real and correct and it is not what was asked.
            #
            # Only when the question declared dimensions at all. Where it
            # declared none -- "do shipping delays affect repeat
            # purchasing?" names no breakdown -- the planner chose the cut,
            # and penalising it would punish the planner for answering.
            total -= 2.0

    # Presentable beats unpresentable, all else equal: a headline whose
    # result has a chart reads as one answer rather than as a number with
    # an empty frame beside it. Small, so it never outranks relevance.
    if snapshot.result_id in charted:
        total += 0.5

    # The tie-break, and only the tie-break.
    order = TOOL_ORDER.get(analysis_type, DEFAULT_TOOL_ORDER)
    if tool in order:
        total += (len(order) - order.index(tool)) * 0.1

    return total


def rank(
    results: dict[str, ResultSnapshot] | list[ResultSnapshot],
    *,
    analysis_type: str | None,
    target_metrics: tuple[str, ...] = (),
    target_dimensions: tuple[str, ...] = (),
    charted: frozenset[str] = frozenset(),
) -> list[ResultSnapshot]:
    """Every presentable result, best answer first.

    `profile_table` and anything with no rows is dropped: a profile is not
    an answer and an empty result cannot be one.
    """
    candidates = list(results.values() if isinstance(results, dict) else results)
    presentable = [
        snapshot
        for snapshot in candidates
        if snapshot.tool_name != "profile_table" and snapshot.rows
    ]
    kind = str(analysis_type or "")
    return sorted(
        presentable,
        key=lambda snapshot: (
            -score(
                snapshot,
                analysis_type=kind,
                target_metrics=target_metrics,
                target_dimensions=target_dimensions,
                charted=charted,
            )
        ),
    )


def best(
    results: dict[str, ResultSnapshot] | list[ResultSnapshot],
    *,
    analysis_type: str | None,
    target_metrics: tuple[str, ...] = (),
    target_dimensions: tuple[str, ...] = (),
    charted: frozenset[str] = frozenset(),
) -> ResultSnapshot | None:
    ranked = rank(
        results,
        analysis_type=analysis_type,
        target_metrics=target_metrics,
        target_dimensions=target_dimensions,
        charted=charted,
    )
    return ranked[0] if ranked else None
