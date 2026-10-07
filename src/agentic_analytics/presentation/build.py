"""Assembling one `AnalysisPresentation` from what the run already proved.

A pure function of its inputs. It reads the accepted contract, the verified
snapshot, the recorded coverage and the published findings, and it writes
no number it cannot point at. Give it the same run twice and it returns the
same presentation, which is what lets Compare Both show one result instead
of two that are worded differently.
"""

from __future__ import annotations

from typing import Any

from agentic_analytics.analytics.labels import derivation_of
from agentic_analytics.analytics.results import ResultSnapshot
from agentic_analytics.presentation.charts import presentation_chart
from agentic_analytics.presentation.fields import display_field_for
from agentic_analytics.presentation.schemas import (
    AnalysisPresentation,
    CaveatSeverity,
    DisplayField,
    PresentationCaveat,
    PresentationProvenanceRef,
    PresentationShape,
    PresentationTable,
)
from agentic_analytics.presentation.summarize import (
    THIN_GROUP_ROWS,
    date_range_label,
    describe_filter,
    detect_shape,
    dimension_columns,
    measure_column,
    named_rows,
    numbers_resolve,
    scope_for,
    summarize,
    thin_elsewhere,
    thin_named,
)
from agentic_analytics.verification.typing import as_number

#: Rows the table shows before asking. The complete result stays available
#: and the scope says how many there are, so this never changes a number.
TABLE_PREVIEW_LIMIT = 25


class _Column:
    """A column described from the result alone.

    Used when the upload profile is not to hand. It carries the same
    attributes `display_field_for` reads, so the boolean and percentage
    gates behave identically -- they just have less evidence to work with,
    and therefore decline more often, which is the right direction.
    """

    def __init__(self, name: str, snapshot: ResultSnapshot, *, role: str) -> None:
        self.name = name
        self.role = role
        self.data_type = snapshot.declared_type(name) or ""
        values = {
            str(snapshot.cell(row, name))
            for row in range(len(snapshot.rows))
            if snapshot.cell(row, name) is not None
        }
        self.distinct_count = len(values)
        numeric = sorted(
            (float(v) for v in values if _is_number(v)),
        )
        self.min_value = str(numeric[0]) if numeric else None
        self.max_value = str(numeric[-1]) if numeric else None
        self.null_pct = 0.0
        self.reason = "described from the result"
        self.additive = "unknown"
        self.ambiguous = False


def _is_number(text: str) -> bool:
    try:
        float(text)
    except (TypeError, ValueError):
        return False
    return True


def _observed(snapshot: ResultSnapshot, column: str) -> set[str]:
    return {
        str(snapshot.cell(row, column))
        for row in range(len(snapshot.rows))
        if snapshot.cell(row, column) is not None
    }


class _ProfiledColumn:
    """A profiled column read from its serialised form.

    The graph carries the schema as `InferredSchema.as_dict()`, so the
    profile reaching this layer is nested dictionaries rather than objects.
    Reading it with `getattr` silently found nothing, the profile was
    discarded, and a flag fell back to being described from the aggregated
    result -- which is how `blue_light_filter_active` came back out as a
    bare 0 and 1 in a run that had profiled it correctly.
    """

    __slots__ = (
        "additive",
        "ambiguous",
        "data_type",
        "distinct_count",
        "max_value",
        "min_value",
        "name",
        "null_pct",
        "reason",
        "role",
    )

    def __init__(self, raw: dict[str, Any]) -> None:
        self.name = str(raw.get("name") or "")
        self.data_type = str(raw.get("data_type") or "")
        self.role = str(raw.get("role") or "")
        self.distinct_count = int(raw.get("distinct_count") or 0)
        self.min_value = raw.get("min_value")
        self.max_value = raw.get("max_value")
        self.null_pct = float(raw.get("null_pct") or 0.0)
        self.reason = str(raw.get("reason") or "")
        self.additive = str(raw.get("additive") or "unknown")
        self.ambiguous = bool(raw.get("ambiguous"))


def _profile_fields(schema: Any) -> list[Any]:
    """The profiled columns, from either the object or its serialised form."""
    if schema is None:
        return []
    fields = getattr(schema, "fields", None)
    if fields is None and isinstance(schema, dict):
        fields = schema.get("fields")
    if not fields:
        return []
    out: list[Any] = []
    for entry in fields:
        out.append(_ProfiledColumn(entry) if isinstance(entry, dict) else entry)
    return out


#: The unit a declared metric format implies. `number` and `ratio` imply
#: none: a bare number has no unit, and a ratio's unit depends on what it
#: is a ratio of, which the registry does not say.
_UNIT_FOR_FORMAT = {
    "percent": "%",
    "currency": "$",
    "integer": None,
    "number": None,
    # A proportion on a 0-1 scale, which a two-proportion test's `rate`
    # column is by construction. Declared by the contract, never inferred
    # from seeing 0.5045.
    "proportion": "%",
}

#: What a declared format multiplies a stored value by before it is
#: written. Only one format does: see `Derivation.PROPORTION_0_1`.
_SCALE_FOR_FORMAT = {"proportion": 100.0}


def display_fields_for(
    mapping: Any,
    snapshot: ResultSnapshot,
    schema: Any | None = None,
) -> list[DisplayField]:
    """Presentation metadata for every column of the result.

    The upload profile is preferred when available: it saw the whole column
    rather than the aggregated rows, so its evidence about roles and ranges
    is stronger. The result is the fallback and is also what confirms a
    flag's observed values.
    """
    profile = {str(field.name): field for field in _profile_fields(schema)}

    measure = str(getattr(mapping, "measure", "") or "")
    dimensions = {str(d) for d in (getattr(mapping, "dimensions", ()) or ())}

    # The metric's declared format, for the measure column only.
    #
    # A governed metric says what it is (`format: percent` for a rate)
    # and that is the only honest source for a unit. Never inferred from
    # magnitude: 10.97 is a percentage because the registry says so, not
    # because it happens to be small.
    declared_format = str(getattr(mapping, "measure_format", "") or "")
    measure_unit = _UNIT_FOR_FORMAT.get(declared_format)
    measure_scale = _SCALE_FOR_FORMAT.get(declared_format, 1.0)
    time_grain = str(getattr(mapping, "time_grain", "") or "") or None

    out: list[DisplayField] = []
    seen: set[str] = set()

    def derived(name: str) -> Any | None:
        """The derivation for `name`, when it is ours to claim.

        Two guards, and both are about not overriding the visitor's own
        data. A column the upload profile saw is theirs, and its own
        evidence is stronger than anything this map could say about it --
        a dataset with a column called `change` or `current` is not
        publishing a decomposition. And the contract's measure is never a
        derived column by definition.
        """
        if name == measure or name in profile:
            return None
        return derivation_of(name)

    # Contract columns first, named as the visitor named them, so a
    # presentation can be read without consulting the engine's aliases.
    for name in [measure, *sorted(dimensions)]:
        if not name or name in seen:
            continue
        seen.add(name)

        # A contract dimension can name a tool's own output column, so a
        # timeseries grouped by `period` is the common case, so the
        # derivation is consulted here too. It used to be read in the
        # second loop only, and a dimension named `period` therefore took
        # the fallback path and came back a CATEGORY holding an instant.
        derivation = derived(name)
        if derivation is not None and name in snapshot.columns:
            out.append(
                display_field_for(
                    _Column(name, snapshot, role="dimension"),
                    derivation=derivation,
                    measure_unit=measure_unit,
                    time_grain=time_grain,
                )
            )
            continue

        field = profile.get(name)
        if field is None:
            # No upload profile, which is every governed-warehouse run.
            #
            # This used to `continue` after marking the name seen, so the
            # loop below skipped it too and a contract column present in
            # the result got no presentation metadata at all. That is where
            # the measure's declared unit had nowhere to attach, and the
            # warehouse's own answer read "is highest for new, at 10.97"
            # with no sign that it was a percentage.
            if name not in snapshot.columns:
                continue
            field = _Column(name, snapshot, role="measure" if name == measure else "dimension")
        resolved = name if name in snapshot.columns else None
        out.append(
            display_field_for(
                field,
                observed_values=_observed(snapshot, resolved) if resolved else None,
                unit=measure_unit if name == measure else None,
                scale=measure_scale if name == measure else 1.0,
                time_grain=time_grain,
            )
        )

    for name in snapshot.columns:
        if name in seen:
            continue
        seen.add(name)

        # A column the analytics tools derived is described from the
        # contract, because its own values cannot say what it is. The
        # profile has no entry for it. It did not exist before the query
        # ran, so the evidence rules below saw a nameless numeric column
        # and declined, which is how `period` became a CATEGORY holding an
        # ISO instant and `change_abs` lost the measure's currency.
        derivation = derived(name)
        if derivation is not None:
            out.append(
                display_field_for(
                    _Column(name, snapshot, role="measure"),
                    derivation=derivation,
                    measure_unit=measure_unit,
                    time_grain=time_grain,
                )
            )
            continue

        field = profile.get(name)
        if field is None:
            role = "measure" if name == measure else "dimension"
            field = _Column(name, snapshot, role=role)
        out.append(
            display_field_for(
                field,
                observed_values=_observed(snapshot, name),
                unit=measure_unit if name == measure else None,
                scale=measure_scale if name == measure else 1.0,
                time_grain=time_grain,
            )
        )
    return out


def _empty_result_caveats(mapping: Any, scope: Any) -> list[PresentationCaveat]:
    """Why nothing came back, naming the restrictions that produced it.

    "No rows matched" on its own sends a reader looking for a fault in
    their data. Naming the predicates tells them where to look, and is
    usually enough to spot the cause without opening the audit.

    Built from the accepted contract rather than from the question text, so
    it describes what actually ran, and written through `describe_filter`
    so the explanation and the scope line above it cannot disagree.
    """
    described = [
        sentence
        for entry in (getattr(mapping, "filters", ()) or ())
        if (sentence := describe_filter(entry))
    ]
    period = getattr(mapping, "period", None)
    if period and len(tuple(period)) == 2:
        start, end = tuple(period)
        described.append(f"the period {date_range_label(start, end)}")

    if not described:
        # Reachable: a query over an empty table, or one whose measure is
        # null throughout, has no restriction to blame.
        message = (
            "No rows were available to answer this question. The analysis ran "
            "and the table held nothing to aggregate."
        )
    else:
        message = (
            "No rows matched the requested filters: "
            + "; ".join(described)
            + ". The analysis ran; the data contained no matching rows."
        )
    return [
        PresentationCaveat(
            code="no_rows_matched",
            message=message,
            severity=CaveatSeverity.WARNING,
            related_component="scope",
        )
    ]


def _caveats(
    mapping: Any,
    snapshot: ResultSnapshot,
    question_coverage: Any | None,
    chart: Any,
    *,
    planner_fallback: bool,
    thin: list[tuple[str, int, int]],
    thin_other: list[tuple[int, int]],
) -> list[PresentationCaveat]:
    out: list[PresentationCaveat] = []

    coverage = snapshot.group_coverage
    if coverage is not None and not coverage.complete:
        omitted = coverage.groups_omitted
        detail = (
            f"{omitted} of the requested groups are not in this result."
            if omitted
            else "Not every requested group is in this result."
        )
        out.append(
            PresentationCaveat(
                code="incomplete_groups",
                message=detail,
                severity=CaveatSeverity.WARNING,
                related_component="coverage",
            )
        )

    if question_coverage is not None and not getattr(question_coverage, "complete", True):
        missing_components = ", ".join(
            str(m) for m in getattr(question_coverage, "missing_components", [])
        )
        out.append(
            PresentationCaveat(
                code="incomplete_question_coverage",
                message=(
                    "The executed analysis did not cover every part of the question: "
                    f"{missing_components}."
                    if missing_components
                    else "The executed analysis did not cover every part of the question."
                ),
                severity=CaveatSeverity.WARNING,
                related_component="coverage",
            )
        )

    if snapshot.truncated:
        out.append(
            PresentationCaveat(
                code="result_truncated",
                message="The result was truncated in transport; the table is not the whole answer.",
                severity=CaveatSeverity.WARNING,
                related_component="coverage",
            )
        )

    def summed_count(column: str) -> int | None:
        if not snapshot.rows or column not in snapshot.columns:
            return None
        values = [as_number(snapshot.cell(row, column)) for row in range(len(snapshot.rows))]
        if any(value is None for value in values):
            return None
        return int(sum(value for value in values if value is not None))

    row_count = summed_count("row_count")
    value_count = summed_count("value_count")
    population = coverage.rows_matching if coverage is not None else row_count
    observations = coverage.observations_matching if coverage is not None else value_count
    if population is not None and observations is not None and observations < population:
        measure = str(getattr(mapping, "measure", "") or "the measure")
        missing_values = population - observations
        row_word = "row" if missing_values == 1 else "rows"
        population_label = (
            f"matching {row_word}"
            if coverage is not None or len(snapshot.rows) == 1
            else f"{row_word} represented in this result"
        )
        out.append(
            PresentationCaveat(
                code="missing_measure_values",
                message=(
                    f"{missing_values:,} {population_label} had no "
                    f"{measure} value and were excluded from the aggregate."
                ),
                severity=CaveatSeverity.WARNING,
                related_component="measure",
            )
        )

    # An extreme whose own group holds almost nothing.
    #
    # The headline names the group and now states its population too (see
    # `thin_extreme_note`), and this is the same fact under "What to be
    # careful about", which is the one place in the report that is *for*
    # things qualifying the answer. It is not a repeat of the no-chart
    # wording problem: a missing chart is a fact about empty space and
    # belongs in that space, while an estimate resting on two rows
    # qualifies the answer itself.
    if thin:
        smallest = min(population for _role, _row, population in thin)
        row_word = "row" if smallest == 1 else "rows"
        out.append(
            PresentationCaveat(
                code="thin_group_extreme",
                message=(
                    f"A group named in the answer holds {smallest:,} {row_word}. "
                    f"Fewer than {THIN_GROUP_ROWS} rows is too few for a rate or an "
                    "average over that group to be a reliable estimate, and the "
                    "highest and lowest groups of a fine breakdown are where the "
                    "smallest populations collect."
                ),
                severity=CaveatSeverity.WARNING,
                related_component="coverage",
            )
        )

    # And groups the answer does not name.
    #
    # The caveat above fires on the population of the group the headline
    # quotes. That is not the only way a thin group changes how a result
    # should be read: on a fine breakdown the reader is shown a *range*,
    # and a range whose members rest on 29 rows and on 363 is not the
    # like-for-like comparison it looks like. Separate code, because it is
    # a different fact and a reader who sees both should be able to tell
    # that they are two.
    if thin_other:
        smallest = min(population for _row, population in thin_other)
        count = len(thin_other)
        group_word = "group" if count == 1 else "groups"
        verb = "holds" if count == 1 else "hold"
        out.append(
            PresentationCaveat(
                code="thin_groups_elsewhere",
                message=(
                    f"{count:,} {group_word} in this result {verb} fewer than "
                    f"{THIN_GROUP_ROWS} rows, the smallest {smallest:,}. Those "
                    "figures are in the range the answer reports and are weaker "
                    "estimates than the groups beside them, so the spread across "
                    "this breakdown is partly the spread of its sample sizes."
                ),
                severity=CaveatSeverity.WARNING,
                related_component="coverage",
            )
        )

    # No `no_chart` caveat. It used to be here *as well as* inline where the
    # chart would be, so a reader was told the same thing twice, once
    # under the answer and once under "What to be careful about", which is
    # for things that qualify the answer. A missing chart does not qualify
    # an answer; it is a fact about the space where a chart is not. That
    # fact belongs in that space, and `AnswerReport` renders it there.

    if planner_fallback:
        out.append(
            PresentationCaveat(
                code="planner_fallback",
                message=(
                    "The cloud planner did not return a usable contract, so the engine "
                    "executed its own. This is not the model agreeing independently."
                ),
                severity=CaveatSeverity.NOTE,
                related_component="planning",
            )
        )
    return out


def build_presentation(
    *,
    mapping: Any,
    snapshot: ResultSnapshot | None,
    findings: list[Any] | None = None,
    question_coverage: Any | None = None,
    chart_decision: dict[str, Any] | None = None,
    schema: Any | None = None,
    planner_fallback: bool = False,
    outcome: str = "completed",
    stopped_reason: str = "",
) -> AnalysisPresentation:
    """One analysis, described for presentation.

    Returns a refusal or failure presentation rather than raising when
    there is no result: a reader is owed the reason, and an empty report
    with a COMPLETE badge was the previous behaviour.
    """
    findings = list(findings or [])

    if outcome == "refused" or (mapping is not None and not getattr(mapping, "confident", True)):
        reason = (
            stopped_reason
            or getattr(mapping, "explanation", "")
            or ("The question could not be mapped to this dataset safely.")
        )
        return AnalysisPresentation(
            shape=PresentationShape.REFUSAL,
            headline=str(reason),
            caveats=[
                PresentationCaveat(
                    code="refused",
                    message=str(reason),
                    severity=CaveatSeverity.BLOCKING,
                    related_component="contract",
                )
            ],
        )

    if snapshot is None or outcome != "completed":
        reason = stopped_reason or "The analysis could not be completed."
        return AnalysisPresentation(
            shape=PresentationShape.FAILURE,
            headline=str(reason),
            caveats=[
                PresentationCaveat(
                    code=str(outcome or "failed"),
                    message=str(reason),
                    severity=CaveatSeverity.BLOCKING,
                )
            ],
        )

    display_fields = display_fields_for(mapping, snapshot, schema)
    shape = detect_shape(mapping, snapshot, display_fields)
    headline, secondary, highlights, derived = summarize(mapping, snapshot, display_fields, shape)
    scope = scope_for(mapping, snapshot)
    # Computed once and handed to both surfaces. `summarize` states this in
    # the summary and `_caveats` states it under "What to be careful about";
    # if each worked it out for itself they would name different groups on
    # an ascending ranking.
    measure_named = measure_column(snapshot, mapping)
    thin = thin_named(mapping, snapshot, measure_named, shape) if measure_named is not None else []
    thin_other = (
        thin_elsewhere(snapshot, named_rows(mapping, snapshot, measure_named, shape))
        if measure_named is not None
        else []
    )

    chart = presentation_chart(
        chart_decision,
        snapshot,
        chart_id=str(getattr(findings[0], "finding_id", "")) if findings else None,
        # The same display metadata the table and the headline use, so the
        # axis cannot say `2025-01` under a headline that says "Oct 2025".
        fields=display_fields,
        # What kind of answer this is, so an absent chart can say why in
        # terms of the result rather than in terms of the engine's record.
        shape=shape,
    )

    if not headline:
        # Nothing publishable was derived. Two very different reasons reach
        # here and they were being given the same sentence.
        #
        # An empty result is not an unsummarisable one. "The analysis
        # produced a result that could not be summarised as an answer"
        # blames the engine for what is usually a filter that matched
        # nothing: a value spelled differently, a period outside the
        # file's range, and sends the reader looking for a fault in the
        # product instead of at the restriction they asked for.
        #
        # So the empty case says what happened and names the predicates,
        # through the same `describe_filter` the scope line uses. The
        # sentence itself is not new: `graph/build.py` has written it into
        # `report.limitations` for a long time, where only the
        # pre-presentation rendering path showed it, a path that was
        # being reached by accident, because `scope_for` raised on a
        # resolved filter object and `_presentation_for` swallows every
        # exception by design.
        empty = not snapshot.rows
        return AnalysisPresentation(
            shape=PresentationShape.FAILURE,
            headline=(
                "No rows matched the filters this question asked for."
                if empty
                else "The analysis produced a result that could not be summarised as an answer."
            ),
            scope=scope,
            display_fields=display_fields,
            caveats=(
                _empty_result_caveats(mapping, scope)
                if empty
                else [
                    PresentationCaveat(
                        code="unsummarisable_result",
                        message=(
                            "The result did not match any supported answer shape, so no "
                            "statement was published. The table holds what was computed."
                        ),
                        severity=CaveatSeverity.BLOCKING,
                    )
                ]
            ),
        )

    # The prose is checked, not trusted. A figure that is not a cell, a
    # recorded count or a declared difference does not go out.
    for text in (headline, secondary or ""):
        unresolved = numbers_resolve(text, snapshot, scope, derived=derived, fields=display_fields)
        if unresolved:
            raise ValueError(
                f"presentation states {unresolved} which the cited result does not hold"
            )

    dimensions = dimension_columns(snapshot, mapping)
    measure = measure_column(snapshot, mapping)
    # `value_count` is a verification/scope field, not a second business
    # measure. Showing it beside `row_count` on every complete result adds a
    # redundant column and can force a phone-wide table. When the two differ,
    # scope and a coded caveat state the effective observation count.
    visible = [c for c in snapshot.columns if c in {*dimensions, measure} or c == "row_count"]
    table = PresentationTable(
        result_id=snapshot.result_id,
        visible_columns=visible or list(snapshot.columns),
        display_fields=[f for f in display_fields if f.source_name in snapshot.columns],
        default_sort=measure if shape is PresentationShape.RANKING else None,
        preview_limit=TABLE_PREVIEW_LIMIT if len(snapshot.rows) > TABLE_PREVIEW_LIMIT else None,
        complete=(
            snapshot.group_coverage.complete if snapshot.group_coverage is not None else None
        ),
    )

    provenance = [
        PresentationProvenanceRef(
            finding_id=str(getattr(finding, "finding_id", "")),
            result_id=snapshot.result_id,
            evidence_cells=list(getattr(finding, "evidence_cells", []) or []),
        )
        for finding in findings
        if getattr(finding, "finding_id", None)
    ]

    return AnalysisPresentation(
        shape=shape,
        headline=headline,
        secondary_summary=secondary,
        highlights=highlights,
        scope=scope,
        display_fields=display_fields,
        table=table,
        chart=chart,
        caveats=_caveats(
            mapping,
            snapshot,
            question_coverage,
            chart,
            planner_fallback=planner_fallback,
            thin=thin,
            thin_other=thin_other,
        ),
        provenance_refs=provenance,
    )
