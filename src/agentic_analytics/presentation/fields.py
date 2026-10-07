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

from decimal import Decimal, InvalidOperation
from typing import Any

from agentic_analytics.analytics.labels import (
    Derivation,
    column_label,
    derived_unit,
)
from agentic_analytics.presentation.schemas import DisplayField, SemanticKind
from agentic_analytics.verification.canonical import format_number

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


#: What each derivation looks like on screen. `precision` of 0 means a
#: whole number: a count stored as `4.0` is four things, not 4.00.
_DERIVED_DISPLAY: dict[Derivation, tuple[SemanticKind, int | None, bool]] = {
    Derivation.SAME_AS_MEASURE: (SemanticKind.MEASURE, MEASURE_PRECISION, True),
    Derivation.DELTA_OF_MEASURE: (SemanticKind.MEASURE, MEASURE_PRECISION, True),
    Derivation.PROPORTION: (SemanticKind.MEASURE, MEASURE_PRECISION, True),
    Derivation.PERIOD: (SemanticKind.TIME, None, True),
    Derivation.COUNT: (SemanticKind.COUNT, 0, True),
    Derivation.RANK: (SemanticKind.ORDERED_NUMERIC, 0, True),
    Derivation.PROPORTION_0_1: (SemanticKind.MEASURE, MEASURE_PRECISION, True),
}

#: Derivations whose stored value is not the value a reader sees. Only one,
#: and it is written down rather than special-cased at the point of render.
_SCALE: dict[Derivation, float] = {Derivation.PROPORTION_0_1: 100.0}


def _derived_field(
    field: Any,
    derivation: Derivation,
    measure_unit: str | None,
    time_grain: str | None,
) -> DisplayField:
    """A column the analytics tools computed, described from what it is.

    No evidence from the column's own values is consulted, deliberately.
    `diff_vs_best` on a four-row breakdown holds values from -2.30 to 0,
    and every rule in this module that reads a range would describe that as
    something other than "a difference of two rates". The contract already
    says what it is.
    """
    name = str(getattr(field, "name", "") or "")
    kind, precision, ordered = _DERIVED_DISPLAY[derivation]
    return DisplayField(
        source_name=name,
        display_label=humanize(name),
        semantic_kind=kind,
        unit=derived_unit(derivation, measure_unit),
        precision=precision,
        scale=_SCALE.get(derivation, 1.0),
        ordered=ordered,
        identifier=False,
        sensitive=False,
        time_grain=time_grain if derivation is Derivation.PERIOD else None,
    )


def display_field_for(
    field: Any,
    *,
    observed_values: set[str] | None = None,
    unit: str | None = None,
    scale: float = 1.0,
    derivation: Derivation | None = None,
    measure_unit: str | None = None,
    time_grain: str | None = None,
) -> DisplayField:
    """One column's presentation metadata.

    `field` is an `InferredField` from upload profiling or anything with
    the same attributes, so a governed metric column can be described the
    same way. `unit` is only ever passed in from a source that actually
    declared one; this function never invents it.

    `derivation` says the column is one the analytics tools computed, and
    what it is relative to the measure. That is declared knowledge rather
    than evidence about the column itself, so it wins: `period` has no
    entry in the upload profile -- it did not exist before the query --
    and inferring from what is left made it a CATEGORY, which is how a
    stored instant reached a reader. `measure_unit` is the measure's own
    unit, and a difference of two rates becomes percentage points rather
    than inheriting the `%`.
    """
    if derivation is not None:
        return _derived_field(field, derivation, measure_unit, time_grain)

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
        scale=scale,
        boolean_labels=dict(BOOLEAN_LABELS) if boolean else None,
        ordered=bool(ordered),
        identifier=kind is SemanticKind.IDENTIFIER,
        sensitive=False,
        time_grain=time_grain if kind is SemanticKind.TIME else None,
    )


#: Month abbreviations. Short enough for a chart axis, unambiguous in
#: every locale this product renders.
_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def period_label(value: Any, grain: str | None = None) -> str:
    """A period a reader can read.

    The engine stores periods as ISO timestamps, and a headline that says
    "peaked in 2025-12-01T00:00:00" is showing a reader a serialisation
    format. The midnight suffix carries no information at any grain this
    product aggregates to -- a monthly bucket is a month, not an instant --
    so it is never shown.

    Anything that does not parse is returned unchanged. A period this does
    not understand is still the engine's own value, and guessing at it
    would be worse than printing it.
    """
    text = str(value if value is not None else "").strip()
    if not text:
        return text
    head = text.split("T")[0]
    parts = head.split("-")
    try:
        if len(parts) >= 3:
            year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
        elif len(parts) == 2:
            year, month, day = int(parts[0]), int(parts[1]), 1
        else:
            return text
    except ValueError:
        return text
    if not 1 <= month <= 12:
        return text
    if grain in {"day", "week"}:
        return f"{_MONTHS[month - 1]} {day}, {year}"
    if grain == "year":
        return str(year)
    # Month and quarter both read as a month: the bucket's first day is an
    # implementation detail of how it is stored.
    return f"{_MONTHS[month - 1]} {year}"


#: Units written before the number rather than after it. A currency symbol
#: is a prefix in every locale this product renders, and "65,435.38 $" is
#: not a price anyone writes.
_PREFIX_UNITS = frozenset({"$", "£", "€", "¥"})


def with_unit(text: str, field: DisplayField | None) -> str:
    """A formatted number, carrying the unit its field declares."""
    if field is None or not field.unit:
        return text
    unit = field.unit
    if unit in _PREFIX_UNITS:
        return f"{unit}{text}"
    # `%` sits tight against the number; a named unit -- `pp` -- takes a
    # space, because "0.71pp" reads as a typo and "0.71 pp" reads as a
    # measurement.
    return f"{text}{'' if unit == '%' else ' '}{unit}"


def _number(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError, InvalidOperation):
        return None


def display_value(value: Any, field: DisplayField | None) -> str:
    """**The** string a reader sees for one cell. One function, everywhere.

    The headline, the highlights, the scope line, the table, the chart axis
    and its tooltip, the printed page and the PDF all resolve a cell
    through this. There used to be several: the headline formatted a period
    through `period_label` and said "Oct 2025", while the table beside it
    printed the stored value and said "2025-01-01T00:00:00". The same
    number was written two ways on one screen, and neither surface was
    wrong on its own terms.

    Everything it needs is on the `DisplayField`, which is why that type
    carries the grain and the unit rather than leaving each caller to find
    them. A `None` field means no metadata was derived for the column, and
    the value is then shown as stored -- which is the old behaviour, kept
    for the columns this layer still declines to describe.
    """
    if value is None:
        return "—"

    if field is not None and field.boolean_labels:
        return label_value(value, field)

    if field is not None and field.semantic_kind is SemanticKind.TIME:
        return period_label(value, field.time_grain)

    number = _number(value) if not isinstance(value, bool) else None
    if number is None:
        return label_value(value, field)

    # A 0-1 proportion is written as a percentage. The multiplier is on the
    # field, declared by the derivation, so the browser's formatter applies
    # the same one -- see `web/src/lib/displayValue.ts` and the contract
    # both are held to.
    if field is not None and field.scale != 1.0:
        number = number * Decimal(str(field.scale))

    # A declared precision is a pin, not a hint.
    #
    # Both branches used to fall through to `format_number`, which decides
    # from the *value*: whole numbers lose their decimals so that a figure
    # reads the same in a sentence as in the table beside it. Down a column
    # that rule is not consistency, it is the absence of it. A published
    # breakdown of 48 age groups printed `61.08`, `62.94`, `59.48` and then
    # `59` -- the one group whose mean happened to be 59.0033 -- and a
    # reader has to stop and work out whether that cell is a different kind
    # of number. It is not.
    #
    # So a field that declares how many places it has gets them, every
    # time; a column with nothing declared is still read from its value,
    # which is the honest fallback for a column this layer declined to
    # describe. `precision == 0` is the same pin at zero places: a count
    # stored as `4.0` is four things.
    #
    # Deliberately not pushed down into `format_number`. That function is
    # the verifier's canonicalisation -- it builds the claim text a finding
    # is checked against -- and it is handed a number with no field, so it
    # has no declaration to honour. Widening it would change what every
    # recorded finding says in order to fix a column.
    if field is not None and field.precision == 0:
        text = f"{int(number.to_integral_value()):,}"
    elif field is not None and field.precision:
        places = field.precision
        text = f"{round(number, places):,.{places}f}"
    else:
        text = format_number(number)
    return with_unit(text, field)


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
