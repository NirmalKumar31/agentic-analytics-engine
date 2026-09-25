"""One definition of "read-only SQL", shared by every caller that asks.

Runtime safety has always used :func:`agentic_analytics.warehouse.sqlguard.check_sql`
-- an AST walk over the parsed statement. The benchmark and the recording
validator used to ask a much weaker question instead: does the text start with
`select` or `with`?

That gap mattered because it was the *reporting* path. A run could execute
something the guard would have refused and still be scored as "100% read-only
SQL", because the scorer was checking a prefix. Both now call the guard, so a
figure that says read-only means the same thing everywhere.
"""

from __future__ import annotations

from agentic_analytics.data.generator import TABLE_NAMES
from agentic_analytics.warehouse.sqlguard import SQLGuardError, check_sql

#: Generous compared with the runtime ceiling. A governed metric query the
#: engine composed can be long, and this check is about what the statement
#: *does*, not how much a model was allowed to write.
MAX_AUDIT_LENGTH = 32_000


def is_read_only(sql: str, allowed_tables: set[str] | None = None) -> tuple[bool, str]:
    """Whether the real guard accepts this statement, and why not if it does not.

    `allowed_tables` defaults to the built-in demo warehouse, which is what
    the benchmark and the committed recordings run against. A caller with a
    different dataset passes its own table names.
    """
    tables = allowed_tables if allowed_tables is not None else {t.lower() for t in TABLE_NAMES}
    try:
        check_sql(sql, allowed_tables={t.lower() for t in tables}, max_length=MAX_AUDIT_LENGTH)
    except SQLGuardError as exc:
        return False, str(exc)
    return True, ""
