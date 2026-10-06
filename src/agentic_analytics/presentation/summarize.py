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

import math
import re
from decimal import Decimal
from typing import Any

from agentic_analytics.analytics.results import EvidenceCell, ResultSnapshot
from agentic_analytics.presentation.fields import (
    display_value,
    fields_by_name,
    humanize,
    label_value,
    period_label,
    with_unit,
)
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
    # Lineage is empty on a metric-layer result.
    #
    # `compute_metric`, `compare_segments` and `analyze_timeseries` return
    # the metric as a named column and record no `column_lineage`, so the
    # loop above finds no aggregate and every governed-warehouse answer
    # resolved to no measure at all -- which made `detect_shape` call it a
    # failure and the report say "could not be summarised as an answer".
    #
    # The metric's own name is the lineage in that case: the registry
    # resolved it, and the result carries a column called exactly that. It
    # is a last resort rather than a first guess, so the upload path, where
    # lineage exists and is authoritative, is untouched.
    if measure is not None:
        named = resolve_result_column(str(measure), snapshot.columns)
        if named:
            return named
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


# --------------------------------------------------- a thinly populated extreme
#
# "Repeat purchase rate is highest for vip / affiliate / Midwest, at 100%."
#
# That was published, and every part of it was arithmetically true. The
# cell held 1.0 and the group behind it held two customers. A 120-cell
# cross-tab divides a population until some of its cells hold almost
# nothing, and the extremes of such a result are exactly the cells where
# that has happened -- a superlative *selects* for the thinnest cell,
# because a small denominator is what makes 100% reachable at all.
#
# What is published is the number with its population beside it. The group
# is not dropped and the figure is not changed: it is the maximum, and a
# presentation layer that quietly answered with the second-highest group
# would be substituting a result the engine did not compute. The defect
# was never the number. It was stating the number alone.

#: Rows a named extreme needs behind it before its figure is quoted
#: without comment.
#:
#: A convention, not a derivation, and it is written down here so it can
#: be argued with: thirty is the usual floor for treating a sample
#: proportion or mean as a stable estimate. It decides **only when the
#: population is said out loud** -- never which group is named, never what
#: the figure is -- so getting it wrong costs a sentence a reader did not
#: need, in the direction of saying more rather than less.
THIN_GROUP_ROWS = 30


def group_population(snapshot: ResultSnapshot, row: int) -> int | None:
    """Rows behind one group, from the result's own `row_count` column.

    `None` when the result does not carry one, which is not the same as
    zero: a result that never counted its rows cannot be said to have
    counted few.
    """
    if "row_count" not in snapshot.columns:
        return None
    value = as_number(snapshot.cell(row, "row_count"))
    return None if value is None else int(value)


def named_extreme_rows(
    snapshot: ResultSnapshot,
    column: str,
    shape: PresentationShape,
    *,
    ascending: bool = False,
) -> list[tuple[str, int]]:
    """The rows the headline is about to name, with the words for each.

    Shape-aware, and it has to be. A first version took the maximum and
    the minimum for every shape and so described, on a ranking, the
    population of a group the headline does not mention -- `_ranking`
    reports one end *because* claiming the other was a published
    falsehood built from a real cell, and a note about it would have
    reintroduced that by the back door.

    Empty for the shapes that name no group extreme at all: a statistical
    test states its own sample sizes, and a scalar has one population
    which the scope line already gives.
    """
    if shape in (PresentationShape.STATISTICAL_TEST, PresentationShape.SCALAR):
        return []
    rows = _values(snapshot, column)
    if not rows:
        return []

    if shape is PresentationShape.RANKING:
        # Row 0 is the end the question asked for; the result is ordered.
        return [(f"the {'lowest' if ascending else 'highest'} group", 0)]

    high_row, _ = max(rows, key=lambda pair: pair[1])
    low_row, _ = min(rows, key=lambda pair: pair[1])
    if shape is PresentationShape.TIME_SERIES:
        if high_row == low_row:
            return [("this period", high_row)]
        return [("the peak period", high_row), ("the lowest period", low_row)]
    if high_row == low_row:
        # One group, so neither "highest" nor "lowest" is a claim that was
        # made: there was no comparison.
        return [("this group", high_row)]
    return [("the highest group", high_row), ("the lowest group", low_row)]


def thin_of(snapshot: ResultSnapshot, named: list[tuple[str, int]]) -> list[tuple[str, int, int]]:
    """Those of `named` whose own populations are below the floor.

    Returns `(role, row, population)`. Empty is the ordinary case and
    means every group the answer names rests on enough rows to quote
    without comment.
    """
    out: list[tuple[str, int, int]] = []
    for role, row in named:
        population = group_population(snapshot, row)
        if population is not None and population < THIN_GROUP_ROWS:
            out.append((role, row, population))
    return out


def thin_extreme_note(snapshot: ResultSnapshot, thin: list[tuple[str, int, int]]) -> str:
    """The sentence that puts a thin group's population beside its figure.

    Empty when there is nothing to say. No semicolons: the presentation
    contract rejects a second one, because a semicolon run repeating a
    grouped table is one of the two prose failures it exists to stop, and
    this sentence is appended to a summary that may already carry one.
    """
    if not thin:
        return ""
    coverage = snapshot.group_coverage
    total = coverage.rows_matching if coverage is not None else None

    def quantity(population: int) -> str:
        word = "row" if population == 1 else "rows"
        if total is not None:
            return f"{population:,} of {total:,} matching {word}"
        return f"{population:,} {word}"

    if len(thin) == 1:
        role, _row, population = thin[0]
        return (
            f"{role.capitalize()} covers {quantity(population)}, too few for "
            "that figure to be a reliable estimate."
        )
    first, second = thin[0], thin[1]
    return (
        f"{first[0].capitalize()} covers {quantity(first[2])} and "
        f"{second[0]} covers {quantity(second[2])}, too few for either "
        "figure to be a reliable estimate."
    )


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


def _statistical(
    mapping: Any,
    snapshot: ResultSnapshot,
    column: str,
    dimensions: list[str],
    by_name: dict[str, DisplayField],
) -> tuple[str, str | None, list[PresentationHighlight]]:
    """A test, as the answer to the question that asked for it.

    This branch did not exist. `detect_shape` has returned
    `STATISTICAL_TEST` for a long time and nothing ever selected a test to
    present, so the code path was unreachable -- see `graph/relevance.py`
    for how a relationship question ended up headlined with an unrelated
    breakdown instead.

    **The headline states the association and the secondary line refuses
    the causal reading.** Not a caveat a reader may or may not reach: the
    sentence that would be wrong is the one a reader takes away, so the
    correction travels with it. The engine already withholds a *finding*
    that asserts causation; this is the same rule applied to the headline.
    """
    stats = snapshot.statistical_result
    field = by_name.get(column)
    dimension = dimensions[0] if dimensions else "group"

    # Every group the test measured, in the order the result holds them.
    groups = [
        (
            label_value(snapshot.cell(row, dimension), by_name.get(dimension)),
            _value_of(snapshot, row, column, field),
        )
        for row in range(len(snapshot.rows))
    ]
    if not groups or stats is None:
        return ("", None, [])

    subject = str(getattr(mapping, "subject", "") or "")
    phrase = humanize(subject).lower() if subject else _measure_phrase(mapping, snapshot, column)
    stated = " and ".join(f"{value.formatted_value} for {name}" for name, value in groups)
    headline = f"{phrase[0].upper()}{phrase[1:]} is {stated}."

    # What the test found, in the words a reader can act on, and what it
    # does not establish. `p_value_adjusted` is what the publication gate
    # reads when a family of tests was corrected, so it is what is quoted.
    adjusted = getattr(stats, "p_value_adjusted", None)
    p_value = adjusted if isinstance(adjusted, float) else stats.p_value
    observations = sum(int(n) for n in (stats.sample_sizes or {}).values())
    grouping = str(getattr(mapping, "grouping", "") or dimension)
    secondary = (
        f"A {stats.test_name} across {observations:,} observations, grouped by "
        f"{humanize(grouping).lower()}, puts the difference at "
        f"{format_p_value(p_value)}. That is an association, not a cause: the "
        "groups were not assigned at random, so something else may explain both."
    )

    group_label = by_name[dimension].display_label if dimension in by_name else "Group"
    highlights = [
        PresentationHighlight(
            highlight_id=f"group_{index}",
            label=f"{group_label}: {name}",
            value=value,
            evidence_cells=[_cell(snapshot, index, column, f"{phrase} for {name}")],
        )
        for index, (name, value) in enumerate(groups)
    ]
    return headline, secondary, highlights


def format_p_value(p: float) -> str:
    """A p-value a reader can act on.

    Below a thousandth it is written as a bound rather than as
    `2.15e-122`: the exponent is precision about how unlikely chance is,
    and no decision turns on the difference between that and `< 0.001`.
    """
    try:
        value = float(p)
    except (TypeError, ValueError):
        return "an unreported p-value"
    # `nan < 0.001` is False, so a non-finite value fell straight through to
    # the format string and published "p = nan". A reader cannot act on
    # that, and `readerQuality` counts `NaN` as a value that never arrived
    # -- correctly.
    if not math.isfinite(value):
        return "an unreported p-value"
    if value < 0.001:
        return "p < 0.001"
    return f"p = {value:.3f}"


def _value_of(
    snapshot: ResultSnapshot, row: int, column: str, field: DisplayField | None
) -> PresentationValue:
    """One cell, as stored and as written.

    The written form comes from `display_value` -- the same function the
    table and the chart resolve a cell through -- so a figure cannot be
    worded one way in the headline and another in the table beneath it.
    """
    raw = snapshot.cell(row, column)
    return PresentationValue(
        raw_value=raw,
        formatted_value=display_value(raw, field),
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


#: How an operator reads in a sentence. A reader is being told what was
#: excluded, not shown a predicate.
_OPERATORS = {
    "=": "is",
    "==": "is",
    "eq": "is",
    "!=": "is not",
    "ne": "is not",
    ">": "is over",
    "gt": "is over",
    ">=": "is at least",
    "gte": "is at least",
    "<": "is under",
    "lt": "is under",
    "<=": "is at most",
    "lte": "is at most",
    "in": "is one of",
    "not_in": "is none of",
    "between": "",
    "range": "",
}


def date_range_label(start: Any, end: Any) -> str:
    """A date window a reader can read.

    `Jan 1 - Dec 31, 2025` within one year, and both years when it spans
    two. The endpoints go through `period_label` at day grain, so a stored
    `2025-01-01T00:00:00` is never one of them.
    """
    first = period_label(start, "day")
    last = period_label(end, "day")
    if not first or not last:
        return " to ".join(part for part in (first, last) if part)
    # `Jan 1, 2025` and `Dec 31, 2025` share a year: say it once.
    head, _, year = first.rpartition(", ")
    tail_head, _, tail_year = last.rpartition(", ")
    if year and year == tail_year and head and tail_head:
        return f"{head} - {tail_head}, {year}"
    return f"{first} - {last}"


def _filter_parts(entry: Any) -> tuple[str, str, Any]:
    """Column, operator and value, from either shape the contract uses.

    Filters arrive as dictionaries from a governed contract and as tuples
    from a mapping built in code. The tuple branch used to unpack into
    three names with `(*list(entry), None, None)[:3]`, and the dictionary
    branch read an `operator` key that a range filter does not carry -- so
    a date window published the literal string
    ``Order date None ['2025-01-01', '2025-12-31']`` as the scope line
    under the headline. Both the `None` and the Python list repr were being
    shown to a reader.
    """
    if hasattr(entry, "as_dict") and callable(entry.as_dict):
        # A resolved `Filter`. Its own serialisation names the operator,
        # including the ones it computes from `negated`.
        try:
            entry = entry.as_dict()
        except Exception:  # pragma: no cover - a filter that cannot describe itself
            return "", "", None

    if isinstance(entry, dict):
        column = str(entry.get("column") or entry.get("field") or "")
        operator = str(entry.get("operator") or entry.get("op") or entry.get("comparison") or "")
        value = entry.get("value", entry.get("values"))
        if not operator and isinstance(value, list | tuple) and len(tuple(value)) == 2:
            operator = "between"
        return column, operator, value

    parts = tuple(entry) if isinstance(entry, list | tuple) else (entry,)
    column = str(parts[0]) if parts else ""
    if len(parts) >= 3:
        return column, str(parts[1]), parts[2]
    if len(parts) == 2:
        value = parts[1]
        operator = "between" if isinstance(value, list | tuple) and len(tuple(value)) == 2 else "is"
        return column, operator, value
    return column, "", None


def describe_filter(entry: Any) -> str:
    """One filter, in words, with its endpoints formatted.

    Returns `""` for an entry with no column, which is a filter this layer
    cannot describe; the caller drops it rather than publishing a sentence
    about nothing. A dropped filter is still in the accepted contract in
    the evidence drawer, where the raw predicate belongs.
    """
    # A resolved `Filter` already writes its own sentence, and that sentence
    # is part of its contract -- `row_filters.py` keeps one per kind so the
    # provenance and the prose cannot drift. Preferred over anything derived
    # here.
    #
    # Without this, the object fell through to the "not a dict, not a tuple"
    # branch, `str(entry)` became the column name, and the scope line under
    # a refused run published
    # `CategoryFilter(column='region', value='Atlantis', negated=False, ...)`
    # -- a dataclass repr, as prose, to a reader.
    own = getattr(entry, "describe", None)
    if callable(own):
        try:
            described = str(own()).strip()
        except Exception:  # pragma: no cover - a filter that cannot describe itself
            described = ""
        if described:
            return described[0].upper() + described[1:]

    column, operator, value = _filter_parts(entry)
    if not column:
        return ""

    label = humanize(column)
    word = _OPERATORS.get(operator.lower().strip(), operator.strip())

    if isinstance(value, list | tuple):
        items = list(value)
        if operator.lower().strip() in {"between", "range"} and len(items) == 2:
            return f"{label}: {date_range_label(items[0], items[1])}"
        written = ", ".join(_filter_value(item) for item in items)
        return f"{label} {word} {written}".strip() if word else f"{label}: {written}"

    if value is None:
        # Nothing to compare against. "Order date is" is not a sentence, so
        # the predicate is named without one rather than printed with a
        # `None` where the value should be.
        return f"{label} {word}".strip() if word else label

    return f"{label} {word} {_filter_value(value)}".strip()


def _filter_value(value: Any) -> str:
    """One filter endpoint. A stored instant is written as a date."""
    text = str(value).strip()
    if _LOOKS_ISO.match(text):
        return period_label(text, "day")
    return text


#: A stored date or instant, which a filter endpoint often is.
_LOOKS_ISO = re.compile(r"^\d{4}-\d{2}(-\d{2})?([T ].*)?$")


def scope_for(
    mapping: Any, snapshot: ResultSnapshot, *, rows_total: int | None = None
) -> PresentationScope:
    """The recorded population, carried through rather than recomputed."""
    coverage = snapshot.group_coverage
    filters = [
        described
        for entry in (getattr(mapping, "filters", ()) or ())
        if (described := describe_filter(entry))
    ]

    period = None
    window = getattr(mapping, "period", None)
    if window and len(tuple(window)) == 2:
        start, end = tuple(window)
        period = f"{date_range_label(start, end)}"

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
    fields: list[DisplayField] | None = None,
) -> list[float]:
    """Figures in `text` that the engine did not record. Empty is correct.

    A number in a published sentence must be traceable. Four sources
    count, and each is an engine-computed record rather than a guess:

    * a numeric cell of the cited result, or its row count;
    * a recorded coverage count, which is data the engine measured and the
      reason `GroupCoverage` keeps the four meanings of "limit" apart;
    * a declared difference, recomputed here from two cells;
    * a cell restated in the unit its column declares. A proportion stored
      as `0.5045` and written as `50.46%` is the same figure in the unit
      the presentation declared for it, and the scale is on the field where
      both formatters can see it. Only a *declared* scale counts, so this
      admits no number the contract did not say was the same one.
    * a figure of the cited result's own `statistical_result` -- the
      statistic, the p-value, the effect size, the group sizes and their
      total. These are **in** the result; they are simply not in its rows,
      and this function used to read only the rows. The first attempt at a
      statistical headline was refused for stating the 30,000 observations
      the test itself reported, which is the guard working correctly on an
      incomplete list of sources rather than on a false sentence.

    Anything else is a number nobody can check, which is how a sentence
    with two real cells in it managed to be false.
    """
    scales: dict[int, Decimal] = {}
    for field in fields or []:
        if field.scale != 1.0 and field.source_name in snapshot.columns:
            scales[snapshot.columns.index(field.source_name)] = Decimal(str(field.scale))

    allowed: list[Decimal] = [Decimal(snapshot.row_count)]
    for row in snapshot.rows:
        for index, value in enumerate(row):
            if isinstance(value, bool):
                continue
            number = as_number(value)
            if number is None:
                continue
            allowed.append(number)
            scale = scales.get(index)
            if scale is not None:
                allowed.append(number * scale)
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
    stats = snapshot.statistical_result
    if stats is not None:
        sizes = [int(n) for n in (stats.sample_sizes or {}).values()]
        for figure in (
            stats.statistic,
            stats.p_value,
            getattr(stats, "p_value_adjusted", None),
            stats.effect_size,
            stats.confidence_level,
            *sizes,
            sum(sizes) if sizes else None,
        ):
            if figure is None:
                continue
            number = as_number(figure)
            if number is not None:
                allowed.append(number)
        if stats.confidence_interval:
            for bound in stats.confidence_interval:
                number = as_number(bound)
                if number is not None:
                    allowed.append(number)

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
            formatted_value=with_unit(format_number(difference), measure_field),
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
    grain = str(getattr(mapping, "time_grain", "") or "") or None
    high_period = period_label(snapshot.cell(high_row, axis), grain)
    low_period = period_label(snapshot.cell(low_row, axis), grain)

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
    """Headline, secondary summary, highlights and any derived figures.

    One exit, and it is deliberate. Each shape builds its own sentences and
    then the thin-group note is appended here, because appending it inside
    the builders put it where a later line could take it away:
    `_small_breakdown` calls `_extremes` and then *overwrites* the summary
    with "All groups are in the table below." -- so on a breakdown of four
    groups or fewer, which is exactly where a thin group is most likely,
    the note was computed, returned, and silently discarded. Found by a
    test asserting the sentence rather than the function.
    """
    by_name = fields_by_name(display_fields)
    column = measure_column(snapshot, mapping)
    dimensions = dimension_columns(snapshot, mapping)
    if column is None or not snapshot.rows:
        return ("", None, [], [])

    derived: list[Decimal] = []

    if shape is PresentationShape.STATISTICAL_TEST:
        headline, secondary, highlights = _statistical(
            mapping, snapshot, column, dimensions, by_name
        )
    elif shape is PresentationShape.SCALAR:
        headline, secondary, highlights = _scalar(mapping, snapshot, column, by_name)
    elif shape is PresentationShape.BOOLEAN_COMPARISON and dimensions:
        headline, secondary, highlights, derived = _boolean_comparison(
            mapping, snapshot, column, dimensions[0], by_name
        )
    elif shape is PresentationShape.RANKING:
        headline, secondary, highlights = _ranking(mapping, snapshot, column, dimensions, by_name)
    elif shape is PresentationShape.TIME_SERIES:
        headline, secondary, highlights = _time_series(
            mapping, snapshot, column, dimensions, by_name
        )
    elif shape is PresentationShape.CATEGORICAL_BREAKDOWN:
        headline, secondary, highlights = _small_breakdown(
            mapping, snapshot, column, dimensions, by_name
        )
    else:
        headline, secondary, highlights = _extremes(
            mapping, snapshot, column, dimensions, by_name, shape=shape
        )

    # A superlative selects for the thinnest cell, so the population of a
    # group the answer names goes with it. Appended rather than replacing
    # anything: the reader still needs to know whether the breakdown was
    # complete, and now also what the extreme rests on.
    note = thin_extreme_note(snapshot, thin_named(mapping, snapshot, column, shape))
    if note:
        secondary = f"{secondary} {note}" if secondary else note

    return headline, secondary, highlights, derived


def thin_named(
    mapping: Any,
    snapshot: ResultSnapshot,
    column: str,
    shape: PresentationShape,
) -> list[tuple[str, int, int]]:
    """The thinly populated groups this shape's answer names.

    The one definition, so the sentence in the summary and the caveat in
    `build._caveats` cannot describe different groups -- which they would,
    on an ascending ranking, if each worked it out for itself.
    """
    return thin_of(
        snapshot,
        named_extreme_rows(
            snapshot, column, shape, ascending=bool(getattr(mapping, "ascending", False))
        ),
    )
