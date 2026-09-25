"""Read-only SQL enforcement.

The model never reaches DuckDB directly. Every statement passes through here
first, and the guard works on a parsed AST rather than on string matching:
regex screening is defeated by comments, casing, whitespace and string
literals, and it cannot tell `read_csv` in a FROM clause from the characters
`read_csv` inside a quoted string.

This is the outer of two layers. The inner layer is the DuckDB connection
itself, which runs with `enable_external_access=false` and
`lock_configuration=true` so that filesystem and network access are refused by
the engine even if a statement somehow gets past the parser.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

DIALECT = "duckdb"

# sqlglot logs a warning and falls back to a generic `Command` node for syntax
# it does not model. That fallback is exactly how this guard detects
# non-query statements, so the warning is expected rather than notable -- and
# it would otherwise echo the rejected statement into the application log.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

# Only these top-level statement types are analytical reads.
ALLOWED_ROOTS: tuple[type[exp.Expression], ...] = (exp.Select, exp.Union, exp.Except, exp.Intersect)

# Any node type here is a write, a DDL, or a side effect.
FORBIDDEN_NODES: dict[type[exp.Expression], str] = {
    exp.Insert: "INSERT",
    exp.Update: "UPDATE",
    exp.Delete: "DELETE",
    exp.Drop: "DROP",
    exp.Alter: "ALTER",
    exp.Create: "CREATE",
    exp.Copy: "COPY",
    exp.Attach: "ATTACH",
    exp.Detach: "DETACH",
    exp.Command: "non-query command",
    exp.Set: "SET",
    exp.Use: "USE",
    exp.Grant: "GRANT",
    exp.Merge: "MERGE",
    exp.Pragma: "PRAGMA",
    exp.Transaction: "transaction control",
    exp.Commit: "COMMIT",
    exp.Rollback: "ROLLBACK",
}

# Table functions that read from outside the database. `run_readonly_sql`
# exists to query tables the session already loaded, so none of these have a
# legitimate use on an agent-authored statement.
FORBIDDEN_FUNCTIONS: frozenset[str] = frozenset(
    {
        "read_csv",
        "read_csv_auto",
        "read_parquet",
        "read_json",
        "read_json_auto",
        "read_ndjson",
        "read_ndjson_auto",
        "read_text",
        "read_blob",
        "read_xlsx",
        "parquet_scan",
        "csv_scan",
        "json_scan",
        "iceberg_scan",
        "delta_scan",
        "sniff_csv",
        "glob",
        "sqlite_scan",
        "postgres_scan",
        "postgres_query",
        "mysql_scan",
        "mysql_query",
        "duckdb_extensions",
        "install",
        "load",
        "sqlite_attach",
        "shapefile_scan",
        "st_read",
        "st_readosm",
        "duckdb_settings",
        "duckdb_secrets",
        "gsheet",
        "iceberg_metadata",
        "parquet_metadata",
        "parquet_file_metadata",
        "parquet_kv_metadata",
        "parquet_schema",
        "arrow_scan",
        "arrow_scan_dumb",
        "getenv",
        "which_secret",
        "duckdb_functions",
    }
)

# Table functions that generate rows from nothing. Useful for a date spine,
# and the classic way to build a query that is read-only and still burns a
# core for as long as it is allowed to: `SELECT count(*) FROM range(1e12)`.
# Permitted, with their literal arguments bounded.
GENERATOR_FUNCTIONS: frozenset[str] = frozenset({"range", "generate_series"})
MAX_GENERATED_ROWS = 10_000_000

# Structural ceilings. A query can be read-only, reference only real tables,
# and still be a denial of service: a dozen cross joins, a thousand-deep
# expression tree, or a recursive CTE with no termination. The engine-level
# timeout is the backstop; these reject the obvious cases before any work
# starts.
MAX_AST_NODES = 4_000
MAX_AST_DEPTH = 60
MAX_JOINS = 12
MAX_CTES = 24
MAX_SET_OPERATIONS = 24
MAX_SUBQUERIES = 40

# Statement separators are rejected before parsing multiple statements.
_URI_RE = re.compile(r"\b(?:https?|ftp|s3|gcs|gs|az|azure|abfss?|hf|file)://", re.IGNORECASE)


class SQLGuardError(ValueError):
    """A statement was rejected. The message is safe to show a user."""


@dataclass
class GuardResult:
    """Outcome of a successful check."""

    sql: str
    tables: list[str] = field(default_factory=list)


def _root_kind(node: exp.Expression) -> str:
    return type(node).__name__.upper()


def check_sql(sql: str, *, allowed_tables: set[str], max_length: int = 8000) -> GuardResult:
    """Validate a statement, or raise :class:`SQLGuardError`.

    Returns the normalised statement. Callers execute the returned SQL rather
    than the input, so what was validated is what runs.
    """
    if not sql or not sql.strip():
        raise SQLGuardError("empty SQL statement")
    if len(sql) > max_length:
        raise SQLGuardError(f"SQL exceeds the {max_length} character limit")
    if "\x00" in sql:
        raise SQLGuardError("SQL contains a null byte")

    try:
        parsed = sqlglot.parse(sql, read=DIALECT)
    except sqlglot.ParseError as exc:
        raise SQLGuardError(f"SQL failed to parse: {exc}") from None
    except RecursionError:
        # sqlglot parses by recursive descent, so a deeply nested expression
        # exhausts the Python stack during parsing -- before any structural
        # check could run. Catching it here turns a crash into a refusal.
        raise SQLGuardError("the query nests too deeply to parse; simplify it") from None

    statements: list[exp.Expression] = [s for s in parsed if isinstance(s, exp.Expression)]
    if not statements:
        raise SQLGuardError("no statement found")
    if len(statements) > 1:
        raise SQLGuardError("only a single statement is allowed")

    root: exp.Expression = statements[0]
    # `WITH ... SELECT` parses as the Select carrying a `with` argument, so
    # unwrapping a subquery root is enough to reach the real statement kind.
    if isinstance(root, exp.Subquery) and isinstance(root.this, exp.Expression):
        root = root.this

    if not isinstance(root, ALLOWED_ROOTS):
        kind = FORBIDDEN_NODES.get(type(root), _root_kind(root))
        raise SQLGuardError(f"{kind} is not permitted; only SELECT and WITH...SELECT are allowed")

    for node_type, label in FORBIDDEN_NODES.items():
        if list(root.find_all(node_type)):
            raise SQLGuardError(f"{label} is not permitted in an analytical query")

    # A CTE name is a reference to a relation defined inside this same
    # statement, so it is not a table the guard should be checking against
    # the session catalogue.
    cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE) if cte.alias_or_name}

    tables: list[str] = []
    for table in root.find_all(exp.Table):
        name = table.name
        if not name:
            continue
        if table.catalog:
            raise SQLGuardError(f"cross-database reference {table.catalog}.{name} is not permitted")
        tables.append(name)

    for anon in root.find_all(exp.Anonymous):
        anon_name = anon.this.lower() if isinstance(anon.this, str) else ""
        if anon_name in FORBIDDEN_FUNCTIONS:
            raise SQLGuardError(f"function {anon_name}() is not permitted")

    # sqlglot promotes some readers to typed nodes rather than Anonymous, so
    # the name check is repeated over every function node.
    for func in root.find_all(exp.Func):
        func_name = _func_name(func)
        if func_name and func_name.lower() in FORBIDDEN_FUNCTIONS:
            raise SQLGuardError(f"function {func_name.lower()}() is not permitted")

    for literal in root.find_all(exp.Literal):
        if literal.is_string and _URI_RE.search(str(literal.this) or ""):
            raise SQLGuardError("remote URLs are not permitted in a query")

    _check_complexity(root)
    _check_generators(root)

    unknown = sorted(
        {t for t in tables if t.lower() not in allowed_tables and t.lower() not in cte_names}
    )
    if unknown:
        raise SQLGuardError(
            f"unknown table(s): {', '.join(unknown)}; available: {sorted(allowed_tables)}"
        )

    real_tables = sorted({t for t in tables if t.lower() not in cte_names})
    return GuardResult(sql=root.sql(dialect=DIALECT), tables=real_tables)


def _depth(node: exp.Expression, limit: int) -> int:
    """AST depth, abandoning the walk once the limit is passed."""
    best = 0
    for child in node.args.values():
        children = child if isinstance(child, list) else [child]
        for item in children:
            if isinstance(item, exp.Expression):
                best = max(best, _depth(item, limit))
                if best >= limit:
                    return best
    return best + 1


def _check_complexity(root: exp.Expression) -> None:
    """Reject a query whose shape alone would be expensive."""
    for nodes, _ in enumerate(root.walk(), start=1):
        if nodes > MAX_AST_NODES:
            raise SQLGuardError(
                f"the query has more than {MAX_AST_NODES} syntax nodes; simplify it"
            )

    if _depth(root, MAX_AST_DEPTH + 1) > MAX_AST_DEPTH:
        raise SQLGuardError(f"the query nests deeper than {MAX_AST_DEPTH} levels")

    joins = len(list(root.find_all(exp.Join)))
    if joins > MAX_JOINS:
        raise SQLGuardError(f"the query has {joins} joins; the limit is {MAX_JOINS}")

    ctes = list(root.find_all(exp.CTE))
    if len(ctes) > MAX_CTES:
        raise SQLGuardError(f"the query has {len(ctes)} CTEs; the limit is {MAX_CTES}")

    # A recursive CTE has no bound this guard can check statically, and the
    # analytical tools never need one. Refusing it removes an unbounded
    # execution path rather than relying on the timeout to catch it.
    for with_clause in root.find_all(exp.With):
        if with_clause.args.get("recursive"):
            raise SQLGuardError(
                "recursive CTEs are not permitted; they have no statically checkable bound"
            )

    set_ops = sum(len(list(root.find_all(kind))) for kind in (exp.Union, exp.Except, exp.Intersect))
    if set_ops > MAX_SET_OPERATIONS:
        raise SQLGuardError(
            f"the query has {set_ops} set operations; the limit is {MAX_SET_OPERATIONS}"
        )

    subqueries = len(list(root.find_all(exp.Subquery)))
    if subqueries > MAX_SUBQUERIES:
        raise SQLGuardError(f"the query has {subqueries} subqueries; the limit is {MAX_SUBQUERIES}")

    # A cross join with no predicate multiplies row counts. One is a
    # legitimate way to pair two aggregates; several is a row explosion.
    cross_joins = [
        join
        for join in root.find_all(exp.Join)
        if not join.args.get("on") and not join.args.get("using")
    ]
    if len(cross_joins) > 2:
        raise SQLGuardError(
            f"the query has {len(cross_joins)} unrestricted joins; add join conditions"
        )


def _literal_number(node: exp.Expression) -> float | None:
    """The numeric value of a literal, or None if it is not one.

    Handles a negated literal, which parses as `Neg(Literal)`.
    """
    if isinstance(node, exp.Neg):
        inner = _literal_number(node.this) if isinstance(node.this, exp.Expression) else None
        return None if inner is None else -inner
    if isinstance(node, exp.Literal) and not node.is_string:
        try:
            return float(str(node.this))
        except (TypeError, ValueError):
            return None
    return None


def _check_generators(root: exp.Expression) -> None:
    """Bound the row count a generator function may be asked to produce.

    Every argument must be a numeric literal. sqlglot rewrites `range(n)` to
    `GENERATE_SERIES(0, n)`, so taking the largest literal it can find would
    read the implicit zero and pass a call whose real bound is a subquery.
    """
    for func in root.find_all(exp.Func):
        name = (_func_name(func) or "").lower()
        if name not in GENERATOR_FUNCTIONS:
            continue

        arguments = [value for value in func.args.values() if isinstance(value, exp.Expression)]
        if not arguments:
            raise SQLGuardError(f"{name}() must be called with literal bounds")

        bound = 0.0
        for argument in arguments:
            value = _literal_number(argument)
            if value is None:
                raise SQLGuardError(
                    f"{name}() must be called with literal bounds so its size is "
                    "known before the query runs"
                )
            bound = max(bound, abs(value))

        if bound > MAX_GENERATED_ROWS:
            raise SQLGuardError(
                f"{name}() would generate up to {bound:,.0f} rows; "
                f"the limit is {MAX_GENERATED_ROWS:,}"
            )


def _func_name(func: exp.Func) -> str | None:
    """Best-effort name for a function node across sqlglot node shapes."""
    name = getattr(func, "sql_name", None)
    if callable(name):
        try:
            return str(func.sql_name())
        except Exception:
            return None
    this = func.this
    return this if isinstance(this, str) else None
