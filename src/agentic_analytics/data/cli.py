"""Generation entry point for the built-in warehouse."""

from __future__ import annotations

import json
from pathlib import Path

from agentic_analytics.data.generator import (
    DEFAULT_SEED,
    GeneratorConfig,
    generate_warehouse,
    warehouse_fingerprint,
)


def build(out_dir: Path, seed: int = DEFAULT_SEED) -> dict[str, object]:
    """Generate the warehouse and return a manifest."""
    counts = generate_warehouse(out_dir, GeneratorConfig(seed=seed))
    manifest: dict[str, object] = {
        "seed": seed,
        "row_counts": counts,
        "dataset_fingerprint": warehouse_fingerprint(out_dir),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
