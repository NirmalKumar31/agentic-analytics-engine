"""The engine's own answer, written from the query it ran.

A model proposed the right number and the engine threw it away. Asked for
the total net value, a worker claimed "the sum of total_net_value is
176851.26 across 4000 rows" -- both cells resolving exactly -- and the
critic withheld it as only partially supported, reasoning that
`total_net_value` might be read as a source column when it is the alias
of `SUM(net_value)`. The doubt was reasonable. The wording created it.

So for a question the engine mapped confidently, the engine writes the
answer itself. It knows the operation, the source column and the cell the
number came from, so it can say "the total net value is 176,851.26 across
4,000 rows" -- naming the column of the visitor's file rather than the
engine's internal alias, and leaving no ambiguity for anyone to resolve.

This claim is not a model's opinion and is not checked by one. It is
built from the executed result, and it is still put through the
deterministic gates: its cells must resolve, its arithmetic must check,
and it must match the resolved intent. What it cannot be is discarded
because a model dislikes a column name the engine chose.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from agentic_analytics.analytics.results import EvidenceCell, ResultSnapshot
from agentic_analytics.verification.typing import as_number

#: How each operation reads in a sentence.
#: Groups enumerated in the answer sentence. The rest are in the cited
#: result, which the report renders in full beside the answer.
ENUMERATED_GROUPS = 12
#: Groups that read as a sentence. Above this, enumerating them produced a
#: semicolon run that trailed off in "and further groups in the cited
#: result": a paragraph that repeated the table badly and said nothing the
#: table did not already say. Beyond this the answer describes the
#: breakdown's shape and leaves the enumeration to the table.
PROSE_GROUPS = 4

_PHRASING = {
    "sum": "The total {what} is {value}",
    "average": "The average {what} is {value}",
    "count": "There are {value} {what}",
}


def resolve_result_column(name: str, columns: list[str]) -> str | None:
    """The column a requested name is actually returned as, or ``None``.

    The compiler aliases output columns to lowercase slugs, so a grouping
    by `Holiday_Flag` arrives as `holiday_flag`. Comparing the requested
    name literally meant this function bailed out and the complete
    grouped answer was never composed -- the report fell back to a model
    summary naming only the highest and lowest group.

    Returns the column as the result spells it, because that is what
    `cell()` has to be given.
    """
    if name in columns:
        return name
    from agentic_analytics.analytics.upload_plan import alias_for

    wanted = {name.lower(), alias_for(name)}
    for column in columns:
        if column.lower() in wanted:
            return column
    return None


#: Decimal places a published figure is displayed to.
#:
#: The engine rounds aggregates to four inside SQL, and printing all four
#: put `1,122,887.8924` in a sentence about money. Two is what a reader
#: expects and what the independently computed figure uses. The evidence
#: cell keeps the value the query produced. This changes presentation
#: only, never what was verified.
DISPLAY_PLACES = 2

#: How each operation reads in a sentence. "Sum weekly sales by holiday
#: flag" is not how anyone says it.
_OPERATION_WORD = {"sum": "total", "average": "average", "count": "count of"}


def format_number(value: Decimal) -> str:
    """Thousands separators, at the precision the table uses.

    The same figure has to read identically in the sentence and in the
    table beside it. Stripping trailing zeros printed a revenue total as
    `12,296,516.7` in the answer and `12,296,516.70` in the table, which a
    reader has to stop and reconcile. A whole number carries no decimals in
    either place; a fractional one carries two in both.
    """
    if value == value.to_integral_value():
        return f"{int(value):,}"
    quantised = round(value, DISPLAY_PLACES)
    if quantised == quantised.to_integral_value():
        return f"{int(quantised):,}"
    return f"{quantised:,.{DISPLAY_PLACES}f}"


#: The presentation layer formats the same figures for the same report, so
#: it shares this implementation rather than keeping a second one that can
#: drift. The private alias keeps the existing call sites in this module.
_format = format_number


def _format_metric(value: Decimal, metric_format: str) -> str:
    """Present registry values without exposing binary floating-point noise."""
    if metric_format == "currency":
        return f"${value:,.2f}"
    if metric_format == "percent":
        return f"{value:,.2f}%"
    return _format(value)


def _rank_answer(mapping: Any, snapshot: ResultSnapshot, task_id: str | None) -> Any | None:
    """The engine's own sentence for a ranking, claiming only what it holds.

    A ranking returns a short list, so it supports a statement about the
    extreme the question asked for and nothing about the other end. The
    model's summary said "20 has the highest total_weekly_sales at
    301,397,792 and 39 the lowest at 207,445,542" -- and 39 was the tenth
    highest of a ten-row result, while the actual lowest store was 33 at
    37,160,221.96. Both numbers were real cells, so numeric verification
    passed; the word "lowest" was the falsehood.
    """
    from agentic_analytics.agents.schemas import CandidateFinding

    measure = getattr(mapping, "measure", None)
    dimension = getattr(mapping, "dimension", None)
    named = getattr(mapping, "named_columns", None) or []
    if not measure or not dimension or measure not in named or not snapshot.rows:
        return None
    dimension_column = resolve_result_column(str(dimension), snapshot.columns)
    target = next(
        (
            column
            for column, origin in (snapshot.column_lineage or {}).items()
            if origin.get("kind") == "aggregate" and origin.get("column") == measure
        ),
        None,
    )
    target = resolve_result_column(str(target), snapshot.columns) if target else None
    if dimension_column is None or target is None:
        return None

    value = as_number(snapshot.cell(0, target), declared_type=snapshot.declared_type(target))
    if value is None:
        return None
    label = str(snapshot.cell(0, dimension_column))
    ascending = bool(getattr(mapping, "ascending", False))
    extreme = "lowest" if ascending else "highest"
    what = measure.replace("_", " ")
    # The group first, then its figure. "The highest total weekly revenue by
    # branch no is 45 at 12,296,516.70" reads as though 45 were the answer;
    # the branch and the revenue are both numbers and the sentence gave the
    # reader no way to tell which was which.
    subject = f"{dimension.replace('_', ' ')} {label}" if label.isdigit() else label
    return CandidateFinding(
        text=(f"{subject.capitalize()} has the {extreme} total {what}, at {_format(value)}."),
        kind="calculated_fact",
        task_id=task_id,
        result_ids=[snapshot.result_id],
        evidence_cells=[
            EvidenceCell(
                result_id=snapshot.result_id,
                row=0,
                column=target,
                value=snapshot.cell(0, target),
                label=f"{extreme} total of {measure}, at {label}",
            )
        ],
    )


def _group_label(snapshot: ResultSnapshot, row: int, columns: list[str], names: list[str]) -> str:
    """How one group is named in prose.

    A bare key reads badly on its own: a store table grouped by `Branch_No`
    produced "1: 222,402,808.85", where the leading "1" looks like a list
    marker rather than the store it identifies. A numeric key is therefore
    introduced by what it is a key of.
    """
    parts: list[str] = []
    for column, name in zip(columns, names, strict=False):
        value = snapshot.cell(row, column)
        text = str(value)
        # `bool` is caught by the same branch deliberately: a 0/1 flag needs
        # naming for exactly the same reason a store number does.
        if isinstance(value, bool | int | float | Decimal):
            text = f"{name.replace('_', ' ')} {text}"
        parts.append(text)
    return " / ".join(parts)


def _trend_answer(mapping: Any, snapshot: ResultSnapshot, task_id: str | None) -> Any | None:
    """The engine's own sentence for a time series.

    A trend had no canonical answer, so the only candidate was a model's
    prose -- and on a 33-month series the relevance gate withheld it as not
    answering the question. The engine had computed a correct monthly
    series and published nothing at all.

    Composed from the result's own periods and values, like a breakdown,
    which is what a trend is once the axis is the grouping.
    """
    from agentic_analytics.agents.schemas import CandidateFinding

    measure = getattr(mapping, "measure", None)
    named = getattr(mapping, "named_columns", None) or []
    if not measure or measure not in named:
        return None
    if "period" not in snapshot.columns or not snapshot.rows:
        return None
    target = next(
        (
            column
            for column, origin in (snapshot.column_lineage or {}).items()
            if origin.get("kind") == "aggregate" and origin.get("column") == measure
        ),
        None,
    )
    target = resolve_result_column(str(target), snapshot.columns) if target else None
    if target is None:
        return None

    shown = min(len(snapshot.rows), ENUMERATED_GROUPS)
    entries: list[str] = []
    cells: list[EvidenceCell] = []
    for row in range(shown):
        value = as_number(snapshot.cell(row, target), declared_type=snapshot.declared_type(target))
        if value is None:
            return None
        label = str(snapshot.cell(row, "period"))
        entries.append(f"{label}: {_format(value)}")
        cells.append(
            EvidenceCell(
                result_id=snapshot.result_id,
                row=row,
                column=target,
                value=snapshot.cell(row, target),
                label=f"monthly total of {measure} for {label}",
            )
        )
    tail = "." if shown == len(snapshot.rows) else "; and further periods in the cited result."
    what = measure.replace("_", " ")
    return CandidateFinding(
        text=f"Monthly total {what} by month: " + "; ".join(entries) + tail,
        kind="calculated_fact",
        task_id=task_id,
        result_ids=[snapshot.result_id],
        evidence_cells=cells,
    )


def _population_is_empty(snapshot: ResultSnapshot) -> bool:
    """Whether the restrictions left no rows at all.

    Read from the engine's own counts: the coverage block when present,
    otherwise the result's `row_count` column. A zero here is the
    difference between a measured total and an empty population, and only
    one of those is an answer.
    """
    coverage = snapshot.group_coverage
    if coverage is not None and coverage.rows_matching == 0:
        return True
    if not snapshot.rows:
        # A grouped aggregate with no groups. A scalar always returns one
        # row, so this only fires for a breakdown.
        return bool(snapshot.group_coverage) or "row_count" in snapshot.columns
    if "row_count" in snapshot.columns and len(snapshot.rows) == 1:
        total = as_number(
            snapshot.cell(0, "row_count"), declared_type=snapshot.declared_type("row_count")
        )
        return total == 0
    return False


def canonical_answer(
    mapping: Any, snapshot: ResultSnapshot, task_id: str | None = None
) -> Any | None:
    """The direct answer to a confidently mapped question, or ``None``.

    Only for a single-figure question -- no grouping, one row. A breakdown
    has no one sentence that is *the* answer, and inventing one would mean
    choosing which group to lead with, which is a judgement the engine has
    no basis for.
    """
    from agentic_analytics.agents.schemas import CandidateFinding

    if mapping is None or not getattr(mapping, "confident", False):
        return None
    if _population_is_empty(snapshot):
        # No row passed the restrictions. "The total revenue is 0" reads as
        # a measurement of an empty set rather than as "nothing matched",
        # and a reader cannot tell the two apart. The report says nothing
        # was answerable instead, which is what happened.
        return None
    canonical: Any = getattr(mapping, "canonical_dict", lambda: {})()
    if isinstance(canonical, dict) and canonical.get("kind") == "metric_registry":
        if snapshot.tool_name != "compute_metric":
            return None
        metric = getattr(mapping, "metric", None)
        dimensions = list(getattr(mapping, "dimensions", ()) or ())
        metric_column = resolve_result_column(str(metric), snapshot.columns) if metric else None
        if not metric or metric_column is None:
            return None
        metric = metric_column
        resolved_dimensions = [
            resolve_result_column(str(d), snapshot.columns) or d for d in dimensions
        ]
        if any(d not in snapshot.columns for d in resolved_dimensions):
            # A follow-up or an unrelated task may have computed the same
            # metric without the required grouping.  It is not a direct
            # answer, and it must not be allowed to crash verification.
            return None
        metric_format = str(getattr(mapping, "metric_format", "number"))
        entries: list[str] = []
        cells: list[EvidenceCell] = []
        for row_index, _row in enumerate(snapshot.rows):
            value_cell = snapshot.cell(row_index, metric)
            value = as_number(value_cell, declared_type=snapshot.declared_type(metric))
            if value is None:
                return None
            label = " / ".join(
                str(snapshot.cell(row_index, dimension) or "") for dimension in resolved_dimensions
            )
            if not label:
                label = metric.replace("_", " ")
            entries.append(f"{label}: {_format_metric(value, metric_format)}")
            cells.append(
                EvidenceCell(
                    result_id=snapshot.result_id,
                    row=row_index,
                    column=metric,
                    value=value_cell,
                    label=f"{metric.replace('_', ' ')} for {label}",
                )
            )
        if not entries:
            return None
        what = metric.replace("_", " ")
        by = " by " + " and ".join(d.replace("_", " ") for d in dimensions) if dimensions else ""
        return CandidateFinding(
            text=f"{what.capitalize()}{by}: " + "; ".join(entries) + ".",
            kind="calculated_fact",
            task_id=task_id,
            result_ids=[snapshot.result_id],
            evidence_cells=cells,
        )
    operation = getattr(mapping, "operation", "")
    if operation == "trend":
        return _trend_answer(mapping, snapshot, task_id)
    if operation == "rank":
        return _rank_answer(mapping, snapshot, task_id)
    if operation not in _PHRASING:
        return None
    dimensions = [str(d) for d in (getattr(mapping, "dimensions", ()) or ())]
    if not dimensions and getattr(mapping, "dimension", None):
        dimensions = [str(mapping.dimension)]
    if not dimensions and len(snapshot.rows) != 1:
        return None

    # The visitor has to have said what to measure. "How much?" resolves
    # to a count of rows, which the engine can compute and which is not an
    # answer to a question that never named a column, because volunteering one
    # turned four questions the corpus requires be refused into confident
    # answers. A named measure is the difference between answering and
    # guessing what was meant.
    measure_named = getattr(mapping, "measure", None)
    # A count is the exception, but only a *grouped* count. The count is
    # the value, so there is no measure to name, and two corpus datasets
    # have no trustworthy numeric column and answer only this way.
    #
    # A bare count is not covered, because the comment above is right: "how
    # many tickets are there" never says what a ticket is, and answering it
    # with a row count is the guess the corpus requires be refused. My
    # first version of this exception omitted the grouping requirement and
    # turned exactly that case into a confident answer.
    counting = (
        operation == "count" and measure_named is None and bool(getattr(mapping, "dimensions", ()))
    )
    if not counting and (
        not measure_named or measure_named not in (getattr(mapping, "named_columns", None) or [])
    ):
        return None

    # The output column this operation produced, found through lineage so
    # the alias never has to be guessed at.
    measure = getattr(mapping, "measure", None)
    target = None
    for column, origin in (snapshot.column_lineage or {}).items():
        if origin.get("kind") == "aggregate" and origin.get("column") == (measure or "*"):
            target = column
            break
    target = resolve_result_column(str(target), snapshot.columns) if target else None
    if target is None and counting and "row_count" in snapshot.columns:
        # A count has no measure column: the count *is* the value. Two
        # corpus datasets with no trustworthy numeric column answer only
        # this way, and requiring a named measure left them publishing
        # nothing once the fast path stopped asking a model to restate it.
        target = "row_count"
    if target is None:
        return None

    if dimensions:
        # Every requested cut, in order. Reading the singular projection
        # here meant a two-cut answer had no dimension at all, fell through
        # to the scalar path, failed its one-row check and published
        # nothing: a correct two-dimensional result with an empty report.
        columns = [resolve_result_column(str(d), snapshot.columns) for d in dimensions]
        if any(column is None for column in columns):
            return None
        dimension_columns = [str(column) for column in columns]
        grouped_entries: list[str] = []
        grouped_cells: list[EvidenceCell] = []
        # A breakdown with many groups belongs in the cited result, not in
        # one sentence. Enumerating 45 stores produced a 400-character
        # claim that was then cut mid-number, so the engine's own complete
        # answer failed numeric verification and a two-group summary was
        # published instead.
        #
        # Every numeral in the text must be a value the cited result holds,
        # so the closing clause deliberately carries no count: how many
        # groups there are, and how many rows they cover, is reported from
        # `group_coverage` as data rather than asserted here as prose.
        names = [str(d) for d in dimensions]
        values: list[tuple[int, Decimal]] = []
        for row in range(len(snapshot.rows)):
            measured = as_number(
                snapshot.cell(row, target), declared_type=snapshot.declared_type(target)
            )
            if measured is None:
                return None
            values.append((row, measured))
        if not values:
            return None

        what = (measure or "rows").replace("_", " ")
        subject = (
            f"{_OPERATION_WORD.get(operation, operation).capitalize()} {what} "
            f"by {' and '.join(name.replace('_', ' ') for name in names)}"
        )

        def cell_for(row: int) -> EvidenceCell:
            return EvidenceCell(
                result_id=snapshot.result_id,
                row=row,
                column=target,
                value=snapshot.cell(row, target),
                label=(
                    f"{operation} of {measure} for "
                    f"{_group_label(snapshot, row, dimension_columns, names)}"
                ),
            )

        if len(values) <= PROSE_GROUPS:
            # Few enough to read as a sentence.
            for row, value in values:
                label = _group_label(snapshot, row, dimension_columns, names)
                grouped_entries.append(f"{label}: {_format(value)}")
                grouped_cells.append(cell_for(row))
            return CandidateFinding(
                text=f"{subject}: " + "; ".join(grouped_entries) + ".",
                kind="calculated_fact",
                task_id=task_id,
                result_ids=[snapshot.result_id],
                evidence_cells=grouped_cells,
            )

        # Too many to enumerate. Describe the shape of the breakdown and
        # leave the groups to the table beside it, which carries all of
        # them. The extremes are the two figures a reader looks for first
        # and they are the two the table makes hardest to find.
        #
        # "Highest" and "lowest" are claims about every group, so they are
        # only made when the result holds every group. A breakdown cut
        # short describes what it has instead, and never implies it is the
        # whole picture, the same overclaim that once reported the tenth
        # of a top-ten list as the minimum.
        coverage = snapshot.group_coverage
        whole = coverage.complete if coverage is not None else True
        highest_row, highest = max(values, key=lambda pair: pair[1])
        lowest_row, lowest = min(values, key=lambda pair: pair[1])
        top_label = _group_label(snapshot, highest_row, dimension_columns, names)
        bottom_label = _group_label(snapshot, lowest_row, dimension_columns, names)

        if whole:
            text = (
                f"{subject} is highest for {top_label}, at {_format(highest)}, "
                f"and lowest for {bottom_label}, at {_format(lowest)}. "
                "Every group is listed in the result below."
            )
        else:
            text = (
                f"{subject}: the largest group in this result is {top_label}, "
                f"at {_format(highest)}, and the smallest is {bottom_label}, "
                f"at {_format(lowest)}. This result does not carry every group "
                "the question asked for; the groups it does carry are listed below."
            )
        return CandidateFinding(
            text=text,
            kind="calculated_fact",
            task_id=task_id,
            result_ids=[snapshot.result_id],
            evidence_cells=[cell_for(highest_row), cell_for(lowest_row)],
        )

    value = as_number(snapshot.cell(0, target), declared_type=snapshot.declared_type(target))
    if value is None:
        return None

    # Say what the visitor's column is called, not what the engine called
    # its output.
    what = (measure or "rows").replace("_", " ")
    text = _PHRASING[operation].format(what=what, value=_format(value))

    cells = [
        EvidenceCell(
            result_id=snapshot.result_id,
            row=0,
            column=target,
            value=snapshot.cell(0, target),
            label=f"{operation} of {measure}" if measure else "row count",
        )
    ]
    if "row_count" in snapshot.columns and target != "row_count":
        rows = as_number(
            snapshot.cell(0, "row_count"), declared_type=snapshot.declared_type("row_count")
        )
        if rows is not None:
            text += f" across {_format(rows)} rows"
            cells.append(
                EvidenceCell(
                    result_id=snapshot.result_id,
                    row=0,
                    column="row_count",
                    value=snapshot.cell(0, "row_count"),
                    label="contributing rows",
                )
            )

    return CandidateFinding(
        text=text + ".",
        kind="calculated_fact",
        task_id=task_id,
        result_ids=[snapshot.result_id],
        evidence_cells=cells,
    )
