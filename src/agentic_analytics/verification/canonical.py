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
#: cell keeps the value the query produced -- this changes presentation
#: only, never what was verified.
DISPLAY_PLACES = 2

#: How each operation reads in a sentence. "Sum weekly sales by holiday
#: flag" is not how anyone says it.
_OPERATION_WORD = {"sum": "total", "average": "average", "count": "count of"}


def _format(value: Decimal) -> str:
    """Thousands separators, and no more precision than a reader wants."""
    if value == value.to_integral_value():
        return f"{int(value):,}"
    quantised = round(value, DISPLAY_PLACES)
    if quantised == quantised.to_integral_value():
        return f"{int(quantised):,}"
    return f"{quantised:,f}".rstrip("0").rstrip(".")


def _format_metric(value: Decimal, metric_format: str) -> str:
    """Present registry values without exposing binary floating-point noise."""
    if metric_format == "currency":
        return f"${value:,.2f}"
    if metric_format == "percent":
        return f"{value:,.2f}%"
    return _format(value)


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
    if operation not in _PHRASING:
        return None
    dimension = getattr(mapping, "dimension", None)
    if not dimension and len(snapshot.rows) != 1:
        return None

    # The visitor has to have said what to measure. "How much?" resolves
    # to a count of rows, which the engine can compute and which is not an
    # answer to a question that never named a column -- volunteering one
    # turned four questions the corpus requires be refused into confident
    # answers. A named measure is the difference between answering and
    # guessing what was meant.
    measure_named = getattr(mapping, "measure", None)
    if not measure_named or measure_named not in (getattr(mapping, "named_columns", None) or []):
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
    if target is None:
        return None

    if dimension:
        dimension_column = resolve_result_column(str(dimension), snapshot.columns)
        if dimension_column is None:
            return None
        grouped_entries: list[str] = []
        grouped_cells: list[EvidenceCell] = []
        for row in range(len(snapshot.rows)):
            value = as_number(
                snapshot.cell(row, target), declared_type=snapshot.declared_type(target)
            )
            if value is None:
                return None
            label = str(snapshot.cell(row, dimension_column))
            grouped_entries.append(f"{label}: {_format(value)}")
            grouped_cells.append(
                EvidenceCell(
                    result_id=snapshot.result_id,
                    row=row,
                    column=target,
                    value=snapshot.cell(row, target),
                    label=f"{operation} of {measure} for {label}",
                )
            )
        what = (measure or "rows").replace("_", " ")
        return CandidateFinding(
            text=f"{_OPERATION_WORD.get(operation, operation).capitalize()} {what} "
            f"by {dimension.replace('_', ' ')}: " + "; ".join(grouped_entries) + ".",
            kind="calculated_fact",
            task_id=task_id,
            result_ids=[snapshot.result_id],
            evidence_cells=grouped_cells,
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
