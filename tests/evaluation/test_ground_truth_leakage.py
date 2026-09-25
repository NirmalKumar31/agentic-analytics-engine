"""Ground-truth leakage audit.

The import-graph test proves the answer key is not *imported* by agent code.
That is necessary and not sufficient: an expected answer could still reach a
model through a prompt, a tool description, a metric description, a filter
value, or an evaluation object handed to the graph.

So this captures every byte any provider would see during a full benchmark
run -- system prompts, user prompts, the structured context, and the
serialised schemas -- and asserts the answer key is absent from all of it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from agentic_analytics.data.generator import GeneratorConfig, generate_warehouse
from agentic_analytics.data.ground_truth import PATTERNS
from agentic_analytics.evaluation.cases import CASES
from agentic_analytics.evaluation.harness import run_benchmark
from agentic_analytics.llm.base import LLMRequest
from agentic_analytics.llm.fake import FakeProvider

# Terms that would give the answer away if an agent saw them. Entity names
# that legitimately appear in the data (a category, a carrier) are excluded:
# the agent is supposed to find those in query results. What must never
# appear is the *expectation* -- the pattern id, the answer-key prose, or the
# benchmark's stated direction.
BANNED_SUBSTRINGS: list[str] = [
    *(p.pattern_id for p in PATTERNS),
    "ground_truth",
    "ground truth",
    "injected",
    "answer key",
    "expected_entity",
    "expect_entity_any",
    "expected_direction",
    "pattern_found",
    "benchmark",
]

# Whole sentences from the answer key. Any fragment of one appearing in a
# prompt would be a direct leak.
BANNED_PHRASES: list[str] = [
    phrase.strip().lower()
    for p in PATTERNS
    for phrase in re.split(r"[.;]", p.description)
    if len(phrase.strip()) > 40
]


class RecordingProvider(FakeProvider):
    """Behaves exactly like the scripted provider, and keeps every request."""

    def __init__(self, sink: list[LLMRequest], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._sink = sink

    async def complete_json(self, request: LLMRequest) -> dict[str, Any]:
        self._sink.append(request)
        return await super().complete_json(request)


@pytest.fixture(scope="module")
def captured(tmp_path_factory: pytest.TempPathFactory) -> list[LLMRequest]:
    """Every provider request made during a full benchmark run."""
    warehouse = tmp_path_factory.mktemp("leak")
    generate_warehouse(warehouse, GeneratorConfig(n_customers=6_000, n_products=250, seed=11))

    sink: list[LLMRequest] = []
    import agentic_analytics.graph.runner as runner_module

    original = runner_module.build_provider
    runner_module.build_provider = lambda settings=None: RecordingProvider(  # type: ignore[assignment]
        sink, max_calls=settings.budgets.max_llm_calls if settings else 40
    )
    try:
        import anyio

        anyio.run(run_benchmark, warehouse)
    finally:
        runner_module.build_provider = original  # type: ignore[assignment]

    assert sink, "no provider requests were captured"
    return sink


def _visible_text(request: LLMRequest) -> str:
    """Everything a provider could read, including the scripted-only context."""
    return "\n".join(
        [
            request.role,
            request.system,
            request.user,
            json.dumps(request.context, default=str),
            json.dumps(request.schema_, default=str),
        ]
    )


def test_the_run_actually_exercised_every_agent(captured: list[LLMRequest]) -> None:
    roles = {r.role for r in captured}
    assert {
        "question_analyst",
        "planner",
        "worker_next_tool",
        "worker_findings",
        "critic",
        "reporter",
    } <= roles, roles
    assert len(captured) > 50, f"only {len(captured)} requests captured"


@pytest.mark.parametrize("banned", BANNED_SUBSTRINGS)
def test_no_answer_key_term_reaches_a_provider(banned: str, captured: list[LLMRequest]) -> None:
    lowered = banned.lower()
    for request in captured:
        text = _visible_text(request).lower()
        assert lowered not in text, f"{banned!r} appeared in a {request.role} request"


def test_no_answer_key_sentence_reaches_a_provider(captured: list[LLMRequest]) -> None:
    assert BANNED_PHRASES, "the answer key has no long phrases to check"
    for request in captured:
        text = _visible_text(request).lower()
        for phrase in BANNED_PHRASES:
            assert phrase not in text, f"answer-key prose in a {request.role} request"


def test_benchmark_expectations_never_reach_a_provider(
    captured: list[LLMRequest],
) -> None:
    """The case objects carry the expected answers; only the question travels."""
    corpus = "\n".join(_visible_text(r) for r in captured).lower()
    for case in CASES:
        # The question travels -- that is the input. Nothing else does.
        assert case.question.lower() in corpus, "the question itself should reach the agents"
        if case.notes and len(case.notes) > 40:
            assert case.notes.lower() not in corpus, case.case_id
        for entity in case.expect_entity_any:
            # An entity may legitimately appear in a query result, but never
            # as an expectation phrased the way the benchmark phrases it.
            assert f"expect_entity_any={entity}" not in corpus
            assert f"expected: {entity}" not in corpus


def test_no_instruction_text_names_an_expected_entity(
    captured: list[LLMRequest],
) -> None:
    """An entity may appear as data; it must never appear as an instruction.

    `Home & Kitchen` legitimately shows up in a query result the tools
    returned, and a worker has to read it to state a finding. What must not
    exist is any *instruction* naming it. The system prompt is entirely
    static, so it is the exact surface to check -- along with the prompt
    templates themselves, which is where such a hint would be written.
    """
    entities = [p.expected_entity.lower() for p in PATTERNS if p.expected_entity]
    assert entities

    for request in captured:
        system = request.system.lower()
        for entity in entities:
            assert entity not in system, f"{entity!r} appears in the {request.role} system prompt"

    template_source = (
        (
            Path(__file__).resolve().parents[2]
            / "src"
            / "agentic_analytics"
            / "agents"
            / "prompts.py"
        )
        .read_text()
        .lower()
    )
    for entity in entities:
        assert entity not in template_source, f"{entity!r} is written into a prompt template"


def test_environment_carries_no_answer_key() -> None:
    import os

    for key, value in os.environ.items():
        if not key.startswith("AAE_"):
            continue
        lowered = f"{key}{value}".lower()
        for pattern in PATTERNS:
            assert pattern.pattern_id not in lowered


def test_metric_and_tool_descriptions_are_free_of_expectations() -> None:
    """The metric layer and tool docstrings are prompt surfaces too."""
    from agentic_analytics.mcp_layer.server import SERVER_INSTRUCTIONS
    from agentic_analytics.warehouse.metrics import load_registry

    surfaces = [SERVER_INSTRUCTIONS]
    registry = load_registry()
    for metric in registry.metrics.values():
        surfaces.extend([metric.name, metric.description, metric.sql])
    for model in registry.models.values():
        surfaces.extend([model.name, model.description, model.sql])

    blob = "\n".join(surfaces).lower()
    for pattern in PATTERNS:
        assert pattern.pattern_id not in blob
        assert pattern.title.lower() not in blob


def test_recordings_do_not_embed_the_answer_key() -> None:
    directory = Path(__file__).resolve().parents[2] / "examples" / "recordings"
    for file in directory.glob("*.json"):
        blob = file.read_text().lower()
        for pattern in PATTERNS:
            assert pattern.pattern_id not in blob, file.name
            assert "ground_truth" not in blob, file.name
