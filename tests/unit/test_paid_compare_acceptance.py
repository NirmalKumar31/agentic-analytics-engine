"""Regression tests for the paid hosted-acceptance script's contract checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _script_module() -> object:
    path = Path(__file__).parents[2] / "scripts" / "paid_compare_acceptance.py"
    spec = importlib.util.spec_from_file_location("paid_compare_acceptance", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_paid_acceptance_checks_the_plural_canonical_dimension() -> None:
    script = _script_module()
    assert script._has_exact_dimension({"dimensions": ["Promo_Flag"]}, "Promo_Flag")
    assert not script._has_exact_dimension({"dimension": "Promo_Flag"}, "Promo_Flag")
    assert not script._has_exact_dimension({"dimensions": ["Other"]}, "Promo_Flag")
