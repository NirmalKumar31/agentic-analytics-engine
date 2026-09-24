"""Shared fixtures.

The demo warehouse is generated once per test session into a temporary
directory at a reduced size. Tests that need the full-size warehouse are the
exception and say so.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from agentic_analytics.data.generator import GeneratorConfig, generate_warehouse
from agentic_analytics.warehouse.session import AnalysisSession, open_demo_session

TEST_CONFIG = GeneratorConfig(n_customers=6_000, n_products=250, seed=4242)


@pytest.fixture(scope="session")
def warehouse_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("commerce")
    generate_warehouse(out, TEST_CONFIG)
    return out


@pytest.fixture
def session(warehouse_dir: Path) -> Iterator[AnalysisSession]:
    s = open_demo_session(warehouse_dir)
    try:
        yield s
    finally:
        s.close()
