"""Typed presentation of one verified analysis.

The frontend used to build a report by arranging `finding.text`. That made
the browser responsible for recovering analytical structure from prose it
could only guess at: which number was the headline, whether `0` meant a
boolean, whether a breakdown was complete, which axis was ordered. Every
one of those guesses was wrong somewhere, and the failures looked like
sloppiness rather than like the missing contract they were.

So the engine says what the result *means* for presentation, in a typed
object, derived only from things it already proved: the accepted contract,
the verified snapshot, the coverage records and the provenance cells. No
model writes any of this. Nothing here recomputes an analytical value --
every figure is read back out of a cited cell, so a presentation that
disagrees with the table is a contradiction the numeric verifier catches.

What this module is not: a styling description. It carries no colours, no
layout and no component names. It says "this is a comparison of two
labelled groups, here are the two values, here is what they are called,
here is the cell each came from". How that looks is the frontend's business.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentic_analytics.analytics.results import EvidenceCell, Scalar

#: Bumped when a consumer would have to change to read this correctly.
#: Additive optional fields do not bump it; a removed or re-meant field
#: does. The frontend refuses a major it does not know rather than
#: rendering half of it.
PRESENTATION_SCHEMA_VERSION = "1.0"


class PresentationShape(StrEnum):
    """What kind of answer this is, which is what decides how to show it.

    Derived from the contract and the result's actual shape, never from the
    wording of the question or from a model's opinion.
    """

    SCALAR = "scalar"
    BOOLEAN_COMPARISON = "boolean_comparison"
    CATEGORICAL_BREAKDOWN = "categorical_breakdown"
    ORDERED_NUMERIC_SERIES = "ordered_numeric_series"
    TIME_SERIES = "time_series"
    RANKING = "ranking"
    MULTI_DIMENSIONAL = "multi_dimensional"
    STATISTICAL_TEST = "statistical_test"
    REFUSAL = "refusal"
    FAILURE = "failure"


class SemanticKind(StrEnum):
    """What a column is, for display purposes only.

    Deliberately coarser than the schema's own role inference: this answers
    "how should this be written on screen", not "may this be aggregated".
    """

    MEASURE = "measure"
    CATEGORY = "category"
    BOOLEAN = "boolean"
    ORDERED_NUMERIC = "ordered_numeric"
    TIME = "time"
    IDENTIFIER = "identifier"
    COUNT = "count"


class InterpretationLevel(StrEnum):
    """How far beyond the cells a statement goes.

    A highlight that only restates a cell is `measured`. One that subtracts
    two cells is `derived`. Anything that would need a test the engine did
    not run is not allowed at all, which is why there is no level for it.
    """

    MEASURED = "measured"
    DERIVED = "derived"


class CaveatSeverity(StrEnum):
    NOTE = "note"
    WARNING = "warning"
    BLOCKING = "blocking"


class Strict(BaseModel):
    """Reject unknown keys everywhere.

    A typo in a field name silently becoming an ignored extra is how a
    contract stops being a contract.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class DisplayField(Strict):
    """How one column of the result is named and formatted on screen.

    `source_name` is always kept: the technical inspector and provenance
    show the visitor's own column name, and a humanised label must never be
    the only record of what was queried.
    """

    source_name: str
    display_label: str
    semantic_kind: SemanticKind
    #: Only set when the dataset or a governed metric supplies it. Never
    #: inferred from a column name: "revenue" does not say which currency,
    #: and a wrong unit is worse than none.
    unit: str | None = None
    #: Decimal places for display. `None` means "show the value as stored".
    precision: int | None = None
    #: Label per raw value, for a confidently boolean field only, as
    #: `{"0": "Off", "1": "On"}`. Keys are strings so this survives JSON.
    boolean_labels: dict[str, str] | None = None
    #: Whether the values have a meaningful order. Drives a numeric axis
    #: instead of a categorical one.
    ordered: bool = False
    identifier: bool = False
    #: Whether values should be withheld from non-provenance surfaces.
    sensitive: bool = False

    @model_validator(mode="after")
    def _labels_only_for_booleans(self) -> DisplayField:
        if self.boolean_labels and self.semantic_kind is not SemanticKind.BOOLEAN:
            raise ValueError(
                f"{self.source_name!r} carries boolean labels but is "
                f"{self.semantic_kind.value}; a two-valued column is not a boolean"
            )
        return self


class PresentationValue(Strict):
    """One number, as stored and as written.

    Both halves are kept because they answer different questions. The
    formatted form is what a reader sees; the raw form is what verification
    and the table compare against, and rounding one into the other is how a
    report starts disagreeing with its own evidence.
    """

    raw_value: Scalar = None
    formatted_value: str
    unit: str | None = None


class PresentationHighlight(Strict):
    """One figure worth putting in front of the reader.

    Every highlight cites the cells it came from. A highlight with no
    evidence is a claim the engine cannot support, so the schema does not
    allow one.
    """

    highlight_id: str
    label: str
    value: PresentationValue
    #: The other side of a two-group comparison, when there is one.
    comparison_value: PresentationValue | None = None
    #: `value - comparison_value`, computed from the cells rather than
    #: parsed back out of the formatted strings.
    delta: PresentationValue | None = None
    evidence_cells: list[EvidenceCell] = Field(min_length=1)
    interpretation_level: InterpretationLevel = InterpretationLevel.MEASURED

    @model_validator(mode="after")
    def _delta_needs_both_sides(self) -> PresentationHighlight:
        if self.delta is not None and self.comparison_value is None:
            raise ValueError("a delta without a comparison value states a difference from nothing")
        if self.delta is not None and self.interpretation_level is not InterpretationLevel.DERIVED:
            raise ValueError("a delta is derived, not measured")
        return self


class PresentationScope(Strict):
    """What population the answer covers, and whether that is all of it.

    These are the recorded coverage numbers, carried through unchanged. The
    one thing this type must never do is compute `complete` itself: every
    signal that looked like completeness inferred it wrongly, which is why
    `GroupCoverage` records the four meanings of "limit" separately.
    """

    rows_total: int | None = None
    rows_matching: int | None = None
    rows_represented: int | None = None
    observations_matching: int | None = None
    observations_represented: int | None = None
    groups_returned: int | None = None
    groups_total: int | None = None
    #: Whether every group the question asked for is present. `None` means
    #: "this is not a grouped answer", never "it is complete".
    complete: bool | None = None
    filters: list[str] = Field(default_factory=list)
    period: str | None = None
    ordering: Literal["dimension", "measure", "period"] | None = None


class PresentationTable(Strict):
    """The table beside the answer, pointing at the same snapshot."""

    result_id: str
    visible_columns: list[str] = Field(min_length=1)
    display_fields: list[DisplayField] = Field(default_factory=list)
    default_sort: str | None = None
    preview_limit: int | None = None
    #: Whether the snapshot holds every row of the answer. A preview limit
    #: is a display choice and never changes this.
    complete: bool | None = None


class PresentationChart(Strict):
    """The chart decision, including the decision not to draw one."""

    chart_id: str | None = None
    result_id: str | None = None
    kind: str = "none"
    title: str | None = None
    subtitle: str | None = None
    x_field: str | None = None
    y_field: str | None = None
    series_field: str | None = None
    no_chart_reason: str | None = None
    #: The Vega-Lite specification, data-free. The browser resolves
    #: `result_id` against the snapshot it already holds; see
    #: `web/src/lib/chartHydration.ts` for why the rows are not here.
    spec: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _a_chart_or_a_reason(self) -> PresentationChart:
        drawn = self.kind != "none"
        if drawn and self.spec is None and self.kind != "kpi":
            raise ValueError(f"chart kind {self.kind!r} carries no specification")
        if not drawn and not self.no_chart_reason:
            raise ValueError("declining a chart requires a reason the reader can read")
        if drawn and self.spec is not None and "data" in self.spec:
            raise ValueError(
                "a published chart specification must not carry its own rows; "
                "it cites result_id and the browser resolves it"
            )
        return self


class PresentationCaveat(Strict):
    """Something true about the answer that qualifies it.

    Coded so the frontend can group and the tests can assert on the code
    rather than on prose that will be rewritten.
    """

    code: str
    message: str
    severity: CaveatSeverity = CaveatSeverity.NOTE
    #: Which part of the question or pipeline this is about, when it maps
    #: to one: `measure`, `dimensions`, `period`, `coverage`, `chart`.
    related_component: str | None = None


class PresentationProvenanceRef(Strict):
    """Where one published statement's numbers came from."""

    finding_id: str
    result_id: str
    evidence_cells: list[EvidenceCell] = Field(default_factory=list)


class AnalysisPresentation(Strict):
    """One verified analysis, ready to render without interpretation.

    A consumer may read `headline`, `highlights`, `scope`, `table` and
    `chart` and show them directly. It must not need to parse prose, infer
    a unit, decide what a `0` means, or work out whether a breakdown was
    complete -- all four are stated here because all four were being
    guessed.
    """

    schema_version: str = PRESENTATION_SCHEMA_VERSION
    shape: PresentationShape
    headline: str
    secondary_summary: str | None = None
    highlights: list[PresentationHighlight] = Field(default_factory=list)
    scope: PresentationScope = Field(default_factory=PresentationScope)
    display_fields: list[DisplayField] = Field(default_factory=list)
    table: PresentationTable | None = None
    chart: PresentationChart | None = None
    caveats: list[PresentationCaveat] = Field(default_factory=list)
    provenance_refs: list[PresentationProvenanceRef] = Field(default_factory=list)
    #: True when this was reconstructed from an older payload that predates
    #: the contract. Such a presentation is necessarily partial, and saying
    #: so is the difference between a limitation and a fabrication.
    compatibility_derived: bool = False

    @model_validator(mode="after")
    def _terminal_shapes_carry_no_result(self) -> AnalysisPresentation:
        terminal = self.shape in (PresentationShape.REFUSAL, PresentationShape.FAILURE)
        if terminal and (self.highlights or self.table):
            raise ValueError(f"{self.shape.value} presents no result, so it carries no figures")
        if not terminal and not self.headline:
            raise ValueError("an answered analysis needs a headline")
        return self

    @model_validator(mode="after")
    def _prose_does_not_enumerate(self) -> AnalysisPresentation:
        """Guard the two prose failures that produced unreadable reports.

        A semicolon run repeating a grouped table, and a sentence that
        trails off in "and further groups". Both were real published
        output; both are structurally impossible to miss here, so they are
        rejected at the boundary rather than caught by a reviewer.
        """
        for label, text in (
            ("headline", self.headline),
            ("secondary summary", self.secondary_summary or ""),
        ):
            if text.count(";") > 1:
                raise ValueError(
                    f"the {label} enumerates results with semicolons; "
                    "a breakdown belongs in the table"
                )
            lowered = text.lower()
            for trailing in ("and further group", "and further period"):
                if trailing in lowered:
                    raise ValueError(
                        f"the {label} trails off in {trailing!r}; "
                        "state the shape of the breakdown instead"
                    )
        return self
