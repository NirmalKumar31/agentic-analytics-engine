"""The semantic metric layer.

A metric is an aggregate expression bound to a model. Agents name a metric;
this module produces the SQL. The point is that "revenue" is defined once, so
two analysis tasks that both report revenue cannot disagree about what it
means, and a reviewer can read the definition instead of re-reading generated
SQL.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

METRICS_PATH = Path(__file__).resolve().parent / "metrics.yml"

TimeGrain = Literal["day", "week", "month", "quarter", "year"]
VALID_GRAINS: frozenset[str] = frozenset({"day", "week", "month", "quarter", "year"})

MetricFormat = Literal["currency", "percent", "integer", "number", "ratio"]


class ModelDef(BaseModel):
    """A named relation that metrics aggregate over."""

    name: str
    description: str
    sql: str
    time_field: str
    # Dimension name as an agent sees it -> column in the model relation.
    dimensions: dict[str, str] = Field(default_factory=dict)


class MetricDef(BaseModel):
    """A single business metric."""

    name: str
    model: str
    description: str
    sql: str
    format: MetricFormat = "number"

    def public(self, dimensions: list[str], time_field: str) -> dict[str, object]:
        """The view of a metric that is safe to put in a prompt."""
        return {
            "name": self.name,
            "description": self.description.strip(),
            "expression": " ".join(self.sql.split()),
            "valid_dimensions": dimensions,
            "time_field": time_field,
            "format": self.format,
        }


class MetricRegistry(BaseModel):
    """Loaded metric and model definitions."""

    models: dict[str, ModelDef]
    metrics: dict[str, MetricDef]

    def metric(self, name: str) -> MetricDef:
        try:
            return self.metrics[name]
        except KeyError:
            raise KeyError(f"unknown metric {name!r}; available: {sorted(self.metrics)}") from None

    def model_for(self, metric_name: str) -> ModelDef:
        return self.models[self.metric(metric_name).model]

    def dimensions_for(self, metric_name: str) -> list[str]:
        return sorted(self.model_for(metric_name).dimensions)

    def describe_all(self) -> list[dict[str, object]]:
        """Every metric, in the shape the MCP `list_metrics` tool returns."""
        return [
            m.public(self.dimensions_for(name), self.model_for(name).time_field)
            for name, m in sorted(self.metrics.items())
        ]

    def resolve_dimension(self, metric_name: str, dimension: str) -> str:
        """Map an agent-facing dimension name to its column, or raise."""
        model = self.model_for(metric_name)
        if dimension not in model.dimensions:
            raise KeyError(
                f"metric {metric_name!r} does not support dimension {dimension!r}; "
                f"valid dimensions: {sorted(model.dimensions)}"
            )
        return model.dimensions[dimension]

    def filterable_columns(self, metric_name: str) -> set[str]:
        """Columns a filter may reference: the dimensions plus the time field.

        Deliberately narrow. Filters arrive from a model, and allowing an
        arbitrary column name here would widen the SQL surface that
        `SQLGuard` then has to defend.
        """
        model = self.model_for(metric_name)
        return set(model.dimensions) | {model.time_field}


def load_registry(path: Path | None = None) -> MetricRegistry:
    """Parse ``metrics.yml`` into a validated registry."""
    raw = yaml.safe_load((path or METRICS_PATH).read_text())
    models = {name: ModelDef(name=name, **body) for name, body in raw.get("models", {}).items()}
    metrics = {name: MetricDef(name=name, **body) for name, body in raw.get("metrics", {}).items()}
    for metric in metrics.values():
        if metric.model not in models:
            raise ValueError(f"metric {metric.name!r} references unknown model {metric.model!r}")
    return MetricRegistry(models=models, metrics=metrics)


def grain_expression(time_field: str, grain: str) -> str:
    """``date_trunc`` for a validated grain.

    ``grain`` is checked against a closed set rather than interpolated, so a
    grain value can never carry SQL.
    """
    if grain not in VALID_GRAINS:
        raise ValueError(f"invalid time_grain {grain!r}; valid: {sorted(VALID_GRAINS)}")
    return f"date_trunc('{grain}', {time_field})"
