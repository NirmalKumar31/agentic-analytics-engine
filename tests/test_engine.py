from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import app
from engine import AnalysisError, execute, load_data, resolve, verify


def dataset():
    return load_data(Path("data/demo.csv").read_bytes(), "demo.csv")


def test_grouped_sum_and_evidence():
    ds = dataset()
    plan = resolve("What are total sales by region?", ds)
    result = execute(ds, plan)
    assert result["columns"] == ["region", "value", "row_count"]
    assert {row[0]: row[1] for row in result["rows"]} == {
        "East": 150, "West": 150, "North": 210
    }
    assert verify(ds, result["result_id"]) == result
    assert "SUM" in result["sql"]


def test_refusals():
    ds = dataset()
    with pytest.raises(AnalysisError):
        resolve("Forecast next month's sales", ds)
    with pytest.raises(AnalysisError):
        resolve("Total sales by customer_id", ds)
    with pytest.raises(AnalysisError):
        execute(ds, {"operation": "sum", "measure": "not_a_column"})


def test_end_to_end_api():
    with TestClient(app) as client:
        assert client.get("/api/ready").json()["status"] == "ok"
        session = client.post("/api/demo").json()["session_id"]
        response = client.post("/api/analyze", json={
            "session_id": session, "question": "What are total sales by region?"
        })
        assert response.status_code == 200, response.text
        body = response.json()
        assert "mcp:aggregate_question" in body["trace"]
        assert body["evidence"]["result_id"]
        assert len(body["evidence"]["rows"]) == 3
