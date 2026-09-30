"""Deterministically ground simple warehouse metric questions.

The metric registry is the authority for governed datasets.  A question such
as ``total revenue by region`` must therefore not be delegated to a planner
that is free to turn it into a trend or to select one region.  This module
accepts only wording that maps unambiguously to registry names, then carries
that interpretation through execution and verification.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from agentic_analytics.warehouse.metrics import MetricRegistry


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _mentioned(text: str, name: str) -> bool:
    normal = _normalise(text)
    value = _normalise(name)
    return bool(value and re.search(rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])", normal))


def _time_grain(text: str) -> str | None:
    normal = _normalise(text)
    for word, grain in (
        ("daily", "day"),
        ("day", "day"),
        ("weekly", "week"),
        ("week", "week"),
        ("monthly", "month"),
        ("month", "month"),
        ("quarterly", "quarter"),
        ("quarter", "quarter"),
        ("yearly", "year"),
        ("year", "year"),
    ):
        if re.search(rf"(?<![a-z0-9]){word}(?![a-z0-9])", normal):
            return grain
    return None


@dataclass(frozen=True)
class MetricQuestionMapping:
    """One non-executable, registry-grounded metric query contract."""

    metric: str | None
    dimensions: tuple[str, ...] = ()
    time_grain: str | None = None
    confident: bool = True
    explanation: str = ""
    interpretation: str = "metric-registry"
    named_metrics: list[str] = field(default_factory=list)
    named_dimensions: list[str] = field(default_factory=list)
    metric_format: str = "number"

    # Compatibility names used by existing intent/coverage/report plumbing.
    operation: str = "aggregate"
    measure: str | None = None
    dimension: str | None = None
    filters: tuple[Any, ...] = ()
    period: None = None

    def __post_init__(self) -> None:
        if self.measure is None:
            object.__setattr__(self, "measure", self.metric)
        if self.dimension is None and self.dimensions:
            object.__setattr__(self, "dimension", self.dimensions[0])

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "kind": "metric_registry",
            "operation": self.operation,
            "metric": self.metric,
            "dimensions": list(self.dimensions),
            "time_grain": self.time_grain,
        }

    @property
    def contract_hash(self) -> str:
        blob = json.dumps(self.canonical_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.canonical_dict(),
            "table": None,
            "measure": self.measure,
            "dimension": self.dimension,
            "filters": [],
            "ascending": False,
            "confident": self.confident,
            "explanation": self.explanation,
            "interpretation": self.interpretation,
            "contract_hash": self.contract_hash,
            "canonical_contract": self.canonical_dict(),
        }


def resolve_question(question: str, registry: MetricRegistry) -> MetricQuestionMapping | None:
    """Return a safe direct aggregate contract, or ``None`` for richer work.

    A named unknown metric/dimension is a refusal rather than a fallback.  A
    question that contains no recognisable metric remains on the existing
    analysis path, which preserves advanced warehouse analyses.
    """
    normal = _normalise(question)
    # This contract deliberately owns direct aggregates only.  Ranking,
    # change, causal and temporal questions need their specialised governed
    # tools; treating any mention of a metric as a plain aggregate broke the
    # benchmark by turning "what caused revenue to rise" into a total.
    complex_markers = (
        "highest",
        "lowest",
        "weakest",
        "rank",
        "increase",
        "increased",
        "decrease",
        "decreased",
        "decline",
        "declined",
        "fell",
        "rose",
        "change",
        "trend",
        "over time",
        "monthly",
        "weekly",
        "daily",
        "quarterly",
        "yearly",
        "caused",
        "driving",
        "affect",
        "which ",
    )
    if any(marker in normal for marker in complex_markers):
        return None
    metrics = [name for name in registry.metrics if _mentioned(question, name)]
    if not metrics:
        return None
    if len(metrics) != 1:
        return MetricQuestionMapping(
            None, confident=False, explanation="more than one metric was named"
        )
    metric = metrics[0]
    dimensions = [name for name in registry.dimensions_for(metric) if _mentioned(question, name)]
    # A named grouping that is unsupported must never silently become an
    # ungrouped total.  Limit this check to common grouping phrases.
    grouping = re.search(
        r"\b(?:by|per|across|group(?:ed)? by)\s+([a-z0-9_ ]{2,40})", question, re.I
    )
    if grouping and not dimensions and _time_grain(question) is None:
        phrase = grouping.group(1).strip().split()[0]
        return MetricQuestionMapping(
            metric,
            confident=False,
            explanation=f"the metric does not define a grouping named {phrase!r}",
        )
    return MetricQuestionMapping(
        metric,
        tuple(dimensions),
        _time_grain(question),
        named_metrics=[metric],
        named_dimensions=dimensions,
        metric_format=registry.metric(metric).format,
    )
