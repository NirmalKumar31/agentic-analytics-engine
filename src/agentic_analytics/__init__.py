"""Agentic Analytics Engine.

A bounded multi-agent analytics workflow: a question is decomposed into
analytical tasks, the tasks run deterministic DuckDB analytics through an MCP
tool layer, and every number that reaches the report carries a traceable path
back to the query result that produced it.
"""

__version__ = "0.1.0"
