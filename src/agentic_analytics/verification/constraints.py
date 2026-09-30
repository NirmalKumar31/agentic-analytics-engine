"""Did the result that a claim rests on actually honour the question?

Support and relevance are both about the *claim*: whether its numbers
trace to a cited cell, and whether it addresses what was asked. Neither
asks the prior question -- whether the query that produced those cells
computed the thing the question described.

That gap is how a released build published four regional averages over
1,200 rows for a question about people aged 30 to 40. Every number was
real, every cell resolved, and the critic had no basis to object: the
claim faithfully reported a result that had quietly answered a different
question.

So the executed result is compared against the accepted contract before
anything publishes. The contract says an operation, a measure, a
grouping, a period and a set of row restrictions; the result records what
it was actually run with. A component the question fixed and the result
omitted is a refusal with a specific reason, not a judgement call, and
not something a model is asked to agree with -- an unfiltered result must
be incapable of publishing for a filtered question however confident a
critic is that the sentence is true.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Stable rejection rules. Distinct from each other so a report can say
#: which part of the question went missing rather than "unsupported".
MISSING_FILTER = "missing_required_filter"
MISSING_DIMENSION = "missing_required_dimension"
WRONG_MEASURE = "wrong_measure"
WRONG_OPERATION = "wrong_operation"
MISSING_PERIOD = "missing_required_period"


@dataclass(frozen=True)
class ConstraintVerdict:
    """Whether the cited results preserved the question's components."""

    #: False when there is no contract to check against -- the governed
    #: warehouse, or a question that was never resolved. The caller must
    #: not read `preserved` in that case.
    applicable: bool
    preserved: bool = True
    rule: str = ""
    reason: str = ""
    #: What the contract required, for provenance and for the report.
    required: dict[str, Any] = field(default_factory=dict)


def _params(snapshot: Any) -> dict[str, Any]:
    return dict(getattr(snapshot, "parameters", None) or {})


def _key(applied: dict[str, Any]) -> tuple[str, str, str]:
    """One filter, as a comparable key.

    Numbers are compared numerically so `30` and `30.0` are one
    restriction; everything else is compared as text. Coercing
    unconditionally is what crashed on a category value.
    """
    raw = applied.get("value")
    if isinstance(raw, bool) or raw is None:
        rendered = "" if raw is None else str(raw)
    elif isinstance(raw, int | float):
        rendered = repr(float(raw))
    else:
        try:
            rendered = repr(float(str(raw)))
        except (TypeError, ValueError):
            rendered = str(raw)
    return str(applied.get("column")), str(applied.get("operator")), rendered


def _describe(filters: list[dict[str, Any]]) -> str:
    words = {">=": "at least", "<=": "at most", ">": "over", "<": "under", "=": "exactly"}
    return ", ".join(
        f"{f.get('column', '?').replace('_', ' ')} "
        f"{words.get(str(f.get('operator')), str(f.get('operator')))} "
        f"{'' if f.get('value') is None else f.get('value')}".strip()
        for f in filters
    )


def check_constraints(finding: Any, mapping: Any, snapshots: list[Any]) -> ConstraintVerdict:
    """Compare the accepted contract with what the cited results ran.

    A claim may cite several results. The question is answered if *any*
    one of them carried the whole contract: a finding resting partly on a
    profile and partly on the filtered aggregate is still grounded in the
    filtered aggregate.
    """
    del finding  # reserved: the claim's own wording is judged elsewhere
    if mapping is None or not getattr(mapping, "confident", False):
        return ConstraintVerdict(applicable=False)

    required_filters = [
        f.as_dict() if hasattr(f, "as_dict") else dict(f)
        for f in (getattr(mapping, "filters", None) or ())
    ]
    measure = getattr(mapping, "measure", None)
    dimensions = list(getattr(mapping, "dimensions", ()) or ())
    if not dimensions and getattr(mapping, "dimension", None):
        dimensions = [mapping.dimension]
    period = getattr(mapping, "period", None)
    operation = getattr(mapping, "operation", "")

    required = {
        "operation": operation,
        "measure": measure,
        "dimension": dimensions[0] if len(dimensions) == 1 else None,
        "dimensions": dimensions,
        "period": list(period) if period else None,
        "filters": required_filters,
    }
    if not (required_filters or dimensions or period):
        # Nothing the question fixed can have gone missing.
        return ConstraintVerdict(applicable=False, required=required)

    if not snapshots:
        return ConstraintVerdict(
            applicable=True,
            preserved=False,
            rule=MISSING_FILTER if required_filters else MISSING_DIMENSION,
            reason="the claim cites no result that recorded how it was computed",
            required=required,
        )

    best: ConstraintVerdict | None = None
    for snapshot in snapshots:
        params = _params(snapshot)
        applied = list(params.get("filters") or [])
        verdict = _compare(
            params, applied, required, required_filters, measure, dimensions, period
        )
        if verdict is None:
            return ConstraintVerdict(applicable=True, preserved=True, required=required)
        if best is None:
            best = verdict
    assert best is not None
    return best


def _compare(
    params: dict[str, Any],
    applied: list[dict[str, Any]],
    required: dict[str, Any],
    required_filters: list[dict[str, Any]],
    measure: Any,
    dimensions: list[str],
    period: Any,
) -> ConstraintVerdict | None:
    """`None` when this result carried the whole contract."""
    if required_filters:
        # Compared by type, not by coercion. A categorical filter's value
        # is text -- `float("G5")` raised and took the whole verification
        # down with it, which the expanded corpus found immediately.
        wanted = {_key(f) for f in required_filters}
        have = {_key(f) for f in applied if f.get("column") is not None}
        if not wanted <= have:
            return ConstraintVerdict(
                applicable=True,
                preserved=False,
                rule=MISSING_FILTER,
                reason=(
                    "the question restricts the rows to "
                    f"{_describe(required_filters)}, and this result was computed "
                    "without that restriction"
                ),
                required=required,
            )
    applied_dimensions = list(params.get("dimensions") or [])
    if not applied_dimensions and params.get("dimension"):
        applied_dimensions = [str(params["dimension"])]
    if dimensions and applied_dimensions != dimensions:
        return ConstraintVerdict(
            applicable=True,
            preserved=False,
            rule=MISSING_DIMENSION,
            reason=(
                "the question asks for a breakdown by "
                f"{', '.join(item.replace('_', ' ') for item in dimensions)}, "
                "and this result is not grouped by the same columns"
            ),
            required=required,
        )
    if measure and params.get("measure") not in (None, measure):
        return ConstraintVerdict(
            applicable=True,
            preserved=False,
            rule=WRONG_MEASURE,
            reason=(
                f"the question asks about {str(measure).replace('_', ' ')}, and this "
                f"result measures {str(params.get('measure')).replace('_', ' ')}"
            ),
            required=required,
        )
    if period and params.get("period") not in (None, list(period)):
        return ConstraintVerdict(
            applicable=True,
            preserved=False,
            rule=MISSING_PERIOD,
            reason="the question names a period this result was not restricted to",
            required=required,
        )
    return None
