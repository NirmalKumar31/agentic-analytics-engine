"""Deterministic presentation of a verified analysis.

See `schemas.py` for why this layer exists: the frontend was recovering
analytical structure from prose, and every guess it had to make was wrong
somewhere.
"""

from agentic_analytics.presentation.build import build_presentation, display_fields_for
from agentic_analytics.presentation.fields import display_field_for, humanize, label_value
from agentic_analytics.presentation.schemas import (
    PRESENTATION_SCHEMA_VERSION,
    AnalysisPresentation,
    CaveatSeverity,
    DisplayField,
    InterpretationLevel,
    PresentationCaveat,
    PresentationChart,
    PresentationHighlight,
    PresentationProvenanceRef,
    PresentationScope,
    PresentationShape,
    PresentationTable,
    PresentationValue,
    SemanticKind,
)
from agentic_analytics.presentation.summarize import detect_shape, numbers_resolve

__all__ = [
    "PRESENTATION_SCHEMA_VERSION",
    "AnalysisPresentation",
    "CaveatSeverity",
    "DisplayField",
    "InterpretationLevel",
    "PresentationCaveat",
    "PresentationChart",
    "PresentationHighlight",
    "PresentationProvenanceRef",
    "PresentationScope",
    "PresentationShape",
    "PresentationTable",
    "PresentationValue",
    "SemanticKind",
    "build_presentation",
    "detect_shape",
    "display_field_for",
    "display_fields_for",
    "humanize",
    "label_value",
    "numbers_resolve",
]
