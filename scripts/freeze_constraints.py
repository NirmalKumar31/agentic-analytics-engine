"""Regenerate ``constraints.txt`` from the installed runtime closure.

Walks the dependency graph of the project rather than freezing the whole
environment, so development-only packages never reach the production pin file.
"""

from __future__ import annotations

import importlib.metadata as md
import pathlib
import re

ROOT_PACKAGE = "agentic-analytics-engine"
# Installed explicitly by the image alongside the wheel.
WEB_PACKAGES = ("fastapi", "uvicorn", "python-multipart")

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


def main() -> None:
    lines = collect()
    target = pathlib.Path(__file__).resolve().parents[1] / "constraints.txt"
    target.write_text(HEADER + "\n".join(lines) + "\n")
    print(f"wrote {target.name} with {len(lines)} pins")


if __name__ == "__main__":
    main()
