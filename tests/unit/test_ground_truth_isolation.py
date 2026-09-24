"""The answer key must never reach an agent.

The evaluation asserts that agents recovered injected patterns. That claim is
worthless if the patterns were in the prompt, so this walks the import graph
of everything an agent can reach and fails if `ground_truth` is in it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "agentic_analytics"

# Everything that participates in producing a prompt, choosing a tool, or
# executing analysis. The generator is excluded: it injects the patterns, so
# it must import the answer key.
AGENT_FACING = [
    "agents",
    "llm",
    "graph",
    "mcp_layer",
    "analytics",
    "warehouse",
    "verification",
    "api",
]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _python_files(package: str) -> list[Path]:
    directory = PACKAGE / package
    return sorted(directory.rglob("*.py")) if directory.exists() else []


@pytest.mark.parametrize("package", AGENT_FACING)
def test_agent_facing_code_does_not_import_ground_truth(package: str) -> None:
    offenders: list[str] = []
    for path in _python_files(package):
        for module in _imports(path):
            if "ground_truth" in module:
                offenders.append(f"{path.relative_to(PACKAGE)} imports {module}")
    assert not offenders, "the answer key must not be reachable from agent code: " + str(offenders)


def test_ground_truth_text_does_not_appear_in_prompts() -> None:
    """No pattern description may be pasted into a prompt by hand either."""
    from agentic_analytics.data.ground_truth import PATTERNS

    prompt_text = (PACKAGE / "agents" / "prompts.py").read_text().lower()
    for pattern in PATTERNS:
        assert pattern.pattern_id not in prompt_text
        for term in pattern.accept_terms:
            # A generic word like "margin" is fine; a specific injected entity
            # such as "rapidpost" is not.
            if len(term) > 8:
                assert term.lower() not in prompt_text, term


def test_only_the_generator_and_evaluation_import_ground_truth() -> None:
    allowed = {"data/generator.py", "data/ground_truth.py"}
    importers: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        for module in _imports(path):
            if "ground_truth" in module:
                importers.add(str(path.relative_to(PACKAGE)))
    unexpected = {i for i in importers if i not in allowed and not i.startswith("evaluation/")}
    assert not unexpected, unexpected
