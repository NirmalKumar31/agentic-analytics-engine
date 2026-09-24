"""Generation entry point for the built-in warehouse."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agentic_analytics.data.generator import (
    DEFAULT_SEED,
    GeneratorConfig,
    generate_warehouse,
    warehouse_fingerprint,
)


@dataclass(frozen=True)
class Manifest:
    """What a generation run produced."""

    seed: int
    row_counts: dict[str, int]
    dataset_fingerprint: str


def build(out_dir: Path, seed: int = DEFAULT_SEED) -> Manifest:
    """Generate the warehouse and return a manifest."""
    counts = generate_warehouse(out_dir, GeneratorConfig(seed=seed))
    manifest = Manifest(
        seed=seed,
        row_counts=counts,
        dataset_fingerprint=warehouse_fingerprint(out_dir),
    )
    (out_dir / "manifest.json").write_text(
        json.dumps(
            {
                "seed": manifest.seed,
                "row_counts": manifest.row_counts,
                "dataset_fingerprint": manifest.dataset_fingerprint,
            },
            indent=2,
        )
        + "\n"
    )
    return manifest
