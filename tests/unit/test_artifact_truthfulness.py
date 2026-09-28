"""What an evaluation artifact may say, and what it may never carry.

Both rules come from the first paid smoke's report. It recorded
`"temperature": 0.0` for a cloud run whose transport refuses sampling
fields outright, and it wrote the author's home directory into a JSON
file. Neither broke anything. Both make the artifact untrustworthy, which
is the only thing an artifact is for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_analytics.config import Settings
from agentic_analytics.evaluation.real_model import safe_path
from agentic_analytics.llm.cloud import FORBIDDEN_SAMPLING_FIELDS


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("var/real-model-checkpoint", "var/real-model-checkpoint"),
        # macOS
        ("/Users/someone/private/checkpoint", "checkpoint"),
        # Linux
        ("/home/someone/private/checkpoint", "checkpoint"),
        # Windows, forward and backslashed. Neither is absolute on POSIX,
        # so both resolve *under* the repository and would otherwise pass
        # a containment check while still naming a person.
        ("C:/Users/someone/checkpoint", "checkpoint"),
        ("C:\\Users\\someone\\checkpoint", "checkpoint"),
    ],
)
def test_a_private_path_never_reaches_an_artifact(given: str, expected: str) -> None:
    assert safe_path(Path(given)) == expected


def test_a_repository_path_stays_readable() -> None:
    """Sanitising must not reduce every path to a bare name: a relative
    path inside the repository is useful and identifies nobody."""
    assert "/" in safe_path(Path("var/real-model-checkpoint"))


def test_sanitised_paths_name_no_user() -> None:
    for raw in ("/Users/alice/x", "/home/bob/x", "C:/Users/carol/x"):
        assert "alice" not in safe_path(Path(raw))
        assert "bob" not in safe_path(Path(raw))
        assert "carol" not in safe_path(Path(raw))


def _environment(mode: str) -> dict[str, object]:
    from agentic_analytics.evaluation.real_model import environment_fingerprint as build

    cfg = Settings(provider_mode=mode, log_json=False)  # type: ignore[arg-type]
    return dict(build(cfg))


def test_a_cloud_artifact_states_no_sampling_setting() -> None:
    """The transport refuses sampling fields, so claiming one is false.

    `temperature: 0.0` sat in every cloud report while `temperature` was
    on the forbidden list two modules away.
    """
    assert "temperature" in FORBIDDEN_SAMPLING_FIELDS
    env = _environment("cloud")
    assert env["temperature"] is None
    assert env["sampling_fields_sent"] == "none"


def test_a_cloud_artifact_states_what_was_sent() -> None:
    env = _environment("cloud")
    assert env["reasoning_effort"] in ("low", "medium", "high", "minimal")
    assert env["service_tier"] == "default"
    assert env["store"] is False


def test_a_local_artifact_still_records_its_temperature() -> None:
    """Ollama is sent one, so recording it is truthful there."""
    env = _environment("local")
    assert env["temperature"] == 0.0


def test_the_committed_historical_artifacts_are_not_rewritten() -> None:
    """Old reports record an older provider and an older shape. They are
    evidence of what happened, and correcting them would be a lie about
    the past rather than a fix."""
    fixture = (
        Path(__file__).resolve().parents[1] / "fixtures" / "paid_smoke" / "first_smoke_38cfb51.json"
    )
    recorded = json.loads(fixture.read_text())
    assert recorded["recorded_outcome"]["irrelevant_published"] == 1
    assert recorded["fixture_kind"] == "failed product-quality acceptance"
