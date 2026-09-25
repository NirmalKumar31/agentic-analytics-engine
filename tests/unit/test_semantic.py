"""Inferred schema for datasets with no metric layer.

The roles drive what an agent can do with an uploaded file, so getting them
wrong is not cosmetic: a revenue column read as an identifier cannot be summed,
and a price read as a dimension produces a chart with one bar per row.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from agentic_analytics.analytics.semantic import infer_schema
from agentic_analytics.warehouse.session import AnalysisSession, open_upload_session


def _upload(tmp_path: Path, name: str, text: str) -> AnalysisSession:
    file = tmp_path / name
    file.write_text(text)
    return open_upload_session(file, name, "csv")


SMALL = "order_date,region,channel,revenue,cost,units\n" + "".join(
    f"2025-0{i % 5 + 1}-1{i % 9},{['West', 'East', 'North', 'South'][i % 4]},"
    f"{['web', 'retail'][i % 2]},{1000 + i * 97}.50,{600 + i * 55}.25,{i % 9 + 1}\n"
    for i in range(10)
)


def _large() -> str:
    rng = random.Random(3)
    header = "order_id,order_date,region,rating,sales_amount,net_amount,qty,notes\n"
    rows = "".join(
        f"{i},2025-{rng.randint(1, 12):02d}-15,{['West', 'East', 'North', 'South'][i % 4]},"
        f"{rng.randint(1, 5)},{rng.uniform(10, 9000):.2f},{rng.uniform(5, 8000):.2f},"
        f"{rng.randint(1, 40)},note-{i}\n"
        for i in range(500)
    )
    return header + rows


def test_money_columns_are_measures_even_in_a_tiny_file(tmp_path: Path) -> None:
    """Cardinality is meaningless when there are ten rows.

    Every numeric column in a small file has few distinct values, so an
    absolute threshold would call all of them dimensions.
    """
    session = _upload(tmp_path, "small.csv", SMALL)
    try:
        schema = infer_schema(session, "uploaded_data")
        assert set(schema.measures) == {"revenue", "cost", "units"}
        assert set(schema.dimensions) == {"region", "channel"}
        assert schema.time_fields == ["order_date"]
    finally:
        session.close()


def test_roles_are_sensible_on_a_larger_file(tmp_path: Path) -> None:
    session = _upload(tmp_path, "large.csv", _large())
    try:
        schema = infer_schema(session, "uploaded_data")
        assert "order_id" in schema.identifiers
        assert "order_date" in schema.time_fields
        assert "region" in schema.dimensions
        # A 1-5 rating genuinely repeats, so it groups.
        assert "rating" in schema.dimensions
        assert {"sales_amount", "net_amount", "qty"} <= set(schema.measures)
        # Free text with a distinct value per row cannot be grouped by.
        assert "notes" not in schema.dimensions
    finally:
        session.close()


def test_a_float_is_never_an_identifier(tmp_path: Path) -> None:
    """A price is usually near-unique and is still a quantity to sum."""
    text = "price\n" + "".join(f"{i + 0.5}\n" for i in range(200))
    session = _upload(tmp_path, "p.csv", text)
    try:
        schema = infer_schema(session, "uploaded_data")
        assert schema.measures == ["price"]
        assert schema.identifiers == []
    finally:
        session.close()


def test_constant_columns_are_ignored(tmp_path: Path) -> None:
    text = "always,varies\n" + "".join(f"same,{i}\n" for i in range(100))
    session = _upload(tmp_path, "c.csv", text)
    try:
        schema = infer_schema(session, "uploaded_data")
        roles = {f.name: f.role for f in schema.fields}
        assert roles["always"] == "ignored"
    finally:
        session.close()


def test_ambiguity_is_raised_only_when_the_choice_matters(tmp_path: Path) -> None:
    """Two plausible revenue columns is a question; one is not."""
    two = "sales_amount,net_amount\n" + "".join(f"{i}.5,{i}.1\n" for i in range(100))
    session = _upload(tmp_path, "two.csv", two)
    try:
        schema = infer_schema(session, "uploaded_data")
        concepts = {a["concept"] for a in schema.ambiguities}
        assert "revenue" in concepts
        question = next(a for a in schema.ambiguities if a["concept"] == "revenue")["question"]
        assert "sales_amount" in question and "net_amount" in question
    finally:
        session.close()

    one = "sales_amount,widgets\n" + "".join(f"{i}.5,{i}\n" for i in range(100))
    session = _upload(tmp_path, "one.csv", one)
    try:
        assert infer_schema(session, "uploaded_data").ambiguities == []
    finally:
        session.close()


def test_everything_is_marked_inferred(tmp_path: Path) -> None:
    """An inferred measure is not a governed metric and must not read as one."""
    session = _upload(tmp_path, "s.csv", SMALL)
    try:
        payload = infer_schema(session, "uploaded_data").as_dict()
        assert payload["status"] == "inferred"
        assert all(f["reason"] for f in payload["fields"])
    finally:
        session.close()


def test_inference_is_deterministic(tmp_path: Path) -> None:
    first = _upload(tmp_path, "a.csv", _large())
    second = _upload(tmp_path, "b.csv", _large())
    try:
        assert (
            infer_schema(first, "uploaded_data").as_dict()["fields"]
            == (infer_schema(second, "uploaded_data").as_dict()["fields"])
        )
    finally:
        first.close()
        second.close()


def test_a_hostile_column_name_is_carried_as_data(tmp_path: Path) -> None:
    text = '"a\\"; DROP TABLE x --",b\n1,2\n3,4\n'
    session = _upload(tmp_path, "h.csv", text)
    try:
        schema = infer_schema(session, "uploaded_data")
        assert len(schema.fields) == 2
    finally:
        session.close()


@pytest.mark.parametrize("name", ["customer_id", "orderId", "uuid", "user_key", "sku_code"])
def test_key_like_names_are_identifiers(tmp_path: Path, name: str) -> None:
    text = f"{name},value\n" + "".join(f"{i},{i * 2}\n" for i in range(100))
    session = _upload(tmp_path, "k.csv", text)
    try:
        roles = {f.name: f.role for f in infer_schema(session, "uploaded_data").fields}
        assert roles[name] == "identifier"
    finally:
        session.close()
