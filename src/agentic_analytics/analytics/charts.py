"""Chart selection as a pure function of the contract and the result.

Chart choice used to be a model call, which is why the same verified data
could produce a chart in one pane, a different encoding in the other, and
none at all in a third run. A chart is a rendering of a result the engine
already computed and verified; nothing about that decision needs judgement,
and a model asked for it introduces variance with no authority.

So: the contract says what was asked for, the snapshot says what came back,
and the pair determines one specification. Identical contracts over
identical results produce identical charts, which is what lets Compare Both
render one shared chart instead of two that disagree.

When no chart would help, that is stated as a reason rather than left as an
empty panel.
"""

from __future__ import annotations

from typing import Any, Literal

from agentic_analytics.analytics.labels import output_label
from agentic_analytics.analytics.results import ResultSnapshot
from agentic_analytics.analytics.semantic import NUMERIC_TYPES

ChartKind = Literal["bar", "line", "grouped_bar", "ranked_bar", "kpi", "none"]

#: The output column a trend's `DATE_TRUNC` lands in. Named because the
#: axis type now depends on it: this one column is a date and is drawn on
#: a time scale, and every other cut is an unordered category.
PERIOD_COLUMN = "period"

#: Categories a bar chart can show before it stops being readable. Above
#: this the table is the honest presentation and the chart is declined.
MAX_BAR_CATEGORIES = 60
#: Distinct values an *ordered numeric* cut can show as bars before the
#: bars stop being the readable form.
#:
#: Above this it is drawn as the sequence it is -- see the single-cut
#: branch of `chart_for`. Twelve is a bar chart's comfortable label budget
#: at a reading column's width, and the number only decides which of two
#: honest drawings is used: it never declines a chart and never changes a
#: value.
MAX_ORDERED_BARS = 12
#: Series in a multi-series line. More lines than this is a tangle.
MAX_SERIES = 8
#: Cells in a grouped/stacked chart, as categories x series.
MAX_GROUPED_CELLS = 240
#: Cuts one specification can separate: one on an axis, one in the colour
#: legend. A result cut more ways than this cannot be drawn here without
#: leaving a cut out, and leaving a cut out draws a different result --
#: see the third branch of `chart_for`.
MAX_CUTS = 2

#: How a period reads on an axis and in a tooltip, per time grain, as a
#: d3-time-format string.
#:
#: Declared here rather than in the presentation layer, which imports it,
#: because the engine's own specification has to carry a format for the
#: path that never reaches a presentation -- and because two copies of this
#: map is how an axis and a tooltip come to disagree about a date.
#:
#: Format strings only, never `labelExpr`: `expr` is on the forbidden-key
#: list in `chartSafety.ts` and in the server's own validator, because a
#: specification that can carry an expression can carry code.
TIME_AXIS_FORMAT: dict[str, str] = {
    "day": "%b %-d, %Y",
    "week": "%b %-d, %Y",
    "month": "%b %Y",
    "quarter": "%b %Y",
    "year": "%Y",
}
#: Monthly, for a period whose grain was not declared. Every period column
#: the engine produces is a `DATE_TRUNC`, so the value is a date whatever
#: the grain; the fallback decides only how much of it is shown.
DEFAULT_TIME_FORMAT = "%b %Y"


def time_format_for(grain: str | None) -> str:
    """The axis format for a time grain, falling back to month."""
    return TIME_AXIS_FORMAT.get(str(grain or ""), DEFAULT_TIME_FORMAT)


def _numeric_column(snapshot: ResultSnapshot, exclude: set[str]) -> str | None:
    """The measure column, found through lineage rather than guessed."""
    for column, origin in (snapshot.column_lineage or {}).items():
        if origin.get("kind") == "aggregate" and column in snapshot.columns:
            return column
    for column in snapshot.columns:
        if column not in exclude and column != "row_count":
            return column
    return None


def _ordered_numeric(snapshot: ResultSnapshot, column: str) -> bool:
    """Whether a cut is a numeric sequence rather than a set of labels.

    Read from the column's **declared type**, never from its values. The
    same rule `presentation/fields.py` uses to reach
    `SemanticKind.ORDERED_NUMERIC`, and the same principle the planner's
    relevance scoring follows: a declared signal is evidence, a guess
    from the data is not. `DECIMAL(10,2)` carries its precision in the
    name, so the parameters come off before the lookup.

    A result written before the engine recorded column types declares
    nothing, which reads as "not known to be ordered" -- the direction
    that leaves the drawing as it was.
    """
    declared = (snapshot.declared_type(column) or "").upper()
    return declared.split("(")[0].strip() in NUMERIC_TYPES


def _label(column: str) -> str:
    """An axis, legend or tooltip title a reader can act on.

    The declared label comes first. `rate_effect` separated into "rate
    effect" is still the arithmetic's name for itself, and a chart axis is
    a primary surface -- the published PDF carried `rate_effect` down its
    y-axis, under a title that said the same thing.

    Falling back to word-separation keeps every other column working and
    claims nothing: an uploaded file's `branch_no` becomes "branch no",
    not "branch number".
    """
    declared = output_label(column)
    if declared is not None:
        return declared
    return column.replace("_", " ")


def _measure_format(snapshot: ResultSnapshot, measure: str) -> str:
    """The d3 number format for a measure, matching how the table shows it.

    A revenue total printed as ``83,373,290.48`` in the table and ``83M`` on
    the axis beside it reads as two different figures. The table shows whole
    numbers without decimals and fractional numbers to two places, so the
    axis is decided the same way, from the values actually returned.
    """
    if measure not in snapshot.columns:  # pragma: no cover - callers check first
        return ","
    position = snapshot.columns.index(measure)
    fractional = False
    for row in snapshot.rows:
        value = row[position] if position < len(row) else None
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, int):
            continue
        if isinstance(value, float) and value != int(value):
            fractional = True
            break
    return ",.2f" if fractional else ","


def _tooltip(
    fields: list[tuple[str, str]],
    measure: str,
    value_format: str,
    *,
    time_format: str | None = None,
) -> list[dict[str, Any]]:
    """Hover detail: the cuts that identify a mark, then its measured value.

    A temporal field gets the same format as its axis. An unformatted
    temporal tooltip is the worst of the three surfaces a date appears on:
    a reader has to ask for it, so whatever it says reads as the precise
    answer -- and what it said was the stored instant.
    """
    entries: list[dict[str, Any]] = []
    for field, kind in fields:
        entry: dict[str, Any] = {"field": field, "type": kind, "title": _label(field)}
        if kind == "temporal" and time_format:
            entry["format"] = time_format
        entries.append(entry)
    entries.append(
        {
            "field": measure,
            "type": "quantitative",
            "title": _label(measure),
            "format": value_format,
        }
    )
    return entries


def chart_for(mapping: Any, snapshot: ResultSnapshot) -> dict[str, Any]:
    """The chart specification for one governed result.

    Returns a dict with ``kind``, ``title``, ``spec`` and, when no chart is
    appropriate, ``no_chart_reason``. The caller turns it into a
    :class:`ChartSpec`; this function stays free of the report machinery so
    it can be tested as the pure function it is.
    """
    if mapping is None or not getattr(mapping, "confident", False):
        return {"kind": "none", "no_chart_reason": "the question was not resolved"}

    operation = str(getattr(mapping, "operation", ""))
    dimensions = [str(d) for d in (getattr(mapping, "dimensions", ()) or ())]
    resolved = [
        column
        for column in snapshot.columns
        if column != "row_count" and column in {_alias_of(d) for d in dimensions} | {"period"}
    ]
    has_period = PERIOD_COLUMN in snapshot.columns
    grain = str(getattr(mapping, "time_grain", "") or "") or None
    measure = _numeric_column(snapshot, exclude=set(resolved) | {PERIOD_COLUMN})
    rows = len(snapshot.rows)

    if measure is None:
        return {"kind": "none", "no_chart_reason": "the result has no measured column to plot"}

    value_format = _measure_format(snapshot, measure)

    if not resolved:
        # A single figure. A one-bar chart adds nothing a number does not.
        return {
            "kind": "kpi",
            "title": f"{_label(measure)}",
            "spec": {"kind": "kpi", "value_column": measure},
        }

    if rows == 0:
        return {"kind": "none", "no_chart_reason": "no rows matched the requested population"}

    # More cuts than one specification can separate.
    #
    # This branch did not exist, and its absence was not an empty panel --
    # it was a wrong chart. A result cut three ways fell through to the
    # two-cut branch below, where `other = next(c for c in resolved if c
    # != category)` takes the *first* remaining cut and the third is never
    # encoded at all. A 120-row segment-by-channel-by-region cross-tab was
    # drawn as 4 x 5 = 20 bars, each of them six different regions stacked
    # invisibly on one another: a chart of a result the engine did not
    # compute, under a title that named two of the three cuts.
    #
    # Declining is the honest outcome, and it is also what the rest of the
    # system was already relying on. `graph/relevance.py` gives a charted
    # result a small bonus on the grounds that a presentable answer reads
    # better than a number in an empty frame -- which is only true while
    # "charted" means "drawn correctly". The cross-tab was collecting that
    # bonus for a chart that misrepresented it.
    if len(resolved) > MAX_CUTS:
        cuts = ", ".join(_label(column) for column in resolved)
        return {
            "kind": "none",
            "no_chart_reason": (
                f"this result is cut {len(resolved)} ways ({cuts}); a chart here can "
                f"separate {MAX_CUTS}, and leaving one out would draw a different "
                "result. The complete result is in the table"
            ),
        }

    # One cut only.
    if len(resolved) == 1:
        column = resolved[0]
        if has_period:
            return _line(column, measure, value_format, grain)
        if operation == "rank":
            return {
                "kind": "ranked_bar",
                "title": f"{_label(measure)} by {_label(column)}, ranked",
                "spec": _bar_spec(column, measure, sort="-y", value_format=value_format),
            }
        if rows > MAX_BAR_CATEGORIES:
            return {
                "kind": "none",
                "no_chart_reason": (
                    f"{rows} categories is more than a readable bar chart shows; "
                    "the complete result is in the table"
                ),
            }

        # An ordered numeric cut is a sequence, so it is drawn as one.
        #
        # `PERIOD_COLUMN`'s comment above says "every other cut is an
        # unordered category", and for a cut like `age` that is simply not
        # true. `presentation/fields.py` already knows it -- it resolves
        # such a column to `SemanticKind.ORDERED_NUMERIC` and its own
        # comment says plotting one on a categorical axis "is what made a
        # 48-age breakdown unreadable". The chart builder had not been
        # told, so it kept drawing them as labels.
        #
        # A published report is what showed it: "the average bedtime phone
        # minutes by age" over 8,500 rows came back as 48 nominal bars.
        # Forty-eight tick labels collided along the foot of the chart, and
        # because a nominal axis sorts its values as strings their order
        # was a coincidence of every age having two digits.
        #
        # **The y axis keeps its zero.** Suppressing it was the first
        # instinct, because the values span 51.70 to 65.49 and a zero
        # baseline presses them into the top fifth of the frame. It is also
        # the one change here that would have been dishonest: that spread
        # is about a tenth of the mean, the thin-group note beside it says
        # some of those groups rest on 29 rows, and a zoomed axis would
        # draw a decisive pattern over a result whose own shape is "nearly
        # flat, with noise". The axis type was the defect. The baseline was
        # not.
        if _ordered_numeric(snapshot, column) and rows > MAX_ORDERED_BARS:
            return {
                "kind": "line",
                "title": f"{_label(measure)} by {_label(column)}",
                "spec": _line_spec(
                    column, measure, value_format=value_format, axis_type="quantitative"
                ),
            }

        return {
            "kind": "bar",
            "title": f"{_label(measure)} by {_label(column)}",
            "spec": _bar_spec(column, measure, value_format=value_format),
        }

    # Two cuts. Time plus a category is a multi-series line; two categories
    # are grouped bars. Both are declined when they would be unreadable.
    category = next((c for c in resolved if c != "period"), resolved[0])
    series_count = len({row[snapshot.columns.index(category)] for row in snapshot.rows})
    if has_period:
        if series_count > MAX_SERIES:
            return {
                "kind": "none",
                "no_chart_reason": (
                    f"{series_count} series over time is more than a readable chart shows; "
                    "the complete result is in the table"
                ),
            }
        return {
            "kind": "line",
            "title": f"{_label(measure)} over time by {_label(category)}",
            "spec": _line_spec(
                PERIOD_COLUMN, measure, colour=category, value_format=value_format, grain=grain
            ),
        }

    other = next(c for c in resolved if c != category)
    other_count = len({row[snapshot.columns.index(other)] for row in snapshot.rows})
    if series_count * other_count > MAX_GROUPED_CELLS:
        return {
            "kind": "none",
            "no_chart_reason": (
                f"{series_count} x {other_count} groups is more than a readable chart "
                "shows; the complete result is in the table"
            ),
        }
    return {
        "kind": "grouped_bar",
        "title": f"{_label(measure)} by {_label(category)} and {_label(other)}",
        "spec": _bar_spec(category, measure, colour=other, value_format=value_format),
    }


def _line(column: str, measure: str, value_format: str, grain: str | None) -> dict[str, Any]:
    return {
        "kind": "line",
        "title": f"{_label(measure)} over time",
        "spec": _line_spec(column, measure, value_format=value_format, grain=grain),
    }


def _bar_spec(
    category: str,
    measure: str,
    *,
    colour: str | None = None,
    sort: str | None = None,
    value_format: str = ",",
) -> dict[str, Any]:
    encoding: dict[str, Any] = {
        "x": {"field": category, "type": "nominal", "title": _label(category)},
        "y": {
            "field": measure,
            "type": "quantitative",
            "title": _label(measure),
            "axis": {"format": value_format},
        },
    }
    if sort:
        encoding["x"]["sort"] = sort
    fields = [(category, "nominal")]
    if colour:
        encoding["color"] = {"field": colour, "type": "nominal", "title": _label(colour)}
        fields.append((colour, "nominal"))
    encoding["tooltip"] = _tooltip(fields, measure, value_format)
    return {"mark": "bar", "encoding": encoding}


def _line_spec(
    axis: str,
    measure: str,
    *,
    colour: str | None = None,
    value_format: str = ",",
    grain: str | None = None,
    axis_type: str | None = None,
) -> dict[str, Any]:
    """A line over time.

    **The time axis is `temporal`, and it was `ordinal`.** That one word
    is why a published monthly trend had `1264982400000` down its x axis
    while the table beside it said "Feb 2010". The period column is a
    `DATE_TRUNC`, so its values are dates; declared as an unordered
    category, nothing in either formatter would write a date format onto
    it -- the presentation layer keys its format on `type == "temporal"`
    and so skipped the axis, writing only the title. The title arrived and
    the ticks did not, which is exactly what the artefact shows.

    `temporal` is also simply what the field is: this is a line chart
    through time, so the axis is a time scale, ticks thin instead of
    colliding, and the grain decides how much of each date is shown.
    """
    # `axis_type` is passed for an ordered numeric cut, which is
    # `quantitative`: declared `ordinal`, Vega gives a 48-age axis
    # forty-eight discrete ticks and crowds them exactly as the bar chart
    # did. A number line spaces them and thins the labels itself.
    kind = axis_type or ("temporal" if axis == PERIOD_COLUMN else "ordinal")
    time_format = time_format_for(grain) if kind == "temporal" else None
    x: dict[str, Any] = {"field": axis, "type": kind, "title": _label(axis)}
    if time_format:
        # `labelOverlap` for the same reason the registry path sets it:
        # Vega resolves colliding tick labels by *dropping* them, and a
        # time axis that silently loses most of its labels is worse than
        # one that is tight. Greedy keeps as many as fit. Tick count is
        # left to Vega, which has the width and this does not -- the
        # report is laid out at six of them.
        x["axis"] = {"format": time_format, "labelOverlap": "greedy"}
    encoding: dict[str, Any] = {
        "x": x,
        "y": {
            "field": measure,
            "type": "quantitative",
            "title": _label(measure),
            "axis": {"format": value_format},
        },
    }
    fields = [(axis, kind)]
    if colour:
        encoding["color"] = {"field": colour, "type": "nominal", "title": _label(colour)}
        fields.append((colour, "nominal"))
    encoding["tooltip"] = _tooltip(fields, measure, value_format, time_format=time_format)
    return {"mark": {"type": "line", "point": True}, "encoding": encoding}


def _alias_of(column: str) -> str:
    from agentic_analytics.analytics.upload_plan import alias_for

    return alias_for(column)
