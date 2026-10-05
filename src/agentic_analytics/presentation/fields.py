"""How a column is named and formatted, decided from evidence.

Three of these decisions were being made badly, and each has the same
shape: a guess that is right often enough to look fine and wrong often
enough to publish a falsehood.

* `blue_light_filter_active` held `0` and `1`, and the report printed `0`
  and `1`. The column is a flag and the reader needs "Off" and "On" -- but
  a numeric column holding exactly two values is not therefore a flag. A
  store table with two stores would become "Off" and "On" under a rule that
  only counts distinct values.
* `deep_sleep_pct` is a percentage and reads as one. `net_promoter_score`
  is not, and neither is `pct_id`. A name is suggestive, not sufficient:
  the observed range has to be compatible too.
* "revenue" says nothing about currency. Printing `$` on a column whose
  dataset never claimed dollars invents a fact about someone else's data.

So each rule here requires converging evidence and otherwise declines.
Declining costs a reader a nicer label. Guessing costs them a wrong one,
and they have no way to tell.
"""

from __future__ import annotations

from typing import Any

from agentic_analytics.analytics.labels import column_label
from agentic_analytics.presentation.schemas import DisplayField, SemanticKind

#: Column-name tokens that suggest a percentage. Necessary, never
#: sufficient: the observed values must also sit in a percentage range.
_PERCENT_TOKENS = frozenset({"pct", "percent", "percentage", "rate", "share"})

#: Tokens that suggest a flag. These raise confidence in a boolean reading
#: but do not create one on their own, and their absence does not block it:
#: the hard gate is that the only values present are 0 and 1.
_FLAG_TOKENS = frozenset({"is", "has", "was", "active", "flag", "enabled", "used", "any"})

#: Labels for a 0/1 flag. "Off"/"On" rather than "No"/"Yes" because these
#: are states of a setting, which is what a `_active`/`_enabled` column
#: records, and it reads correctly for `_flag` too.
BOOLEAN_LABELS: dict[str, str] = {"0": "Off", "1": "On", "false": "Off", "true": "On"}

#: Decimal places for a fractional measure, matching the table and the
#: chart axis. One source for all three or they disagree on screen.
MEASURE_PRECISION = 2

_INTEGRAL_TYPES = frozenset({"BIGINT", "INTEGER", "SMALLINT", "TINYINT", "HUGEINT", "UBIGINT"})
_BOOLEAN_TYPES = frozenset({"BOOLEAN", "BOOL"})


def humanize(name: str) -> str:
    """A readable label for a column name.

    An analytical output column gets the name declared for it in
    `analytics/labels.py`: those columns are the engine's own, so what they
    measure is known and can be said. `rate_effect` separated into "rate
    effect" is still the arithmetic naming itself, and that is what reached
    a reader as a chart title.

    Everything else is conservative on purpose. It separates words and
    capitalises the first; it does not expand abbreviations, because `pct`
    becoming "Percentage" on a column that turns out not to be one
    compounds the error, and `Branch_No` becoming "Branch Number" is a
    claim about someone else's naming.
    """
    return column_label(name)


def _tokens(name: str) -> set[str]:
    """Name tokens, so `page_views` does not match on "age".

    Substring matching here read `page_views` as containing "age" and
    classified it as a weak-additive age column. Tokens only.
    """
    return {token for token in name.lower().replace("-", "_").split("_") if token}


def _looks_boolean(field: Any, observed: set[str] | None) -> bool:
    """Whether a column is confidently a two-state flag.

    A declared BOOLEAN always is. A numeric column is only treated as one
    when every value actually present is 0 or 1 -- not merely when there
    are two of them.
    """
    declared = str(getattr(field, "data_type", "") or "").upper()
    if declared in _BOOLEAN_TYPES:
        return True
    if declared not in _INTEGRAL_TYPES:
        return False
    if int(getattr(field, "distinct_count", 0) or 0) != 2:
        return False

    # Prefer the values the result actually returned; fall back to the
    # profile's min/max, which is the only evidence available before a
    # query runs.
    if observed is not None:
        return bool(observed) and observed <= {"0", "1"}
    bounds = {
        str(getattr(field, "min_value", "") or "").strip(),
        str(getattr(field, "max_value", "") or "").strip(),
    }
    return bounds == {"0", "1"}


def _looks_percentage(name: str, field: Any) -> bool:
    """Whether a column is confidently a percentage on a 0-100 scale.

    Requires both a name token and a compatible observed range. A `rate`
    column holding 0.03 is a ratio, not a percentage, and formatting it
    with a `%` would multiply the reader's understanding by a hundred.
    """
    if not (_tokens(name) & _PERCENT_TOKENS):
        return False
    raw_low = getattr(field, "min_value", None)
    raw_high = getattr(field, "max_value", None)
    if raw_low is None or raw_high is None:
        return False
    try:
        low = float(str(raw_low))
        high = float(str(raw_high))
    except (TypeError, ValueError):
        return False
    return 0.0 <= low <= high <= 100.0 and high > 1.0


def display_field_for(
    field: Any,
    *,
    observed_values: set[str] | None = None,
    unit: str | None = None,
) -> DisplayField:
    """One column's presentation metadata.

    `field` is an `InferredField` from upload profiling or anything with
    the same attributes, so a governed metric column can be described the
    same way. `unit` is only ever passed in from a source that actually
    declared one; this function never invents it.
    """
    name = str(getattr(field, "name", "") or "")
    role = str(getattr(field, "role", "") or "")
    boolean = _looks_boolean(field, observed_values)

    if boolean:
        kind = SemanticKind.BOOLEAN
    elif role == "identifier":
        kind = SemanticKind.IDENTIFIER
    elif role == "time":
        kind = SemanticKind.TIME
    elif role == "measure":
        kind = SemanticKind.MEASURE
    else:
        kind = SemanticKind.CATEGORY

    # A numeric column used as a grouping has a real order -- ages 18..65
    # are a sequence, not an unordered set of labels -- and plotting it on
    # a categorical axis is what made a 48-age breakdown unreadable.
    declared = str(getattr(field, "data_type", "") or "").upper()
    numeric = declared in _INTEGRAL_TYPES or declared.startswith(("DECIMAL", "DOUBLE", "FLOAT"))
    ordered = kind is SemanticKind.TIME or (
        numeric and not boolean and kind in (SemanticKind.CATEGORY, SemanticKind.MEASURE)
    )
    if ordered and kind is SemanticKind.CATEGORY:
        kind = SemanticKind.ORDERED_NUMERIC

    resolved_unit = unit
    precision: int | None = None
    if _looks_percentage(name, field):
        resolved_unit = resolved_unit or "%"
        precision = MEASURE_PRECISION
    elif kind in (SemanticKind.MEASURE, SemanticKind.ORDERED_NUMERIC) and not boolean:
        precision = None if declared in _INTEGRAL_TYPES else MEASURE_PRECISION

    return DisplayField(
        source_name=name,
        display_label=humanize(name),
        semantic_kind=kind,
        unit=resolved_unit,
        precision=precision,
        boolean_labels=dict(BOOLEAN_LABELS) if boolean else None,
        ordered=bool(ordered),
        identifier=kind is SemanticKind.IDENTIFIER,
        sensitive=False,
    )


def label_value(value: Any, field: DisplayField | None) -> str:
    """How one cell of a dimension reads on screen.

    A flag's raw value becomes its state. Everything else is shown as it
    was stored, because a dimension value is the visitor's own data and
    rewriting it is not the engine's business.
    """
    if value is None:
        return "—"
    text = str(value).strip()
    if field is not None and field.boolean_labels:
        if isinstance(value, bool):
            return field.boolean_labels.get("true" if value else "false", text)
        # `1.0` and `1` are the same flag.
        key = text
        try:
            key = str(int(float(text)))
        except (TypeError, ValueError):
            key = text.lower()
        return field.boolean_labels.get(key, field.boolean_labels.get(text.lower(), text))
    return text


def fields_by_name(fields: list[DisplayField]) -> dict[str, DisplayField]:
    return {field.source_name: field for field in fields}
