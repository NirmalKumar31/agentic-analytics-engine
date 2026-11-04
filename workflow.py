"""Three node LangGraph workflow: interpret, use MCP, verify evidence."""
from __future__ import annotations

import json
import os
from typing import Any, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from mcp import Client

from engine import AnalysisError, Dataset, resolve, validate_plan, verify
from mcp_server import build_server


class State(TypedDict, total=False):
    question: str
    session_id: str
    session_key: str
    plan: dict[str, Any]
    result: dict[str, Any]
    answer: str
    trace: list[str]


async def _ai_proposal(question: str, dataset: Dataset, candidates: list[str], operation: str) -> dict[str, Any]:
    if os.getenv("AAE_AI_ENABLED", "false").lower() != "true":
        raise AnalysisError("multiple measures fit; name one explicitly")
    key = os.getenv("AAE_AI_API_KEY")
    model = os.getenv("AAE_AI_MODEL")
    if not key or not model:
        raise AnalysisError("AI interpretation is not configured; name a measure")
    prompt = {
        "question": question,
        "operation": operation,
        "candidate_measures": candidates,
        "dimensions": dataset.roles["dimensions"],
        "instruction": "Choose a measure only when the wording establishes it. Return JSON with operation, measure, dimension. No SQL.",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": "Bearer " + key},
                json={"model": model,
                      "messages": [{"role": "system", "content": "Return only a grounded JSON analytical mapping. If uncertain, use null for measure."},
                                   {"role": "user", "content": json.dumps(prompt)}],
                      "response_format": {"type": "json_object"}, "temperature": 0},
            )
            response.raise_for_status()
            proposal = json.loads(response.json()["choices"][0]["message"]["content"])
            if not isinstance(proposal, dict):
                raise ValueError("proposal was not an object")
            return proposal
    except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AnalysisError("AI interpretation failed; name a measure explicitly") from exc


def build_graph(dataset: Dataset, sessions: dict[str, tuple[str, Dataset]]) -> Any:
    server = build_server(sessions)

    async def interpret(state: State) -> dict[str, Any]:
        plan = resolve(state["question"], dataset)
        if plan["state"] == "ambiguous":
            proposal = await _ai_proposal(
                state["question"], dataset, plan["candidates"], plan["operation"]
            )
            plan = validate_plan(proposal, state["question"], dataset)
        return {"plan": plan, "trace": ["interpret"]}

    async def calculate(state: State) -> dict[str, Any]:
        # The tool takes a typed plan. Session credentials come from application
        # state, never from the model's proposed plan.
        async with Client(server) as client:
            response = await client.call_tool("aggregate_question", {
                "session_id": state["session_id"],
                "session_key": state["session_key"],
                "plan": state["plan"],
            })
            if response.is_error:
                raise AnalysisError("analytics tool refused the plan")
            result = dict(response.structured_content or {})
            if not result.get("result_id"):
                raise AnalysisError("analytics tool returned no evidence")
            return {"result": result, "trace": state["trace"] + ["mcp:aggregate_question"]}

    async def check(state: State) -> dict[str, Any]:
        snapshot = verify(dataset, state["result"]["result_id"])
        if snapshot["rows"] != state["result"]["rows"] or snapshot["sql"] != state["result"]["sql"]:
            raise AnalysisError("the returned result differs from stored evidence")
        label = state["plan"]["operation"]
        first = snapshot["rows"][0]
        if len(snapshot["rows"]) == 1 and len(snapshot["columns"]) == 2:
            answer = label.title() + ": " + str(first[0])
        else:
            answer = label.title() + " by " + str(state["plan"].get("dimension") or "month") + ". Values are shown in the table."
        return {"answer": answer, "trace": state["trace"] + ["verify"]}

    graph = StateGraph(State)
    graph.add_node("interpret", interpret)
    graph.add_node("calculate", calculate)
    graph.add_node("check", check)
    graph.add_edge(START, "interpret")
    graph.add_edge("interpret", "calculate")
    graph.add_edge("calculate", "check")
    graph.add_edge("check", END)
    return graph.compile()


async def analyze(question: str, session_id: str, session_key: str,
                  dataset: Dataset, sessions: dict[str, tuple[str, Dataset]]) -> dict[str, Any]:
    state = await build_graph(dataset, sessions).ainvoke({
        "question": question, "session_id": session_id, "session_key": session_key
    })
    return {"answer": state["answer"], "plan": state["plan"],
            "evidence": state["result"], "trace": state["trace"]}
