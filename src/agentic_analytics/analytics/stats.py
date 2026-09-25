"""Statistical tests and correlation.

Every number a test reports -- statistic, p-value, effect size, confidence
interval, sample sizes -- is computed here by SciPy or by explicit arithmetic.
A model chooses the test and the variables; it never supplies a result. This
is the difference between "the agent says the difference is significant" and
"a two-proportion z-test on n=26,809 and n=3,191 returned p=...".
"""

from __future__ import annotations

import math
from typing import Any, Literal

import numpy as np
from scipy import stats as sps

from agentic_analytics.analytics.execute import fetch_rows, run_query
from agentic_analytics.analytics.filters import Filter, build_where
from agentic_analytics.analytics.results import ResultSnapshot, StatisticalResult
from agentic_analytics.warehouse.session import AnalysisSession

TestType = Literal[
    "two_proportion_z",
    "welch_t_test",
    "one_way_anova",
    "chi_square",
    "pearson_correlation",
    "spearman_correlation",
]

TEST_TYPES: tuple[str, ...] = (
    "two_proportion_z",
    "welch_t_test",
    "one_way_anova",
    "chi_square",
    "pearson_correlation",
    "spearman_correlation",
)

# Tests that need row-level values are capped. Beyond this a seeded reservoir
# sample is drawn, and the snapshot records that it happened along with the
# seed, so the same request against the same data reproduces exactly.
MAX_RAW_ROWS = 50_000
SAMPLE_SEED = 20260924
SAMPLE_METHOD = "duckdb reservoir, seeded"

# Column types each test can accept. A model chooses the test; this decides
# whether the choice is applicable to the data, because running an
# inapplicable test produces a number that looks like evidence and is not.
NUMERIC_DUCK_TYPES = frozenset(
    {
        "TINYINT",
        "SMALLINT",
        "INTEGER",
        "BIGINT",
        "HUGEINT",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
        "UBIGINT",
        "FLOAT",
        "DOUBLE",
        "REAL",
        "DECIMAL",
        "BOOLEAN",
    }
)
MIN_GROUP_SIZE = 2
MIN_CORRELATION_PAIRS = 10
MAX_GROUPS = 12
MAX_CORRELATION_COLUMNS = 8


class StatsError(ValueError):
    """The test request was not valid. Message is safe to show a user."""


def _relation(session: AnalysisSession, name: str) -> tuple[str, set[str], dict[str, str]]:
    """Resolve a model or table name to (sql, filterable columns, column map)."""
    reg = session.registry
    if reg is not None and name in reg.models:
        model = reg.models[name]
        columns = set(model.dimensions) | {model.time_field}
        return model.sql.strip(), columns, dict(model.dimensions)
    for table in session.tables:
        if table.lower() == name.lower():
            cols = {c["name"] for c in session.tables[table].columns}
            return f'SELECT * FROM "{table}"', cols, {}
    available = sorted(session.tables) + (sorted(reg.models) if reg else [])
    raise StatsError(f"unknown model or table {name!r}; available: {available}")


def _require(variables: dict[str, Any], key: str) -> str:
    value = variables.get(key)
    if not isinstance(value, str) or not value.strip():
        raise StatsError(f"variables.{key} is required and must be a column name")
    return value


def _relation_columns(session: AnalysisSession, relation_sql: str) -> dict[str, str]:
    """Column name to DuckDB type for a relation, without reading any rows.

    Holds the session lock. A DuckDB connection carries cursor state, so
    reading `description` without it returns whichever query a concurrent
    worker ran last -- which is how this function first reported the columns
    of an unrelated result.
    """
    try:
        with session.lock:
            cursor = session.con.execute(f"SELECT * FROM (\n{relation_sql}\n) AS r LIMIT 0")
            description = cursor.description or []
            return {str(d[0]): str(d[1]).split("(")[0].upper() for d in description}
    except Exception as exc:
        raise StatsError(f"the relation could not be inspected: {exc}") from None


def _check_column(column: str, relation_sql: str, session: AnalysisSession) -> str:
    """Confirm a column exists in the relation before it reaches SQL."""
    columns = _relation_columns(session, relation_sql)
    for name in columns:
        if name.lower() == column.lower():
            return name
    raise StatsError(f"column {column!r} not found; available columns: {sorted(columns)}")


def _require_numeric(session: AnalysisSession, relation_sql: str, column: str) -> str:
    """Resolve a column and refuse it if it is not numeric.

    A correlation between two text columns, or a t-test on a category label,
    is not a weaker result -- it is a meaningless one. Refusing is the only
    honest outcome.
    """
    resolved = _check_column(column, relation_sql, session)
    duck_type = _relation_columns(session, relation_sql)[resolved]
    if duck_type not in NUMERIC_DUCK_TYPES:
        raise StatsError(
            f"column {resolved!r} is {duck_type}, which this test cannot use; "
            "it requires a numeric column"
        )
    return resolved


def statistical_test(
    session: AnalysisSession,
    test_type: str,
    variables: dict[str, Any],
    filters: list[Filter] | None = None,
    *,
    task_id: str | None = None,
    confidence_level: float = 0.95,
    timeout_seconds: float = 20.0,
) -> ResultSnapshot:
    """Run one statistical test and snapshot both its table and its result."""
    if test_type not in TEST_TYPES:
        raise StatsError(f"unknown test_type {test_type!r}; valid: {list(TEST_TYPES)}")

    relation_name = variables.get("model") or variables.get("table")
    if not isinstance(relation_name, str):
        raise StatsError("variables.model (or variables.table) is required")
    relation_sql, filterable, column_map = _relation(session, relation_name)
    where, params = build_where(filters, filterable, column_map)
    from agentic_analytics.analytics.compute import _inline_params

    base = f"SELECT * FROM (\n{relation_sql}\n) AS r{_inline_params(where, params)}"

    handlers = {
        "two_proportion_z": _two_proportion_z,
        "welch_t_test": _welch_t_test,
        "one_way_anova": _one_way_anova,
        "chi_square": _chi_square,
        "pearson_correlation": _correlation,
        "spearman_correlation": _correlation,
    }
    columns, rows, result = handlers[test_type](
        session, base, variables, test_type, confidence_level, timeout_seconds
    )

    snapshot = ResultSnapshot(
        tool_name="statistical_test",
        task_id=task_id,
        sql=base,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        dataset_fingerprint=session.dataset_fingerprint,
        parameters={
            "test_type": test_type,
            "variables": variables,
            "filters": [f.model_dump() for f in (filters or [])],
        },
        warnings=list(result.warnings),
        statistical_result=result,
    )
    return session.results.put(snapshot)


def _reject_degenerate_groups(rows: list[tuple[str, int, float, float]], test_name: str) -> None:
    """Refuse a test whose groups are too small or hold a non-finite value.

    Note what this does *not* do, because the previous docstring implied
    otherwise: it does not reject a group whose values are all identical.
    A zero-variance group is not automatically degenerate -- Welch's t is
    perfectly well defined when one group is constant and the other is not.
    Whether a particular test is actually computable is decided where it is
    computed, by :func:`_require_finite`, which checks the statistic that
    came out rather than guessing from the inputs.
    """
    if not rows:
        raise StatsError(f"{test_name}: no rows remain after filtering")
    tiny = [name for name, n, *_ in rows if n < MIN_GROUP_SIZE]
    if tiny:
        raise StatsError(
            f"{test_name}: group(s) {', '.join(tiny[:4])} have fewer than "
            f"{MIN_GROUP_SIZE} observations"
        )
    for name, _n, _total, spread in rows:
        if not math.isfinite(spread):
            raise StatsError(f"{test_name}: group {name!r} contains a non-finite value")


def _require_finite(test_name: str, **values: float | None) -> None:
    """Refuse to publish a statistic that is not a number.

    NaN and infinity are what an undefined test returns, and storing one as
    evidence is worse than failing: it travels through the pipeline, gets
    cited by a finding, and renders in a report as though it meant
    something. Raising here turns "undefined" into a refusal the user sees.
    """
    bad = [
        name
        for name, value in values.items()
        if value is not None and not math.isfinite(float(value))
    ]
    if bad:
        raise StatsError(
            f"{test_name}: the test is undefined for these groups "
            f"({', '.join(sorted(bad))} is not finite)"
        )


def _group_counts(
    session: AnalysisSession, base: str, group_col: str, value_col: str, timeout: float
) -> list[tuple[str, int, float, float]]:
    sql = (
        f'SELECT CAST("{group_col}" AS VARCHAR) AS grp, COUNT(*) AS n, '
        f'SUM(CAST("{value_col}" AS DOUBLE)) AS total, '
        f'AVG(CAST("{value_col}" AS DOUBLE)) AS mean '
        f"FROM (\n{base}\n) AS b "
        f'WHERE "{group_col}" IS NOT NULL AND "{value_col}" IS NOT NULL '
        f"GROUP BY 1 ORDER BY 1"
    )
    _, rows = fetch_rows(session, sql, timeout)
    return [(str(r[0]), int(r[1]), float(r[2]), float(r[3])) for r in rows]


def _require_binary(session: AnalysisSession, base: str, column: str, timeout: float) -> None:
    """Refuse a value column that is not an indicator.

    This test sums the column and reads the sum as a count of successes.
    That is only true when every value is 0, 1, or boolean. A column holding
    2s and 5s produces a "rate" above 1, a z-statistic computed from it, and
    a p-value -- all of them meaningless, and none of them obviously wrong to
    a reader. Being numeric is not enough; it has to be binary.
    """
    sql = (
        f'SELECT COUNT(*) FROM (\n{base}\n) AS b WHERE "{column}" IS NOT NULL '
        f'AND CAST("{column}" AS DOUBLE) NOT IN (0, 1)'
    )
    _, rows = fetch_rows(session, sql, timeout)
    offending = int(rows[0][0]) if rows else 0
    if offending:
        sample_sql = (
            f'SELECT DISTINCT CAST("{column}" AS DOUBLE) FROM (\n{base}\n) AS b '
            f'WHERE "{column}" IS NOT NULL AND CAST("{column}" AS DOUBLE) NOT IN (0, 1) '
            f"LIMIT 3"
        )
        _, sample = fetch_rows(session, sample_sql, timeout)
        seen = ", ".join(str(r[0]) for r in sample)
        raise StatsError(
            f"two_proportion_z needs a 0/1 or boolean value column; {column!r} has "
            f"{offending:,} row(s) with other values (for example: {seen})"
        )


def _two_proportion_z(
    session: AnalysisSession,
    base: str,
    variables: dict[str, Any],
    _test: str,
    confidence_level: float,
    timeout: float,
) -> tuple[list[str], list[list[Any]], StatisticalResult]:
    """Compare two rates. ``value_column`` must be 0/1 or boolean."""
    group_col = _check_column(_require(variables, "group_column"), base, session)
    value_col = _require_numeric(session, base, _require(variables, "value_column"))
    _require_binary(session, base, value_col, timeout)
    groups = variables.get("groups")

    counts = _group_counts(session, base, group_col, value_col, timeout)
    _reject_degenerate_groups(counts, "two_proportion_z")
    if groups:
        wanted = [str(g) for g in groups]
        counts = [c for c in counts if c[0] in wanted]
    if len(counts) != 2:
        raise StatsError(
            f"two_proportion_z needs exactly two groups, found {len(counts)} "
            f"({[c[0] for c in counts]}); pass variables.groups to choose two"
        )

    (name_a, n_a, succ_a, _), (name_b, n_b, succ_b, _) = counts
    if min(n_a, n_b) == 0:
        raise StatsError("a group has no rows after filtering")
    p_a, p_b = succ_a / n_a, succ_b / n_b
    pooled = (succ_a + succ_b) / (n_a + n_b)
    se_pooled = math.sqrt(pooled * (1 - pooled) * (1 / n_a + 1 / n_b))
    if se_pooled == 0:
        raise StatsError("both groups have an identical constant rate; the test is undefined")
    z = (p_a - p_b) / se_pooled
    p_value = float(2 * sps.norm.sf(abs(z)))

    se_diff = math.sqrt(p_a * (1 - p_a) / n_a + p_b * (1 - p_b) / n_b)
    crit = float(sps.norm.ppf(0.5 + confidence_level / 2))
    diff = p_a - p_b
    ci = (diff - crit * se_diff, diff + crit * se_diff)
    # Cohen's h, the standard effect size for a difference of proportions.
    h = 2 * math.asin(math.sqrt(min(max(p_a, 0.0), 1.0))) - 2 * math.asin(
        math.sqrt(min(max(p_b, 0.0), 1.0))
    )

    warnings: list[str] = []
    for name, n, succ in ((name_a, n_a, succ_a), (name_b, n_b, succ_b)):
        if min(succ, n - succ) < 10:
            warnings.append(
                f"group {name!r} has fewer than 10 events or non-events; "
                "the normal approximation is unreliable"
            )
    warnings.append(
        "This is an association between groups, not evidence of causation; "
        "the groups were not randomly assigned."
    )

    result = StatisticalResult(
        test_name="two-proportion z-test",
        statistic=float(z),
        p_value=p_value,
        sample_sizes={name_a: n_a, name_b: n_b},
        effect_size=float(h),
        effect_size_name="Cohen's h",
        confidence_interval=(float(ci[0]), float(ci[1])),
        confidence_level=confidence_level,
        assumptions=[
            "independent observations within and between groups",
            "normal approximation to the binomial (large-sample)",
        ],
        warnings=warnings,
    )
    columns = ["group", "n", "successes", "rate"]
    rows: list[list[Any]] = [
        [name_a, n_a, succ_a, p_a],
        [name_b, n_b, succ_b, p_b],
    ]
    return columns, rows, result


def _group_stats(
    session: AnalysisSession, base: str, group_col: str, value_col: str, timeout: float
) -> list[tuple[str, int, float, float]]:
    sql = (
        f'SELECT CAST("{group_col}" AS VARCHAR) AS grp, COUNT(*) AS n, '
        f'AVG(CAST("{value_col}" AS DOUBLE)) AS mean, '
        f'STDDEV_SAMP(CAST("{value_col}" AS DOUBLE)) AS sd '
        f"FROM (\n{base}\n) AS b "
        f'WHERE "{group_col}" IS NOT NULL AND "{value_col}" IS NOT NULL '
        f"GROUP BY 1 ORDER BY 1"
    )
    _, rows = fetch_rows(session, sql, timeout)
    return [(str(r[0]), int(r[1]), float(r[2]), float(r[3] or 0.0)) for r in rows]


def _welch_t_test(
    session: AnalysisSession,
    base: str,
    variables: dict[str, Any],
    _test: str,
    confidence_level: float,
    timeout: float,
) -> tuple[list[str], list[list[Any]], StatisticalResult]:
    """Compare two means without assuming equal variance.

    Computed from group n/mean/sd so the test costs one aggregate query
    regardless of table size.
    """
    group_col = _check_column(_require(variables, "group_column"), base, session)
    value_col = _require_numeric(session, base, _require(variables, "value_column"))
    groups = variables.get("groups")

    stats_rows = _group_stats(session, base, group_col, value_col, timeout)
    _reject_degenerate_groups(stats_rows, "welch_t_test")
    if groups:
        wanted = [str(g) for g in groups]
        stats_rows = [s for s in stats_rows if s[0] in wanted]
    if len(stats_rows) != 2:
        raise StatsError(
            f"welch_t_test needs exactly two groups, found {len(stats_rows)} "
            f"({[s[0] for s in stats_rows]}); pass variables.groups to choose two"
        )

    (name_a, n_a, mean_a, sd_a), (name_b, n_b, mean_b, sd_b) = stats_rows
    if min(n_a, n_b) < 2:
        raise StatsError("each group needs at least two rows")
    res = sps.ttest_ind_from_stats(
        mean1=mean_a,
        std1=sd_a,
        nobs1=n_a,
        mean2=mean_b,
        std2=sd_b,
        nobs2=n_b,
        equal_var=False,
    )
    se = math.sqrt(sd_a**2 / n_a + sd_b**2 / n_b)
    if se <= 0:
        # Both groups constant: there is no sampling variability to compare
        # the difference against, so the statistic is undefined rather than
        # very large.
        raise StatsError(
            "welch_t_test: both groups are constant, so the standard error is "
            "zero and the test is undefined"
        )
    # Welch-Satterthwaite degrees of freedom.
    denom = (sd_a**2 / n_a) ** 2 / (n_a - 1) + (sd_b**2 / n_b) ** 2 / (n_b - 1)
    df = ((sd_a**2 / n_a + sd_b**2 / n_b) ** 2 / denom) if denom > 0 else float(n_a + n_b - 2)
    crit = float(sps.t.ppf(0.5 + confidence_level / 2, df)) if df > 0 else 0.0
    diff = mean_a - mean_b
    pooled_sd = math.sqrt(((n_a - 1) * sd_a**2 + (n_b - 1) * sd_b**2) / (n_a + n_b - 2))
    d = diff / pooled_sd if pooled_sd > 0 else 0.0

    # Everything that reaches the snapshot has to be a real number. One
    # group with zero variance is fine and reaches here; two are not, and
    # were caught above.
    _require_finite(
        "welch_t_test",
        statistic=float(res.statistic),
        p_value=float(res.pvalue),
        standard_error=se,
        degrees_of_freedom=df,
        lower_bound=diff - crit * se,
        upper_bound=diff + crit * se,
        effect_size=d,
    )

    result = StatisticalResult(
        test_name="Welch's t-test",
        statistic=float(res.statistic),
        p_value=float(res.pvalue),
        sample_sizes={name_a: n_a, name_b: n_b},
        effect_size=float(d),
        effect_size_name="Cohen's d",
        confidence_interval=(float(diff - crit * se), float(diff + crit * se)),
        confidence_level=confidence_level,
        assumptions=[
            "independent observations",
            "approximately normal group means (satisfied at large n by the CLT)",
            "unequal variances permitted",
        ],
        warnings=[
            "This is an association between groups, not evidence of causation; "
            "the groups were not randomly assigned."
        ],
    )
    columns = ["group", "n", "mean", "sd"]
    rows: list[list[Any]] = [
        [name_a, n_a, mean_a, sd_a],
        [name_b, n_b, mean_b, sd_b],
    ]
    return columns, rows, result


def _one_way_anova(
    session: AnalysisSession,
    base: str,
    variables: dict[str, Any],
    _test: str,
    confidence_level: float,
    timeout: float,
) -> tuple[list[str], list[list[Any]], StatisticalResult]:
    """Compare means across three or more groups."""
    group_col = _check_column(_require(variables, "group_column"), base, session)
    value_col = _require_numeric(session, base, _require(variables, "value_column"))

    stats_rows = _group_stats(session, base, group_col, value_col, timeout)
    _reject_degenerate_groups(stats_rows, "one_way_anova")
    if len(stats_rows) < 3:
        raise StatsError(
            f"one_way_anova needs at least three groups, found {len(stats_rows)}; "
            "use welch_t_test for two"
        )
    if len(stats_rows) > MAX_GROUPS:
        raise StatsError(f"{len(stats_rows)} groups exceeds the limit of {MAX_GROUPS}")

    names = [s[0] for s in stats_rows]
    ns = np.array([s[1] for s in stats_rows], dtype=float)
    means = np.array([s[2] for s in stats_rows], dtype=float)
    sds = np.array([s[3] for s in stats_rows], dtype=float)
    if (ns < 2).any():
        raise StatsError("each group needs at least two rows")

    grand = float((ns * means).sum() / ns.sum())
    ss_between = float((ns * (means - grand) ** 2).sum())
    ss_within = float(((ns - 1) * sds**2).sum())
    df_between = len(ns) - 1
    df_within = float(ns.sum() - len(ns))
    if ss_within <= 0 or df_within <= 0:
        raise StatsError("within-group variance is zero; the F test is undefined")
    f_stat = (ss_between / df_between) / (ss_within / df_within)
    p_value = float(sps.f.sf(f_stat, df_between, df_within))
    eta_sq = ss_between / (ss_between + ss_within)

    result = StatisticalResult(
        test_name="one-way ANOVA",
        statistic=float(f_stat),
        p_value=p_value,
        sample_sizes=dict(zip(names, [int(n) for n in ns], strict=True)),
        effect_size=float(eta_sq),
        effect_size_name="eta squared",
        confidence_level=confidence_level,
        assumptions=[
            "independent observations",
            "approximately normal group means",
            "similar group variances",
        ],
        warnings=[
            "A significant F only says at least one group differs; it does not "
            "identify which, and it is not evidence of causation."
        ],
    )
    columns = ["group", "n", "mean", "sd"]
    rows: list[list[Any]] = [[n, int(c), m, s] for n, c, m, s in stats_rows]
    return columns, rows, result


def _chi_square(
    session: AnalysisSession,
    base: str,
    variables: dict[str, Any],
    _test: str,
    confidence_level: float,
    timeout: float,
) -> tuple[list[str], list[list[Any]], StatisticalResult]:
    """Test independence of two categorical columns from a contingency table."""
    row_col = _check_column(_require(variables, "row_column"), base, session)
    col_col = _check_column(_require(variables, "column_column"), base, session)

    sql = (
        f'SELECT CAST("{row_col}" AS VARCHAR) AS r, CAST("{col_col}" AS VARCHAR) AS c, '
        f"COUNT(*) AS n FROM (\n{base}\n) AS b "
        f'WHERE "{row_col}" IS NOT NULL AND "{col_col}" IS NOT NULL '
        f"GROUP BY 1, 2 ORDER BY 1, 2"
    )
    _, raw = fetch_rows(session, sql, timeout)
    if not raw:
        raise StatsError("no rows available for the contingency table")

    row_levels = sorted({str(r[0]) for r in raw})
    col_levels = sorted({str(r[1]) for r in raw})
    if len(row_levels) > MAX_GROUPS or len(col_levels) > MAX_GROUPS:
        raise StatsError(
            f"contingency table is {len(row_levels)}x{len(col_levels)}; "
            f"each side is limited to {MAX_GROUPS} levels"
        )
    if len(row_levels) < 2 or len(col_levels) < 2:
        raise StatsError("a chi-square test needs at least two levels on each side")

    table = np.zeros((len(row_levels), len(col_levels)), dtype=float)
    for r, c, n in raw:
        table[row_levels.index(str(r)), col_levels.index(str(c))] = float(n)

    chi2, p_value, _dof, expected = sps.chi2_contingency(table)
    n_total = float(table.sum())
    min_dim = min(table.shape) - 1
    cramers_v = math.sqrt(chi2 / (n_total * min_dim)) if min_dim > 0 and n_total else 0.0

    warnings: list[str] = []
    low = int((expected < 5).sum())
    if low:
        warnings.append(
            f"{low} cell(s) have an expected count below 5; the chi-square "
            "approximation is unreliable there"
        )
    warnings.append("Association between categories is not evidence of causation.")

    result = StatisticalResult(
        test_name="chi-square test of independence",
        statistic=float(chi2),
        p_value=float(p_value),
        sample_sizes={"total": int(n_total)},
        effect_size=float(cramers_v),
        effect_size_name="Cramer's V",
        confidence_level=confidence_level,
        assumptions=["independent observations", "expected cell counts of at least 5"],
        warnings=warnings,
    )
    columns = [row_col, *col_levels]
    rows: list[list[Any]] = [
        [row_levels[i], *[int(v) for v in table[i]]] for i in range(len(row_levels))
    ]
    return columns, rows, result


def _correlation(
    session: AnalysisSession,
    base: str,
    variables: dict[str, Any],
    test_type: str,
    confidence_level: float,
    timeout: float,
) -> tuple[list[str], list[list[Any]], StatisticalResult]:
    """Pearson or Spearman correlation between two numeric columns."""
    x_col = _require_numeric(session, base, _require(variables, "x_column"))
    y_col = _require_numeric(session, base, _require(variables, "y_column"))

    pairs_sql = (
        f'SELECT CAST("{x_col}" AS DOUBLE) AS x, CAST("{y_col}" AS DOUBLE) AS y '
        f"FROM (\n{base}\n) AS b "
        f'WHERE "{x_col}" IS NOT NULL AND "{y_col}" IS NOT NULL'
    )
    _, count_rows = fetch_rows(session, f"SELECT COUNT(*) FROM (\n{pairs_sql}\n) AS p", timeout)
    available = int(count_rows[0][0]) if count_rows else 0
    sampled = available > MAX_RAW_ROWS

    if sampled:
        # A seeded reservoir sample, so the same request against the same
        # data returns the same coefficient. An unordered LIMIT would not:
        # DuckDB makes no promise about which rows it returns first.
        sql = (
            f"SELECT * FROM (\n{pairs_sql}\n) AS p "
            f"USING SAMPLE {MAX_RAW_ROWS} ROWS (reservoir, {SAMPLE_SEED})"
        )
    else:
        sql = pairs_sql

    _, raw = fetch_rows(session, sql, timeout)
    if len(raw) < MIN_CORRELATION_PAIRS:
        raise StatsError(
            f"at least {MIN_CORRELATION_PAIRS} paired observations are required, found {len(raw)}"
        )

    x = np.array([float(r[0]) for r in raw])
    y = np.array([float(r[1]) for r in raw])
    if not (np.isfinite(x).all() and np.isfinite(y).all()):
        finite = np.isfinite(x) & np.isfinite(y)
        if finite.sum() < MIN_CORRELATION_PAIRS:
            raise StatsError("too few finite observations remain after removing NaN and infinity")
        x, y = x[finite], y[finite]
    if x.std() == 0 or y.std() == 0:
        raise StatsError("a variable is constant; correlation is undefined")

    if test_type == "spearman_correlation":
        res = sps.spearmanr(x, y)
        r, p_value = float(res.statistic), float(res.pvalue)
        name, effect_name = "Spearman rank correlation", "rho"
    else:
        res = sps.pearsonr(x, y)
        r, p_value = float(res.statistic), float(res.pvalue)
        name, effect_name = "Pearson correlation", "r"

    n = len(x)
    ci: tuple[float, float] | None = None
    if n > 3 and abs(r) < 1.0:
        # Fisher z transform.
        z = math.atanh(r)
        se = 1.0 / math.sqrt(n - 3)
        crit = float(sps.norm.ppf(0.5 + confidence_level / 2))
        ci = (math.tanh(z - crit * se), math.tanh(z + crit * se))

    warnings = [
        "Correlation does not establish causation, and this estimate does not "
        "adjust for any confounding variable."
    ]
    if sampled:
        warnings.append(
            f"computed on a seeded random sample of {len(x):,} of {available:,} rows; "
            "the same request reproduces the same sample"
        )

    result = StatisticalResult(
        test_name=name,
        statistic=r,
        p_value=p_value,
        sample_sizes={"pairs": n},
        effect_size=r,
        effect_size_name=effect_name,
        confidence_interval=ci,
        confidence_level=confidence_level,
        assumptions=(
            ["monotonic relationship", "independent observations"]
            if test_type == "spearman_correlation"
            else ["linear relationship", "independent observations", "no extreme outliers"]
        ),
        warnings=warnings,
        rows_available=available,
        rows_used=len(x),
        sampling_applied=sampled,
        sampling_method=SAMPLE_METHOD if sampled else None,
        sampling_seed=SAMPLE_SEED if sampled else None,
    )
    columns = ["x_column", "y_column", "n_pairs", "coefficient", "p_value"]
    rows: list[list[Any]] = [[x_col, y_col, n, r, p_value]]
    return columns, rows, result


def correlation_matrix(
    session: AnalysisSession,
    table: str,
    columns: list[str],
    filters: list[Filter] | None = None,
    *,
    task_id: str | None = None,
    timeout_seconds: float = 20.0,
) -> ResultSnapshot:
    """Pairwise Pearson correlation, computed by DuckDB's ``corr``.

    Aggregate-only: no row-level data leaves the database.
    """
    if not 2 <= len(columns) <= MAX_CORRELATION_COLUMNS:
        raise StatsError(
            f"between 2 and {MAX_CORRELATION_COLUMNS} columns are required, got {len(columns)}"
        )
    relation_sql, filterable, column_map = _relation(session, table)
    resolved = [_check_column(c, relation_sql, session) for c in columns]

    where, params = build_where(filters, filterable, column_map)
    from agentic_analytics.analytics.compute import _inline_params

    base = f"SELECT * FROM (\n{relation_sql}\n) AS r{_inline_params(where, params)}"

    unions: list[str] = []
    for a in resolved:
        parts = [f"{_lit(a)} AS column_name"]
        parts += [
            f'ROUND(CORR(CAST("{a}" AS DOUBLE), CAST("{b}" AS DOUBLE)), 6) AS "{b}"'
            for b in resolved
        ]
        unions.append(f"SELECT {', '.join(parts)} FROM (\n{base}\n) AS c")

    sql = "\nUNION ALL\n".join(unions)
    return run_query(
        session,
        sql,
        tool_name="correlation_matrix",
        parameters={
            "table": table,
            "columns": resolved,
            "filters": [f.model_dump() for f in (filters or [])],
        },
        task_id=task_id,
        max_rows=len(resolved),
        timeout_seconds=timeout_seconds,
        guard=False,
        extra_warnings=[
            "Correlation does not establish causation and does not adjust for "
            "confounding variables."
        ],
    )


def _lit(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
