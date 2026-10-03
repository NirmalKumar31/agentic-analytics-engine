"""Deterministic answer prose, one template per result shape.

Every sentence here is assembled from figures the engine already computed
and can point at. No model writes any of it, and nothing is phrased in a
way that outruns the analysis: a two-group difference is "descriptive"
because no test was run, and "highest" is only said when the result holds
every group.

The rule that shapes all of these is that a number in prose must be a
number the engine recorded. That is checked, not trusted, by
`numbers_resolve`: each figure must be a cited cell, a recorded coverage
count, or a declared difference recomputed from two cells. The reason it
matters is concrete -- a published sentence once claimed a store was "the
lowest" when it was the tenth of a top-ten list, and both numbers in it
were real cells. Real numbers in a false sentence is the failure mode, so
the words are as constrained as the figures.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from agentic_analytics.analytics.results import EvidenceCell, ResultSnapshot
from agentic_analytics.presentation.fields import fields_by_name, humanize, label_value
from agentic_analytics.presentation.schemas import (
    DisplayField,
    InterpretationLevel,
    PresentationHighlight,
    PresentationScope,
    PresentationShape,
    PresentationValue,
    SemanticKind,
)
from agentic_analytics.verification.canonical import format_number, resolve_result_column
from agentic_analytics.verification.numeric import extract_numbers
from agentic_analytics.verification.typing import as_number

#: Groups that can be named in a sentence. Above this the sentence states
#: the shape of the breakdown and the table carries the groups, because
#: enumerating 45 stores produced a semicolon run that trailed off.
PROSE_GROUPS = 4

#: How close a prose figure must be to a recorded one to count as the same
#: number. Matches the numeric verifier's own tolerance in spirit: display
#: rounding must not make a true sentence unverifiable.
_TOLERANCE = Decimal("0.005")


def measure_column(snapshot: ResultSnapshot, mapping: Any) -> str | None:
    """The aggregate column, found through lineage rather than position.

    An aggregate whose lineage names the contract's measure is preferred.
    Falling back to any aggregate matters because the lineage's `column` is
    the *source* column and a count has none -- resolving only on an exact
    match sent a scalar total to `row_count` and answered "is 10" for a
    revenue question.
    """
    measure = getattr(mapping, "measure", None)
    aggregates: list[str] = []
    for column, origin in (snapshot.column_lineage or {}).items():
        if origin.get("kind") != "aggregate":
            continue
        resolved = resolve_result_column(str(column), snapshot.columns)
        if not resolved:
            continue
        if measure is not None and origin.get("column") in (measure, "*"):
            return resolved
        aggregates.append(resolved)
    if measure is None and aggregates:
        return aggregates[0]
    if aggregates:
        return aggregates[0]
    if "row_count" in snapshot.columns:
        return "row_count"
    return None


def dimension_columns(snapshot: ResultSnapshot, mapping: Any) -> list[str]:
    """The grouping columns present in the result, in contract order."""
    out: list[str] = []
    for dimension in getattr(mapping, "dimensions", ()) or ():
        resolved = resolve_result_column(str(dimension), snapshot.columns)
        if resolved:
            out.append(resolved)
    if not out and "period" in snapshot.columns:
        out.append("period")
    return out


def _values(snapshot: ResultSnapshot, column: str) -> list[tuple[int, Decimal]]:
    """Every row's measured value, as numbers, or an empty list."""
    out: list[tuple[int, Decimal]] = []
    for row in range(len(snapshot.rows)):
        value = as_number(snapshot.cell(row, column), declared_type=snapshot.declared_type(column))
        if value is None:
            return []
        out.append((row, value))
    return out


def detect_shape(
    mapping: Any,
    snapshot: ResultSnapshot,
    display_fields: list[DisplayField],
) -> PresentationShape:
    """What kind of answer this result is.

    Decided from the contract and the result's own columns. Order matters:
    a ranking over a time field is still a ranking, and a boolean flag is
    still a boolean even though it is also a low-cardinality category.
    """
    if snapshot.statistical_result is not None:
        return PresentationShape.STATISTICAL_TEST

    dimensions = dimension_columns(snapshot, mapping)
    if not dimensions:
        return PresentationShape.SCALAR

    if str(getattr(mapping, "operation", "")) == "rank":
        return PresentationShape.RANKING

    if len(dimensions) > 1:
        return PresentationShape.MULTI_DIMENSIONAL

    by_name = fields_by_name(display_fields)
    only = dimensions[0]
    field = by_name.get(only)

    if only == "period" or getattr(mapping, "time_grain", None):
        return PresentationShape.TIME_SERIES
    if field is not None and field.semantic_kind is SemanticKind.TIME:
        return PresentationShape.TIME_SERIES
    if field is not None and field.boolean_labels:
        return PresentationShape.BOOLEAN_COMPARISON
    if field is not None and field.semantic_kind is SemanticKind.ORDERED_NUMERIC:
        return PresentationShape.ORDERED_NUMERIC_SERIES
    return PresentationShape.CATEGORICAL_BREAKDOWN


def _measure_phrase(mapping: Any, snapshot: ResultSnapshot, column: str) -> str:
    """How the aggregated quantity reads, in the visitor's own words.

    Named from the contract's measure, not from the engine's output alias:
    a reader asked about `annual_revenue` and should not be answered about
    `total_annual_revenue_sum`.
    """
    operation = str(getattr(mapping, "operation", "") or "")
    measure = getattr(mapping, "measure", None)
    if column == "row_count" and not measure:
        return "record count"
    subject = humanize(str(measure)).lower() if measure else "rows"
    word = {
        "sum": "total",
        "average": "average",
        "mean": "average",
        "count": "count of",
        "min": "minimum",
        "max": "maximum",
        "median": "median",
        "rank": "total",
        "trend": "total",
    }.get(operation, operation)
    return f"{word} {subject}".strip()


def _with_unit(text: str, field: DisplayField | None) -> str:
    if field is not None and field.unit:
        return f"{text}{'' if field.unit == '%' else ' '}{field.unit}"
    return text


def _value_of(
    snapshot: ResultSnapshot, row: int, column: str, field: DisplayField | None
) -> PresentationValue:
    raw = snapshot.cell(row, column)
    number = as_number(raw, declared_type=snapshot.declared_type(column))
    formatted = format_number(number) if number is not None else str(raw)
    return PresentationValue(
        raw_value=raw,
        formatted_value=_with_unit(formatted, field),
        unit=field.unit if field else None,
    )


def _cell(snapshot: ResultSnapshot, row: int, column: str, label: str) -> EvidenceCell:
    return EvidenceCell(
        result_id=snapshot.result_id,
        row=row,
        column=column,
        value=snapshot.cell(row, column),
        label=label,
    )


def scope_for(
    mapping: Any, snapshot: ResultSnapshot, *, rows_total: int | None = None
) -> PresentationScope:
    """The recorded population, carried through rather than recomputed."""
    coverage = snapshot.group_coverage
    filters = []
    for entry in getattr(mapping, "filters", ()) or ():
        if isinstance(entry, dict):
            column, operator, value = (
                entry.get("column"),
                entry.get("operator"),
                entry.get("value"),
            )
        else:
            column, operator, value = (*list(entry), None, None)[:3]
        if column:
            filters.append(f"{humanize(str(column))} {operator} {value}")

    period = None
    window = getattr(mapping, "period", None)
    if window and len(tuple(window)) == 2:
        start, end = tuple(window)
        period = f"{start} to {end}"

    return PresentationScope(
        rows_total=coverage.rows_total if coverage else rows_total,
        rows_matching=coverage.rows_matching if coverage else None,
        rows_represented=coverage.rows_represented if coverage else None,
        observations_matching=coverage.observations_matching if coverage else None,
        observations_represented=coverage.observations_represented if coverage else None,
        groups_returned=coverage.groups_returned if coverage else None,
        groups_total=coverage.groups_total if coverage else None,
        complete=coverage.complete if coverage else None,
        filters=filters,
        period=period,
        ordering=coverage.ordering if coverage else None,
    )


def numbers_resolve(
    text: str,
    snapshot: ResultSnapshot,
    scope: PresentationScope,
    *,
    derived: list[Decimal] | None = None,
) -> list[float]:
    """Figures in `text` that the engine did not record. Empty is correct.

    A number in a published sentence must be traceable. Three sources
    count, and each is an engine-computed record rather than a guess:

    * a numeric cell of the cited result, or its row count;
    * a recorded coverage count, which is data the engine measured and the
      reason `GroupCoverage` keeps the four meanings of "limit" apart;
    * a declared difference, recomputed here from two cells.

    Anything else is a number nobody can check, which is how a sentence
    with two real cells in it managed to be false.
    """
    allowed: list[Decimal] = [Decimal(snapshot.row_count)]
    for row in snapshot.rows:
        for value in row:
            if isinstance(value, bool):
                continue
            number = as_number(value)
            if number is not None:
                allowed.append(number)
    for count in (
        scope.rows_total,
        scope.rows_matching,
        scope.rows_represented,
        scope.observations_matching,
        scope.observations_represented,
        scope.groups_returned,
        scope.groups_total,
    ):
        if count is not None:
            allowed.append(Decimal(count))
    allowed.extend(derived or [])

    unresolved: list[float] = []
    for stated in extract_numbers(text):
        candidate = Decimal(str(stated))
        if not any(abs(candidate - known) <= _TOLERANCE for known in allowed):
            unresolved.append(stated)
    return unresolved


# --------------------------------------------------------------- templates


def _scalar(
    mapping: Any, snapshot: ResultSnapshot, column: str, by_name: dict[str, DisplayField]
) -> tuple[str, str | None, list[PresentationHighlight]]:
    field = by_name.get(str(getattr(mapping, "measure", "") or ""))
    value = _value_of(snapshot, 0, column, field)
    phrase = _measure_phrase(mapping, snapshot, column)
    headline = f"{phrase.capitalize()} is {value.formatted_value}."

    secondary = None
    if "row_count" in snapshot.columns and column != "row_count":
        rows = as_number(snapshot.cell(0, "row_count"))
        values = (
            as_number(snapshot.cell(0, "value_count"))
            if "value_count" in snapshot.columns
            else rows
        )
        if rows is not None and values is not None:
            secondary = (
                f"Measured from {format_number(values)} non-null values across "
                f"{format_number(rows)} matching records."
                if values != rows
                else f"Measured across {format_number(rows)} matching records."
            )

    highlight = PresentationHighlight(
        highlight_id="scalar",
        label=phrase.capitalize(),
        value=value,
        evidence_cells=[_cell(snapshot, 0, column, phrase)],
    )
    return headline, secondary, [highlight]


def _boolean_comparison(
    mapping: Any,
    snapshot: ResultSnapshot,
    column: str,
    dimension: str,
    by_name: dict[str, DisplayField],
) -> tuple[str, str | None, list[PresentationHighlight], list[Decimal]]:
    field = by_name.get(dimension)
    measure_field = by_name.get(str(getattr(mapping, "measure", "") or ""))
    rows = _values(snapshot, column)
    if len(rows) != 2:
        return ("", None, [], [])

    # Order by the flag's own value so "On" reads second regardless of how
    # the query happened to sort.
    def flag_key(row: int) -> str:
        return str(snapshot.cell(row, dimension))

    ordered = sorted((row for row, _ in rows), key=flag_key)
    low_row, high_row = ordered[0], ordered[1]
    off = _value_of(snapshot, low_row, column, measure_field)
    on = _value_of(snapshot, high_row, column, measure_field)
    off_label = label_value(snapshot.cell(low_row, dimension), field)
    on_label = label_value(snapshot.cell(high_row, dimension), field)

    phrase = _measure_phrase(mapping, snapshot, column)
    name = field.display_label if field else humanize(dimension)
    headline = (
        f"{phrase.capitalize()} is {on.formatted_value} when {name} is "
        f"{on_label}, and {off.formatted_value} when it is {off_label}."
    )

    on_number = as_number(snapshot.cell(high_row, column))
    off_number = as_number(snapshot.cell(low_row, column))
    derived: list[Decimal] = []
    secondary = "No significance test was requested, so this is a descriptive comparison only."
    delta_value = None
    if on_number is not None and off_number is not None:
        difference = abs(on_number - off_number)
        derived.append(difference)
        delta_value = PresentationValue(
            raw_value=float(difference),
            formatted_value=_with_unit(format_number(difference), measure_field),
            unit=measure_field.unit if measure_field else None,
        )
        secondary = (
            f"A descriptive difference of {delta_value.formatted_value}. "
            "No significance test was requested."
        )

    highlight = PresentationHighlight(
        highlight_id="boolean_comparison",
        label=f"{name} {on_label} compared with {off_label}",
        value=on,
        comparison_value=off,
        delta=delta_value,
        evidence_cells=[
            _cell(snapshot, high_row, column, f"{phrase} for {name} {on_label}"),
            _cell(snapshot, low_row, column, f"{phrase} for {name} {off_label}"),
        ],
        interpretation_level=(
            InterpretationLevel.DERIVED if delta_value else InterpretationLevel.MEASURED
        ),
    )
    return headline, secondary, [highlight], derived


def _extremes(
    mapping: Any,
    snapshot: ResultSnapshot,
    column: str,
    dimensions: list[str],
    by_name: dict[str, DisplayField],
    *,
    shape: PresentationShape,
) -> tuple[str, str | None, list[PresentationHighlight]]:
    """The highest and lowest of a breakdown, when that can be claimed.

    "Highest" is a statement about every group, so it is only made when the
    result holds every group. A breakdown cut short says what it has.
    """
    rows = _values(snapshot, column)
    if not rows:
        return ("", None, [])
    measure_field = by_name.get(str(getattr(mapping, "measure", "") or ""))
    phrase = _measure_phrase(mapping, snapshot, column)
    coverage = snapshot.group_coverage
    whole = coverage.complete if coverage is not None else True

    def group_label(row: int) -> str:
        parts = [
            label_value(snapshot.cell(row, dimension), by_name.get(dimension))
            for dimension in dimensions
        ]
        named = [
            f"{by_name[d].display_label} {p}"
            if d in by_name
            and by_name[d].semantic_kind
            in (SemanticKind.ORDERED_NUMERIC, SemanticKind.IDENTIFIER, SemanticKind.BOOLEAN)
            else p
            for d, p in zip(dimensions, parts, strict=False)
        ]
        return " / ".join(named)

    high_row, _high = max(rows, key=lambda pair: pair[1])
    low_row, _low = min(rows, key=lambda pair: pair[1])
    high_value = _value_of(snapshot, high_row, column, measure_field)
    low_value = _value_of(snapshot, low_row, column, measure_field)

    if len(rows) == 1:
        headline = (
            f"{phrase.capitalize()} is {high_value.formatted_value} for {group_label(high_row)}."
        )
    elif whole:
        headline = (
            f"{phrase.capitalize()} is highest for {group_label(high_row)}, at "
            f"{high_value.formatted_value}, and lowest for {group_label(low_row)}, at "
            f"{low_value.formatted_value}."
        )
    else:
        headline = (
            f"The largest group in this result is {group_label(high_row)}, at "
            f"{high_value.formatted_value}, and the smallest is {group_label(low_row)}, at "
            f"{low_value.formatted_value}."
        )

    if shape is PresentationShape.ORDERED_NUMERIC_SERIES:
        # The groups have a numeric order, which is why the chart puts them
        # on a numeric axis -- but an order is not a trend, and nothing here
        # tested for one. Saying so is the difference between describing a
        # breakdown and claiming a direction.
        extent = f"{low_value.formatted_value} to {high_value.formatted_value}"
        secondary = (
            f"Values range from {extent} across every group. The result is "
            "descriptive; no trend or significance test was requested."
        )
    elif whole:
        secondary = "Every group is listed in the table below."
    else:
        secondary = (
            "This result does not carry every group the question asked for; "
            "the groups it does carry are in the table below."
        )

    highlights = [
        PresentationHighlight(
            highlight_id="highest",
            label=f"Highest: {group_label(high_row)}",
            value=high_value,
            evidence_cells=[_cell(snapshot, high_row, column, f"{phrase}, highest")],
        )
    ]
    if low_row != high_row:
        highlights.append(
            PresentationHighlight(
                highlight_id="lowest",
                label=f"Lowest: {group_label(low_row)}",
                value=low_value,
                evidence_cells=[_cell(snapshot, low_row, column, f"{phrase}, lowest")],
            )
        )
    return headline, secondary, highlights


def _small_breakdown(
    mapping: Any,
    snapshot: ResultSnapshot,
    column: str,
    dimensions: list[str],
    by_name: dict[str, DisplayField],
) -> tuple[str, str | None, list[PresentationHighlight]]:
    """Few enough groups to name, without enumerating them as a list."""
    headline, secondary, highlights = _extremes(
        mapping,
        snapshot,
        column,
        dimensions,
        by_name,
        shape=PresentationShape.CATEGORICAL_BREAKDOWN,
    )
    if len(snapshot.rows) <= PROSE_GROUPS:
        secondary = "All groups are in the table below."
    return headline, secondary, highlights


def _ranking(
    mapping: Any,
    snapshot: ResultSnapshot,
    column: str,
    dimensions: list[str],
    by_name: dict[str, DisplayField],
) -> tuple[str, str | None, list[PresentationHighlight]]:
    """Only the end the question asked for.

    A top-ten list says nothing about the minimum. Claiming the tenth row
    was "the lowest" was a published falsehood built from a real cell.
    """
    if not snapshot.rows:
        return ("", None, [])
    measure_field = by_name.get(str(getattr(mapping, "measure", "") or ""))
    ascending = bool(getattr(mapping, "ascending", False))
    extreme = "lowest" if ascending else "highest"
    value = _value_of(snapshot, 0, column, measure_field)
    dimension = dimensions[0] if dimensions else None
    field = by_name.get(dimension) if dimension else None
    raw_label = label_value(snapshot.cell(0, dimension), field) if dimension else ""
    subject = (
        f"{field.display_label} {raw_label}"
        if field and field.semantic_kind in (SemanticKind.ORDERED_NUMERIC, SemanticKind.IDENTIFIER)
        else raw_label
    )
    phrase = _measure_phrase(mapping, snapshot, column)
    headline = f"{subject} has the {extreme} {phrase}, at {value.formatted_value}."
    secondary = (
        f"This is the {extreme} end of the ranking the question asked for. "
        "The table holds the rows that were returned."
    )
    highlight = PresentationHighlight(
        highlight_id=extreme,
        label=f"{extreme.capitalize()}: {subject}",
        value=value,
        evidence_cells=[_cell(snapshot, 0, column, f"{phrase}, {extreme}")],
    )
    return headline, secondary, [highlight]


def _time_series(
    mapping: Any,
    snapshot: ResultSnapshot,
    column: str,
    dimensions: list[str],
    by_name: dict[str, DisplayField],
) -> tuple[str, str | None, list[PresentationHighlight]]:
    rows = _values(snapshot, column)
    if not rows:
        return ("", None, [])
    measure_field = by_name.get(str(getattr(mapping, "measure", "") or ""))
    axis = dimensions[0] if dimensions else "period"
    phrase = _measure_phrase(mapping, snapshot, column)
    coverage = snapshot.group_coverage
    whole = coverage.complete if coverage is not None else True

    high_row, _ = max(rows, key=lambda pair: pair[1])
    low_row, _ = min(rows, key=lambda pair: pair[1])
    high = _value_of(snapshot, high_row, column, measure_field)
    low = _value_of(snapshot, low_row, column, measure_field)
    high_period = str(snapshot.cell(high_row, axis))
    low_period = str(snapshot.cell(low_row, axis))

    if whole:
        headline = (
            f"{phrase.capitalize()} peaked at {high.formatted_value} in {high_period} "
            f"and was lowest at {low.formatted_value} in {low_period}."
        )
        secondary = "The complete series is in the chart and the table below."
    else:
        headline = (
            f"Within the periods returned, {phrase} was highest at {high.formatted_value} "
            f"in {high_period} and lowest at {low.formatted_value} in {low_period}."
        )
        secondary = "This result does not carry every period the question asked for."

    highlights = [
        PresentationHighlight(
            highlight_id="peak",
            label=f"Highest period: {high_period}",
            value=high,
            evidence_cells=[_cell(snapshot, high_row, column, f"{phrase}, highest period")],
        ),
        PresentationHighlight(
            highlight_id="trough",
            label=f"Lowest period: {low_period}",
            value=low,
            evidence_cells=[_cell(snapshot, low_row, column, f"{phrase}, lowest period")],
        ),
    ]
    return headline, secondary, highlights


def summarize(
    mapping: Any,
    snapshot: ResultSnapshot,
    display_fields: list[DisplayField],
    shape: PresentationShape,
) -> tuple[str, str | None, list[PresentationHighlight], list[Decimal]]:
    """Headline, secondary summary, highlights and any derived figures."""
    by_name = fields_by_name(display_fields)
    column = measure_column(snapshot, mapping)
    dimensions = dimension_columns(snapshot, mapping)
    if column is None or not snapshot.rows:
        return ("", None, [], [])

    if shape is PresentationShape.SCALAR:
        headline, secondary, highlights = _scalar(mapping, snapshot, column, by_name)
        return headline, secondary, highlights, []

    if shape is PresentationShape.BOOLEAN_COMPARISON and dimensions:
        return _boolean_comparison(mapping, snapshot, column, dimensions[0], by_name)

    if shape is PresentationShape.RANKING:
        headline, secondary, highlights = _ranking(mapping, snapshot, column, dimensions, by_name)
        return headline, secondary, highlights, []

    if shape is PresentationShape.TIME_SERIES:
        headline, secondary, highlights = _time_series(
            mapping, snapshot, column, dimensions, by_name
        )
        return headline, secondary, highlights, []

    if shape is PresentationShape.CATEGORICAL_BREAKDOWN:
        headline, secondary, highlights = _small_breakdown(
            mapping, snapshot, column, dimensions, by_name
        )
        return headline, secondary, highlights, []

    headline, secondary, highlights = _extremes(
        mapping, snapshot, column, dimensions, by_name, shape=shape
    )
    return headline, secondary, highlights, []
