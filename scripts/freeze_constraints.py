"""Regenerate the pinned dependency closures.

Two files, because they answer different questions.

``constraints.txt`` is the runtime closure: what the image installs and what
`pip-audit` checks. It walks the project's dependency graph rather than
freezing the environment, so development-only packages never reach the
production pin file.

``constraints-dev.txt`` is the development closure: runtime plus the `dev`
extra, which is what tests, recordings and evaluation artifacts were produced
against. Bootstrap and CI install with it so a local run and a CI run resolve
to the same tree.
"""

from __future__ import annotations

import importlib.metadata as md
import pathlib
import re

ROOT_PACKAGE = "agentic-analytics-engine"
# Installed explicitly by the image alongside the wheel.
WEB_PACKAGES = ("fastapi", "uvicorn", "python-multipart")

DEV_HEADER = """# Pinned development dependency closure.
#
# Regenerate after changing pyproject dependencies:
#   make constraints
#
# Runtime plus the `dev` extra: the tree that tests, recordings and evaluation
# artifacts were produced against. `make bootstrap` and CI install with
# `-c constraints-dev.txt` so both resolve identically.
"""

HEADER = """# Pinned runtime dependency closure.
#
# Regenerate after changing pyproject dependencies:
#   make constraints
#
# These are the versions the test suite, the recordings and the evaluation
# numbers in the README were produced against. The image installs with
# `-c constraints.txt` so a rebuild resolves to the same tree.
"""


def _name(requirement: str) -> str:
    return re.split(r"[<>=!\[ ;]", requirement.strip())[0].lower().replace("_", "-")


def collect() -> list[str]:
    seen: set[str] = set()
    pinned: set[str] = set()

    def walk(requirement: str) -> None:
        key = _name(requirement)
        if key in seen:
            return
        seen.add(key)
        try:
            dist = md.distribution(key)
        except md.PackageNotFoundError:
            return
        if key != ROOT_PACKAGE:
            pinned.add(f"{dist.metadata['Name']}=={dist.version}")
        for req in dist.requires or []:
            # Optional extras are not installed in the runtime image.
            if "extra ==" in req:
                continue
            walk(req)

    walk(ROOT_PACKAGE)
    for package in WEB_PACKAGES:
        walk(package)
    return sorted(pinned, key=str.lower)


def collect_dev() -> list[str]:
    """Everything installed, except the project itself.

    The dev tree is whatever `pip install -e ".[dev]"` resolved to, so it is
    read from the environment rather than walked: the point is to record the
    exact closure that produced the committed results.
    """
    pinned = []
    for dist in md.distributions():
        name = (dist.metadata["Name"] or "").strip()
        if not name or name.lower().replace("_", "-") == ROOT_PACKAGE:
            continue
        pinned.append(f"{name}=={dist.version}")
    return sorted(set(pinned), key=str.lower)


def main() -> None:
    root = pathlib.Path(__file__).resolve().parents[1]

    lines = collect()
    target = root / "constraints.txt"
    target.write_text(HEADER + "\n".join(lines) + "\n")
    print(f"wrote {target.name} with {len(lines)} pins")

    dev_lines = collect_dev()
    dev_target = root / "constraints-dev.txt"
    dev_target.write_text(DEV_HEADER + "\n".join(dev_lines) + "\n")
    print(f"wrote {dev_target.name} with {len(dev_lines)} pins")


if __name__ == "__main__":
    main()
