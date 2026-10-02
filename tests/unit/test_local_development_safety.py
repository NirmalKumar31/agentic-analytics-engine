"""Ordinary local development must not be one keystroke from spending money.

Settings load `.env`. A repository that has one can therefore select a paid
provider for any command that does not say otherwise -- and `make dev` did
not say otherwise, so a plain development server could come up on the cloud
provider while someone did unrelated UI work.

That is not a hypothetical. During the frontend programme a local server
booted with `provider_mode: cloud` and had to be killed before the browser
suite ran against it.

The fix is a property of the command rather than of whatever is on disk:
every target that runs application code pins the provider explicitly, and an
environment variable beats `.env`. These tests read the Makefile and check
that the property holds, because a pin that is easy to forget on the next
target added is not a boundary.

Deliberately *not* checked here: whether a credential exists. Looking is
unnecessary -- the guard is about intent, not about what happens to be
configured -- and a test that reads credentials to assert something about
them is its own hazard.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MAKEFILE = (Path(__file__).resolve().parents[2] / "Makefile").read_text()

#: Targets whose recipes start the application, the CLI or the test suite.
#: A target not listed here either runs no application code (lint, wheel,
#: docker build) or deliberately talks to a remote deployment instead
#: (live-acceptance, capacity-smoke), where the local provider is irrelevant.
APPLICATION_TARGETS = [
    "data",
    "test",
    "test-cov",
    "dev",
    "serve",
    "record",
    "evaluate",
    "verify",
]

PIN = "$(FAKE)"


def recipe_of(target: str) -> str:
    """The recipe lines of one target, without its prerequisites."""
    pattern = re.compile(
        rf"^{re.escape(target)}:[^\n]*\n((?:\t[^\n]*\n|#[^\n]*\n|\n(?=\t))*)",
        re.MULTILINE,
    )
    match = pattern.search(MAKEFILE)
    assert match, f"no target named {target!r} in the Makefile"
    return match.group(1)


def test_the_pin_is_defined_once() -> None:
    assert re.search(r"^FAKE\s*:=\s*AAE_PROVIDER_MODE=fake\s*$", MAKEFILE, re.MULTILINE)


@pytest.mark.parametrize("target", APPLICATION_TARGETS)
def test_every_application_target_pins_the_provider(target: str) -> None:
    """Explicit environment beats `.env`, so the posture travels with the
    command rather than depending on what the checkout happens to contain."""
    recipe = recipe_of(target)
    # Join backslash continuations first: `make` reads one logical command
    # across several physical lines, and the pin sits on the first of them.
    joined = recipe.replace("\\\n", " ")
    lines = [line for line in joined.splitlines() if line.startswith("\t") and line.strip()]
    assert lines, f"{target} has no recipe lines"
    for line in lines:
        # `@if`-style guards and echoes run no application code.
        if line.lstrip("\t").startswith(("@", "#")):
            continue
        assert PIN in line, (
            f"{target} runs application code without {PIN}: {line.strip()!r}. "
            "Without it this command inherits whatever provider `.env` names."
        )


def test_the_ordinary_development_server_is_not_the_cloud_one() -> None:
    recipe = recipe_of("dev")
    assert PIN in recipe
    assert "AAE_PROVIDER_MODE=cloud" not in recipe


def test_the_cloud_server_is_a_separate_named_target() -> None:
    # Flexibility is kept, but it has to be asked for by name.
    assert re.search(r"^dev-cloud:", MAKEFILE, re.MULTILINE)
    assert "AAE_PROVIDER_MODE=cloud" in recipe_of("dev-cloud")


def test_the_cloud_server_refuses_without_an_unmistakable_confirmation() -> None:
    header = re.search(r"^dev-cloud:([^\n]*)", MAKEFILE, re.MULTILINE)
    assert header, "dev-cloud is missing"
    prerequisites = header.group(1).split("##")[0].split()
    assert prerequisites, "dev-cloud has no prerequisites"
    # First, so the refusal arrives before a data generation and a frontend
    # build rather than after several minutes of work.
    assert prerequisites[0] == "confirm-paid-local", (
        f"the guard must run first; prerequisites are {prerequisites}"
    )

    guard = recipe_of("confirm-paid-local")
    assert "AAE_CONFIRM_PAID_LOCAL_RUN" in guard
    assert "exit 1" in guard


def test_the_refusal_explains_the_cheaper_path() -> None:
    guard = recipe_of("confirm-paid-local")
    assert "make dev" in guard
    assert "AAE_CONFIRM_PAID_LOCAL_RUN=1 make dev-cloud" in guard


def test_the_guard_does_not_look_at_credentials() -> None:
    """Checking whether a key exists would mean reading one, and the guard is
    about intent rather than configuration."""
    guard = recipe_of("confirm-paid-local")
    for secret in ("API_KEY", "OPENAI", "sk-", "cat .env", "grep .env", "source .env"):
        assert secret not in guard, f"the guard inspects {secret!r}"


def test_no_target_reads_or_prints_the_env_file() -> None:
    for forbidden in ("cat .env", "source .env", "grep .env", "echo $$OPENAI"):
        assert forbidden not in MAKEFILE, f"a target touches {forbidden!r}"


def test_the_header_no_longer_overclaims() -> None:
    """The header used to say every target works with no credentials, which
    was true of the defaults and not of a checkout with a `.env`."""
    head = MAKEFILE[: MAKEFILE.index("PY      :=")]
    assert "pins that explicitly" in head
    # And it says where the flexibility went, so the pin does not read as a
    # restriction someone should quietly remove.
    assert "dev-cloud" in head
