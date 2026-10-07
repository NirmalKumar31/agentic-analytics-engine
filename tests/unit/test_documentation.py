"""Documentation checks for claims that previously went stale."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INLINE_LINK = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")


def _tracked_files() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return set(result.stdout.splitlines())


def test_tracked_inline_markdown_links_point_to_tracked_files() -> None:
    """A local-only file must not satisfy an inline documentation link."""
    tracked = _tracked_files()
    markdown = sorted(path for path in tracked if path == "README.md" or path.endswith(".md"))
    broken: list[str] = []

    for relative in markdown:
        source = ROOT / relative
        for raw_target in INLINE_LINK.findall(source.read_text(encoding="utf-8")):
            target = raw_target.strip().strip("<>").split("#", 1)[0].split("?", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            resolved = (source.parent / target).resolve()
            try:
                repo_relative = resolved.relative_to(ROOT).as_posix()
            except ValueError:
                broken.append(f"{relative}: target leaves repository: {raw_target}")
                continue
            if repo_relative not in tracked and not any(
                item.startswith(f"{repo_relative.rstrip('/')}/") for item in tracked
            ):
                broken.append(f"{relative}: {raw_target}")

    assert not broken, "untracked or missing Markdown targets:\n" + "\n".join(broken)


def test_current_docs_do_not_repeat_superseded_claims() -> None:
    current = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "README.md",
            "docs/README.md",
            "docs/ARCHITECTURE.md",
            "docs/DEPLOYMENT.md",
            "docs/LIMITATIONS.md",
        )
    )
    for stale in (
        "deploy/render-live.yaml",
        "web/e2e/preflight.ts",
        "two independent panes",
        "Chromium **96/96**",
        "The two figures are the same script, not a changed one",
    ):
        assert stale not in current


def test_design_package_identifies_itself_as_historical() -> None:
    for path in (
        "docs/design/REDESIGN-BRIEF.md",
        "docs/design/ACCEPTANCE.md",
        "docs/design/MOTION-STORYBOARD.md",
        "docs/design/generators/README.md",
    ):
        opening = (ROOT / path).read_text(encoding="utf-8")[:500]
        assert "historical" in opening.lower(), path


def test_remote_inference_docs_name_the_question_as_disclosed() -> None:
    """The README must say what reaches a cloud planner, and what does not.

    Asserted as claims rather than as one sentence. The previous version
    pinned the exact phrase "A cloud planner sees the question", so an
    editorial pass that kept every fact and changed one verb failed it,
    which teaches the next person to weaken the test instead of keeping
    the disclosure.

    Each item below is a thing a visitor is owed before uploading a file,
    so the test fails if any of them stops being stated.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "cloud planner" in readme
    for disclosed in ("the question", "schema", "aggregate labels"):
        assert disclosed in readme, disclosed
    # And the limit on it: individual rows do not leave the server.
    assert "unaggregated rows" in readme
    assert "the question, schema, inferred column types" in example
