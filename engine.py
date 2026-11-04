"""Data loading, conservative question mapping, SQL construction, and evidence."""
from __future__ import annotations

import csv
import io
import math
import re
import secrets
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import duckdb
import sqlglot
from sqlglot import exp

MAX_BYTES = 2_000_000
MAX_ROWS = 20_000
MAX_COLUMNS = 40
MAX_GROUPS = 50
OPERATIONS = {"sum", "average", "count", "rank", "trend"}


class AnalysisError(ValueError):
    pass


def ident(name: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise AnalysisError("invalid column name")
    return '"' + name + '"'


def _name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _number(value: Any) -> bool:
    if value is None or str(value).strip() == "":
        return True
    try:
        number = float(str(value))
        return math.isfinite(number)
    except (TypeError, ValueError):
        return False


def _date(value: Any) -> bool:
    if value is None or str(value).strip() == "":
        return True
    try:
        date.fromisoformat(str(value)[:10])
        return True
    except ValueError:
        return False


@dataclass
class Dataset:
    columns: dict[str, str]
    roles: dict[str, list[str]]
    conn: Any
    fingerprint: str = field(default_factory=lambda: secrets.token_hex(8))
    results: dict[str, dict[str, Any]] = field(default_factory=dict)

    def close(self) -> None:
        self.conn.close()


def load_data(raw: bytes, filename: str) -> Dataset:
    """Read a bounded upload before locking DuckDB against external access."""
    if not raw or len(raw) > MAX_BYTES:
        raise AnalysisError("upload must be between 1 byte and 2 MB")
    if filename.lower().endswith(".csv"):
        try:
            reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
            source_columns = reader.fieldnames or []
            records = list(reader)
        except (UnicodeError, csv.Error) as exc:
            raise AnalysisError("invalid CSV") from exc
    elif filename.lower().endswith(".parquet"):
        try:
            import pyarrow.parquet as pq

            parquet = pq.ParquetFile(io.BytesIO(raw))
            size = sum(parquet.metadata.row_group(i).total_byte_size for i in range(parquet.metadata.num_row_groups))
            if parquet.metadata.num_rows > MAX_ROWS or parquet.metadata.num_columns > MAX_COLUMNS or size > 32_000_000:
                raise AnalysisError("Parquet exceeds the decoded size limit")
            table = parquet.read()
            source_columns = table.column_names
            records = table.to_pylist()
        except Exception as exc:
            raise AnalysisError("invalid Parquet") from exc
    else:
        raise AnalysisError("upload a CSV or Parquet file")
    if not (1 <= len(source_columns) <= MAX_COLUMNS) or len(records) > MAX_ROWS:
        raise AnalysisError("dataset exceeds the row or column limit")
    if not records:
        raise AnalysisError("dataset has no rows")
    names = [_name(str(c)) for c in source_columns]
    if any(not n or not re.fullmatch(r"[a-z][a-z0-9_]*", n) for n in names):
        raise AnalysisError("column names must contain letters")
    if len(set(names)) != len(names):
        raise AnalysisError("column names collide after normalization")
    columns: dict[str, str] = {}
    values: dict[str, list[Any]] = {}
    for old, new in zip(source_columns, names):
        values[new] = [row.get(old) for row in records]
        nonempty = [v for v in values[new] if v is not None and str(v).strip()]
        if nonempty and all(_number(v) for v in nonempty):
            columns[new] = "DOUBLE"
        elif nonempty and all(_date(v) for v in nonempty) and ("date" in new or "time" in new):
            columns[new] = "DATE"
        else:
            columns[new] = "VARCHAR"
    roles = {"measures": [], "dimensions": [], "time_fields": [], "identifiers": []}
    for name, dtype in columns.items():
        distinct = len({str(v) for v in values[name] if v is not None})
        if name == "id" or name.endswith("_id") or "email" in name or "phone" in name:
            roles["identifiers"].append(name)
        elif dtype == "DATE":
            roles["time_fields"].append(name)
        elif dtype == "DOUBLE":
            roles["measures"].append(name)
        elif distinct <= MAX_GROUPS and distinct <= max(1, int(len(records) * 0.8)):
            roles["dimensions"].append(name)
        else:
            roles["identifiers"].append(name)
    conn = duckdb.connect(":memory:")
    try:
        ddl = ", ".join(ident(n) + " " + t for n, t in columns.items())
        conn.execute("CREATE TABLE data (" + ddl + ")")
        normalized = []
        for record in records:
            row = []
            for old, new in zip(source_columns, names):
                value = record.get(old)
                if value is None or str(value).strip() == "":
                    row.append(None)
                elif columns[new] == "DOUBLE":
                    row.append(float(value))
                elif columns[new] == "DATE":
                    row.append(str(value)[:10])
                else:
                    row.append(str(value)[:500])
            normalized.append(tuple(row))
        placeholders = ", ".join("?" for _ in names)
        conn.executemany("INSERT INTO data VALUES (" + placeholders + ")", normalized)
        conn.execute("SET enable_external_access=false")
        conn.execute("SET lock_configuration=true")
    except Exception:
        conn.close()
        raise
    return Dataset(columns=columns, roles=roles, conn=conn)


def schema(dataset: Dataset) -> dict[str, Any]:
    return {"columns": dataset.columns, "roles": dataset.roles, "rows": dataset.conn.execute("SELECT COUNT(*) FROM data").fetchone()[0]}


def resolve(question: str, dataset: Dataset) -> dict[str, Any]:
    """A small fixed grammar: no silent substitution for missing columns."""
    q = question.lower().strip()
    if not q or len(q) > 500:
        raise AnalysisError("question must be 1 to 500 characters")
    if re.search(r"\b(forecast|predict|why did|caused by|causal)\b", q):
        raise AnalysisError("forecasting and causal claims are outside this demo")
    if re.search(r"\b(over time|by month|per month|monthly trend|monthly sales)\b", q):
        operation = "trend"
    elif re.search(r"\b(top|bottom|highest|lowest|rank)\b", q):
        operation = "rank"
    elif re.search(r"\b(average|avg|mean)\b", q):
        operation = "average"
    elif re.search(r"\b(how many|count|number of)\b", q):
        operation = "count"
    elif re.search(r"\b(total|sum|overall)\b", q):
        operation = "sum"
    else:
        raise AnalysisError("ask for a count, total, average, ranking, or monthly trend")
    dimensions = dataset.roles["dimensions"]
    measures = dataset.roles["measures"]
    named_measures = [c for c in measures if re.search(r"(?<![a-z0-9])" + re.escape(c.replace("_", " ")) + r"(?![a-z0-9])", q) or c in q]
    named_dimensions = [c for c in dimensions if re.search(r"(?<![a-z0-9])" + re.escape(c.replace("_", " ")) + r"(?![a-z0-9])", q) or c in q]
    named_unusable = [c for c in dataset.roles["identifiers"] if c.replace("_", " ") in q or c in q]
    if operation in {"sum", "average"} and not named_measures:
        requested = re.search(r"\b(?:total|sum|average|avg|mean)\s+(?:of\s+|the\s+)?([a-z_]+)", q)
        if requested and requested.group(1) not in {"by", "per", "for", "value", "amount"}:
            raise AnalysisError("the requested measure is not a numeric column in this dataset")
    if named_unusable:
        raise AnalysisError("identifier or near unique columns cannot be aggregated or grouped")
    requested_group = re.search(r"\bby\s+([a-z][a-z0-9_ ]{0,35}?)(?:\s+where\b|[?.!,]|$)", q)
    grouping = None
    if requested_group and operation != "trend":
        phrase = requested_group.group(1).strip().replace(" ", "_")
        grouping = phrase if phrase in dimensions else None
        if grouping is None and phrase not in {"month", "week", "year"}:
            raise AnalysisError("the grouping after 'by' is not an available dimension")
    elif named_dimensions:
        grouping = named_dimensions[0]
    if len(named_measures) > 1 or len(named_dimensions) > 1:
        raise AnalysisError("name one measure and at most one grouping")
    measure = None
    if operation != "count":
        if named_measures:
            measure = named_measures[0]
        elif len(measures) == 1:
            measure = measures[0]
        elif measures:
            if re.search(r"\bwhere\b", q):
                raise AnalysisError("name the measure explicitly when adding a filter")
            return {"state": "ambiguous", "operation": operation, "candidates": measures, "dimension": grouping}
        else:
            raise AnalysisError("no numeric measure is available")
    if operation == "count" and re.search(r"\b(?:orders|customers|products)\b", q):
        named_entities = [c for c in dataset.columns if c in {"order_id", "customer_id", "product_id"}]
        if not named_entities:
            raise AnalysisError("counting business entities requires an entity identifier")
    if operation == "rank" and not grouping:
        if len(dimensions) == 1:
            grouping = dimensions[0]
        else:
            raise AnalysisError("name the dimension to rank")
    if operation == "trend" and len(dataset.roles["time_fields"]) != 1:
        raise AnalysisError("a monthly trend needs one unambiguous date field")
    filter_spec = None
    match = re.search(r"\bwhere\s+([a-z][a-z0-9_]*)\s*(?:=|is)\s*([a-z0-9_ -]+?)(?:[?.!]|$)", q)
    if match:
        column, value = match.group(1), match.group(2).strip()
        if column not in dimensions or not value:
            raise AnalysisError("the filter must name a categorical column and value")
        filter_spec = {"column": column, "value": value}
    elif re.search(r"\bwhere\b", q):
        raise AnalysisError("use 'where category = Books' for a filter")
    return {"state": "exact", "operation": operation, "measure": measure,
            "dimension": grouping, "time_field": dataset.roles["time_fields"][0] if operation == "trend" else None,
            "filter": filter_spec}


def validate_plan(plan: dict[str, Any], question: str, dataset: Dataset) -> dict[str, Any]:
    """AI plans pass the same role and explicit-intent checks as rule plans."""
    rules = resolve(question, dataset)
    if rules["state"] != "ambiguous":
        raise AnalysisError("AI planning is available only for ambiguous questions")
    if plan.get("operation") != rules["operation"]:
        raise AnalysisError("AI changed the requested operation")
    measure = plan.get("measure")
    if measure not in rules["candidates"]:
        raise AnalysisError("AI selected a measure outside the schema")
    dimension = plan.get("dimension")
    if dimension != rules["dimension"]:
        raise AnalysisError("AI changed the requested grouping")
    return {"state": "exact", "operation": rules["operation"], "measure": measure,
            "dimension": dimension, "time_field": dataset.roles["time_fields"][0] if rules["operation"] == "trend" else None,
            "filter": None}


def _guard(sql: str) -> str:
    try:
        nodes = sqlglot.parse(sql, read="duckdb")
    except sqlglot.ParseError as exc:
        raise AnalysisError("generated SQL could not be parsed") from exc
    if len(nodes) != 1 or not isinstance(nodes[0], exp.Select):
        raise AnalysisError("only one SELECT query is permitted")
    root = nodes[0]
    blocked = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Attach, exp.Copy, exp.Command)
    if any(root.find(kind) for kind in blocked):
        raise AnalysisError("write or external access is forbidden")
    tables = {t.name.lower() for t in root.find_all(exp.Table)}
    if tables != {"data"}:
        raise AnalysisError("query may only read the active dataset")
    if len(list(root.walk())) > 250 or len(list(root.find_all(exp.Join))) > 0:
        raise AnalysisError("query is too complex")
    return root.sql(dialect="duckdb")


def execute(dataset: Dataset, plan: dict[str, Any]) -> dict[str, Any]:
    """Validate contract again at the tool boundary, then calculate."""
    op = plan.get("operation")
    measure = plan.get("measure")
    dim = plan.get("dimension")
    time_field = plan.get("time_field")
    if op not in OPERATIONS or (op != "count" and measure not in dataset.roles["measures"]):
        raise AnalysisError("invalid analytical contract")
    if dim is not None and dim not in dataset.roles["dimensions"]:
        raise AnalysisError("invalid grouping")
    if op == "rank" and dim is None:
        raise AnalysisError("ranking needs a grouping")
    if op == "trend" and time_field not in dataset.roles["time_fields"]:
        raise AnalysisError("invalid trend date field")
    filters = plan.get("filter")
    params: list[Any] = []
    where = ""
    if filters:
        if not isinstance(filters, dict) or filters.get("column") not in dataset.roles["dimensions"]:
            raise AnalysisError("invalid filter column")
        value = filters.get("value")
        if not isinstance(value, str) or not value or len(value) > 100:
            raise AnalysisError("invalid filter value")
        where = " WHERE lower(" + ident(filters["column"]) + ") = ?"
        params.append(value.lower())
    grouping = []
    if op == "trend":
        grouping.append("strftime(date_trunc('month', " + ident(time_field) + "), '%Y-%m') AS period")
    elif dim:
        grouping.append(ident(dim))
    if op == "count":
        aggregate = "COUNT(*) AS value"
    elif op == "average":
        aggregate = "AVG(" + ident(measure) + ") AS value"
    else:
        aggregate = "SUM(" + ident(measure) + ") AS value"
    selected = grouping + [aggregate, "COUNT(*) AS row_count"]
    sql = "SELECT " + ", ".join(selected) + " FROM data" + where
    if grouping:
        sql += " GROUP BY 1"
        if op == "rank":
            sql += " ORDER BY value " + ("ASC" if plan.get("ascending") else "DESC") + " NULLS LAST LIMIT 10"
        else:
            sql += " ORDER BY 1 NULLS LAST LIMIT " + str(MAX_GROUPS + 1)
    sql = _guard(sql)
    cursor = dataset.conn.execute(sql, params)
    names = [c[0] for c in cursor.description]
    rows = [[v.isoformat() if isinstance(v, date) else v for v in row] for row in cursor.fetchall()]
    if len(rows) > MAX_GROUPS:
        raise AnalysisError("too many groups to publish a complete breakdown")
    result_id = secrets.token_hex(12)
    snapshot = {"result_id": result_id, "columns": names, "rows": rows,
                "row_count": len(rows), "sql": sql, "parameters": params,
                "dataset_fingerprint": dataset.fingerprint}
    dataset.results[result_id] = snapshot
    return snapshot


def verify(dataset: Dataset, result_id: str) -> dict[str, Any]:
    snapshot = dataset.results.get(result_id)
    if not snapshot or snapshot["dataset_fingerprint"] != dataset.fingerprint:
        raise AnalysisError("result evidence is unavailable")
    if not snapshot["rows"] or "value" not in snapshot["columns"]:
        raise AnalysisError("query returned no publishable values")
    index = snapshot["columns"].index("value")
    if any(row[index] is None or not isinstance(row[index], (int, float)) or not math.isfinite(row[index]) for row in snapshot["rows"]):
        raise AnalysisError("the aggregate has no valid numeric result")
    return snapshot


def correlation(dataset: Dataset, x: str, y: str) -> dict[str, Any]:
    if x not in dataset.roles["measures"] or y not in dataset.roles["measures"] or x == y:
        raise AnalysisError("select two different numeric measures")
    sql = _guard("SELECT corr(" + ident(x) + ", " + ident(y) + ") AS value, COUNT(*) AS row_count FROM data")
    row = dataset.conn.execute(sql).fetchone()
    if row[0] is None or not math.isfinite(row[0]):
        raise AnalysisError("correlation is undefined for these columns")
    result_id = secrets.token_hex(12)
    result = {"result_id": result_id, "columns": ["value", "row_count"], "rows": [[row[0], row[1]]],
              "row_count": 1, "sql": sql, "parameters": [], "dataset_fingerprint": dataset.fingerprint,
              "limitation": "Correlation is not causation."}
    dataset.results[result_id] = result
    return result
