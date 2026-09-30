"""Typed proof that an executed result answers the accepted query contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentic_analytics.verification.constraints import (
    MISSING_DIMENSION,
    MISSING_FILTER,
    MISSING_PERIOD,
    WRONG_MEASURE,
    WRONG_OPERATION,
    check_constraints,
)

WRONG_PERIOD = "wrong_period"
WRONG_OUTPUT_SHAPE = "wrong_output_shape"


@dataclass(frozen=True)
class AnswerCoverage:
    """The contract components that one cited result preserved."""

    applicable: bool
    complete: bool = True
    operation: bool = True
    measure: bool = True
    dimensions: bool = True
    filters: bool = True
    period: bool = True
    output_shape: bool = True
    rule: str = ""
    reason: str = ""
    required: dict[str, Any] = field(default_factory=dict)
    missing: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "applicable": self.applicable,
            "complete": self.complete,
            "operation": self.operation,
            "measure": self.measure,
            "dimensions": self.dimensions,
            "filters": self.filters,
            "period": self.period,
            "output_shape": self.output_shape,
            "rule": self.rule,
            "reason": self.reason,
            "required": self.required,
            "missing": list(self.missing),
        }


def _parameters(snapshot: Any) -> dict[str, Any]:
    return dict(getattr(snapshot, "parameters", None) or {})


def _is_metric_contract(mapping: Any) -> bool:
    canonical: Any = getattr(mapping, "canonical_dict", lambda: {})()
    return isinstance(canonical, dict) and canonical.get("kind") == "metric_registry"


def _shape_holds(snapshot: Any, mapping: Any) -> bool:
    columns = set(getattr(snapshot, "columns", None) or [])
    dimensions = getattr(mapping, "dimensions", None) or ()
    if not dimensions:
        dimensions = (getattr(mapping, "dimension", None),)
    if any(dimension and dimension not in columns for dimension in dimensions):
        return False
    # An empty population is a valid answer.  Shape is about the result
    # schema, not whether any row survived the requested restrictions.
    return bool(columns)


def _failure(
    mapping: Any,
    *,
    rule: str,
    reason: str,
    missing: str,
) -> AnswerCoverage:
    return AnswerCoverage(
        applicable=True,
        complete=False,
        operation=missing != "operation",
        measure=missing != "measure",
        dimensions=missing != "dimensions",
        filters=missing != "filters",
        period=missing != "period",
        output_shape=missing != "output_shape",
        rule=rule,
        reason=reason,
        required=mapping.canonical_dict(),
        missing=(missing,),
    )


def check_answer_coverage(mapping: Any, snapshots: list[Any]) -> AnswerCoverage:
    """Require one cited result to carry the whole accepted contract.

    A profile result may support context around an answer, but it cannot
    prove that the aggregate used the requested population.  Every
    component therefore has to be present on the same executed result.
    """
    if mapping is None or not getattr(mapping, "confident", False):
        return AnswerCoverage(applicable=False)

    metric_contract = _is_metric_contract(mapping)
    expected_tool = "compute_metric" if metric_contract else "aggregate_for_question"
    aggregates = [item for item in snapshots if getattr(item, "tool_name", "") == expected_tool]
    if not aggregates:
        # A profile claim can still be judged for support and relevance; it
        # simply is not the direct answer whose executed contract this gate
        # proves.  The canonical answer always cites the aggregate.
        return AnswerCoverage(applicable=False, required=mapping.canonical_dict())

    failures: list[AnswerCoverage] = []
    for snapshot in aggregates:
        params = _parameters(snapshot)
        operation = getattr(mapping, "operation", None)
        if not metric_contract and params.get("operation") != operation:
            failures.append(
                _failure(
                    mapping,
                    rule=WRONG_OPERATION,
                    reason=(
                        f"the question requires {operation}, but this result records "
                        f"{params.get('operation') or 'no operation'}"
                    ),
                    missing="operation",
                )
            )
            continue
        measure = getattr(mapping, "measure", None)
        actual_measure = params.get("metric") if metric_contract else params.get("measure")
        if measure is not None and actual_measure != measure:
            failures.append(
                _failure(
                    mapping,
                    rule=WRONG_MEASURE,
                    reason=(
                        f"the question asks about {str(measure).replace('_', ' ')}, but this "
                        f"result measures {str(actual_measure).replace('_', ' ')}"
                    ),
                    missing="measure",
                )
            )
            continue

        wanted_period = getattr(mapping, "period", None)
        actual_period = params.get("period")
        if wanted_period and actual_period not in (None, list(wanted_period)):
            failures.append(
                _failure(
                    mapping,
                    rule=WRONG_PERIOD,
                    reason="the result covers a different period from the one requested",
                    missing="period",
                )
            )
            continue

        # The metric tool records dimensions as a list and its time grain
        # separately; upload aggregates use the older singular fields.
        if metric_contract:
            wanted_dimensions = list(getattr(mapping, "dimensions", ()) or ())
            if params.get("dimensions") != wanted_dimensions:
                failures.append(
                    _failure(
                        mapping,
                        rule=MISSING_DIMENSION,
                        reason="the executed metric result omitted a requested breakdown",
                        missing="dimensions",
                    )
                )
                continue
            if params.get("time_grain") != getattr(mapping, "time_grain", None):
                failures.append(
                    _failure(
                        mapping,
                        rule=WRONG_PERIOD,
                        reason="the executed metric result used a different time grain",
                        missing="period",
                    )
                )
                continue
            constraints = None
        else:
            constraints = check_constraints(None, mapping, [snapshot])
        if constraints is not None and constraints.applicable and not constraints.preserved:
            component = {
                MISSING_FILTER: "filters",
                MISSING_DIMENSION: "dimensions",
                MISSING_PERIOD: "period",
                WRONG_MEASURE: "measure",
                WRONG_OPERATION: "operation",
            }.get(constraints.rule, "output_shape")
            failures.append(
                _failure(
                    mapping,
                    rule=constraints.rule,
                    reason=constraints.reason,
                    missing=component,
                )
            )
            continue

        if not _shape_holds(snapshot, mapping):
            failures.append(
                _failure(
                    mapping,
                    rule=WRONG_OUTPUT_SHAPE,
                    reason="the executed result does not have the requested answer shape",
                    missing="output_shape",
                )
            )
            continue
        return AnswerCoverage(
            applicable=True,
            required=mapping.canonical_dict(),
        )

    return failures[0]
