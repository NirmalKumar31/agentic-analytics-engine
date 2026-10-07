"""The acceptance scripts' leak pattern is copied, so it has to be checked.

Four scripts run against a live deployment and refuse to print anything
matching `FORBIDDEN` -- an API key, a Redis URL, a local home directory, the
provider's hostname, an internal domain. Each carries its own copy of that
pattern.

The duplication is deliberate. Nothing under `scripts/` imports anything
else under `scripts/`: they are single files run as
`python scripts/<name>.py https://...` against a deployment, with no package
install and no sibling on the path. A shared module would buy one definition
and cost that property.

What the duplication does not come with is any reason the four copies should
stay the same, and the failure is silent and one-directional: a pattern that
loses a branch still runs, still passes, and prints the thing it was meant to
withhold. So the copies are compared here instead.

If a branch is added, add it to all four. If one script genuinely needs to
redact something the others do not, it needs its own named pattern and this
test needs to say why -- not a quiet divergence inside a shared name.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

SCRIPTS = (
    "hosted_acceptance_semantics",
    "hosted_acceptance_upload",
    "paid_compare_acceptance",
    "paid_period_guard_acceptance",
)


def _load(name: str) -> Any:
    path = Path(__file__).parents[2] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, name
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_acceptance_script_redacts_the_same_things() -> None:
    patterns = {name: _load(name).FORBIDDEN for name in SCRIPTS}
    distinct = {(p.pattern, p.flags) for p in patterns.values()}
    assert len(distinct) == 1, {name: p.pattern for name, p in patterns.items()}


def test_the_pattern_still_catches_what_it_names() -> None:
    """Guards the guard.

    Comparing four copies of an empty pattern would also pass, so the shared
    pattern is exercised against one example of each branch it claims. These
    are fabricated samples, not real values.
    """
    forbidden = _load(SCRIPTS[0]).FORBIDDEN
    for sample in (
        "sk-abcdefghij0123456789",
        "redis://localhost:6379/0",
        "rediss://cache.example:6380/0",
        "/Users/someone/project",
        "AAE_CLOUD_API_KEY",
        "api.openai.com",
        "service.internal",
    ):
        assert forbidden.search(sample), sample

    for allowed in ("North", "revenue by region", "https://example.com/api/health"):
        assert not forbidden.search(allowed), allowed


def test_the_pattern_is_case_insensitive() -> None:
    # Set on every copy, and a deployment that echoed `SK-...` or
    # `REDIS://` back in a different case would otherwise slip through.
    forbidden = _load(SCRIPTS[0]).FORBIDDEN
    assert forbidden.flags & re.IGNORECASE
    assert forbidden.search("REDIS://HOST:6379")
