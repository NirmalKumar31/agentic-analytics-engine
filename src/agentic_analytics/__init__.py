"""Agentic Analytics Engine.

A bounded multi-agent analytics workflow: a question is decomposed into
analytical tasks, the tasks run deterministic DuckDB analytics through an MCP
tool layer, and every number that reaches the report carries a traceable path
back to the query result that produced it.
"""

from __future__ import annotations

import os

__version__ = "0.1.0"


def build_sha() -> str:
    """Immutable deployed revision, or ``unknown`` outside a built release."""
    value = os.getenv("AAE_BUILD_SHA") or os.getenv("RENDER_GIT_COMMIT") or "unknown"
    return value.strip() or "unknown"
