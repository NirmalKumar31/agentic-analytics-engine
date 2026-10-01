"""Reading an older payload that predates the presentation contract.

The committed recordings are evidence. They were produced by a build that
had no presentation layer, and rewriting them so they look like they did
would destroy the thing that makes them useful.

So this derives what an old payload can actually support and says so. It
reads the published findings, the result snapshots and the contract that
were recorded, and it sets `compatibility_derived`. What it never does is
fill a gap: a payload with no coverage record gets `complete=None`, not a
guess, because "we did not record this" and "every group is present" were
confused once already and it published a top-25 of 45 as a complete
breakdown.

A consumer should treat a compatibility-derived presentation as a
convenience for rendering an archive, never as evidence about the run.
"""

from __future__ import annotations

from typing import Any

from agentic_analytics.presentation.schemas import (
    AnalysisPresentation,
    CaveatSeverity,
    PresentationCaveat,
    PresentationChart,
    PresentationScope,
    PresentationShape,
    PresentationTable,
)

#: Said on every derived presentation, so a reader is never left thinking
#: the older run recorded more than it did.
DERIVED_NOTE = (
    "This summary was reconstructed from an archived run that predates the "
    "presentation contract. Coverage and highlight details were not recorded "
    "at the time and are not inferred here."
)


def _headline(payload: dict[str, Any]) -> str:
    """The recorded answer, as the older build published it.

    Taken verbatim from the first published finding. It may well be one of
    the semicolon enumerations this contract exists to replace -- that is
    what the run published, and showing it unchanged is the honest
    rendering of an archive.
    """
    findings = payload.get("findings") or []
    for finding in findings:
        text = str((finding or {}).get("text") or "").strip()
        if text:
            return text
    report = payload.get("report") or {}
    for key in ("headline", "summary", "answer"):
        text = str((report or {}).get(key) or "").strip()
        if text:
            return text
    return "This archived run published no answer."


def _shape(payload: dict[str, Any]) -> PresentationShape:
    """A deliberately coarse shape.

    An archived payload does not carry the display metadata that
    distinguishes a boolean comparison from a small categorical breakdown,
    and inventing the distinction here would put labels on values that were
    never profiled. Only the cases the recorded contract states outright
    are claimed; everything else is the safe general shape.
    """
    outcome = str(payload.get("outcome") or "completed")
    if outcome == "refused":
        return PresentationShape.REFUSAL
    if outcome != "completed":
        return PresentationShape.FAILURE
    if not (payload.get("findings") or []):
        return PresentationShape.FAILURE

    contract = payload.get("query_contract") or {}
    dimensions = list(contract.get("dimensions") or [])
    if not dimensions and contract.get("dimension"):
        dimensions = [contract["dimension"]]
    operation = str(contract.get("operation") or "")

    if operation == "rank":
        return PresentationShape.RANKING
    if contract.get("time_grain") or operation == "trend":
        return PresentationShape.TIME_SERIES
    if len(dimensions) > 1:
        return PresentationShape.MULTI_DIMENSIONAL
    if not dimensions:
        return PresentationShape.SCALAR
    return PresentationShape.CATEGORICAL_BREAKDOWN


def _aggregate(payload: dict[str, Any]) -> dict[str, Any] | None:
    for snapshot in (payload.get("results") or {}).values():
        if (snapshot or {}).get("tool_name") == "aggregate_for_question":
            return dict(snapshot)
    return None


def presentation_from_payload(payload: dict[str, Any]) -> AnalysisPresentation:
    """A limited presentation for an archived run."""
    shape = _shape(payload)
    headline = _headline(payload)

    caveats = [
        PresentationCaveat(
            code="compatibility_derived",
            message=DERIVED_NOTE,
            severity=CaveatSeverity.NOTE,
        )
    ]

    if shape in (PresentationShape.REFUSAL, PresentationShape.FAILURE):
        reason = str(payload.get("stopped_reason") or "").strip()
        return AnalysisPresentation(
            shape=shape,
            headline=reason or headline,
            caveats=caveats,
            compatibility_derived=True,
        )

    snapshot = _aggregate(payload)
    table = None
    scope = PresentationScope()
    if snapshot:
        columns = [str(c) for c in (snapshot.get("columns") or [])]
        recorded = snapshot.get("group_coverage") or {}
        # Only what was written down. An absent record stays absent.
        scope = PresentationScope(
            rows_total=recorded.get("rows_total"),
            rows_matching=recorded.get("rows_matching"),
            rows_represented=recorded.get("rows_represented"),
            groups_returned=recorded.get("groups_returned"),
            groups_total=recorded.get("groups_total"),
            complete=recorded.get("complete"),
            ordering=recorded.get("ordering"),
        )
        if columns:
            table = PresentationTable(
                result_id=str(snapshot.get("result_id") or ""),
                visible_columns=columns,
                complete=recorded.get("complete"),
            )

    charts = payload.get("charts") or []
    chart: PresentationChart | None = None
    if charts:
        first = charts[0] or {}
        spec = first.get("spec")
        # An archived specification is only restated when it is already in
        # the shape this contract publishes: a mark, an encoding, and no
        # rows of its own. Older builds inlined their data, and rewriting
        # one into the current shape would be editing the evidence.
        usable = isinstance(spec, dict) and bool(spec.get("mark")) and "data" not in spec
        chart = PresentationChart(
            chart_id=first.get("chart_id"),
            result_id=first.get("result_id"),
            kind="bar" if usable else "none",
            title=first.get("title"),
            spec=spec if usable else None,
            no_chart_reason=(
                None
                if usable
                else (
                    "this archived chart carried its own rows, which the "
                    "presentation contract does not restate; the recorded "
                    "chart is unchanged in the archive"
                )
            ),
        )

    return AnalysisPresentation(
        shape=shape,
        headline=headline,
        scope=scope,
        table=table,
        chart=chart,
        caveats=caveats,
        compatibility_derived=True,
    )
