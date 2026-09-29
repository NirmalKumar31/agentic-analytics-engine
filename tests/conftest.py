"""Shared fixtures.

The demo warehouse is generated once per test session into a temporary
directory at a reduced size. Tests that need the full-size warehouse are the
exception and say so.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from agentic_analytics.config import Settings
from agentic_analytics.data.generator import GeneratorConfig, generate_warehouse
from agentic_analytics.warehouse.session import AnalysisSession, open_demo_session

# The suite must not read the developer's own configuration.
#
# `Settings` loads `.env` by default, which is right for running the
# application and wrong for testing it: a developer who has configured AI
# mode locally gets a different suite from CI, where no `.env` exists. That
# is the worst direction for the difference to run -- it hid nothing until
# someone put a real credential in the file, and then twenty tests failed
# for reasons unrelated to any change.
#
# Cleared at import, before any test constructs a `Settings`.
Settings.model_config["env_file"] = None
for _leaked in [k for k in os.environ if k.startswith("AAE_")]:
    del os.environ[_leaked]

TEST_CONFIG = GeneratorConfig(n_customers=6_000, n_products=250, seed=4242)


@pytest.fixture(scope="session")
def warehouse_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("commerce")
    generate_warehouse(out, TEST_CONFIG)
    return out


@pytest.fixture(scope="session")
def demo_data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A `data_dir` whose `commerce/` holds a generated warehouse.

    `Settings.demo_warehouse_dir` is `data_dir / "commerce"`, and
    `mktemp("commerce")` yields `commerce0`, so passing `warehouse_dir.parent`
    as `data_dir` points at a directory that does not exist. Any test hitting
    `/api/datasets/demo` then depends on `make data` having been run first,
    which is why a clean checkout failed one security test until the demo
    warehouse was generated.
    """
    root = tmp_path_factory.mktemp("aae-data")
    generate_warehouse(root / "commerce", TEST_CONFIG)
    return root


@pytest.fixture
def session(warehouse_dir: Path) -> Iterator[AnalysisSession]:
    s = open_demo_session(warehouse_dir)
    try:
        yield s
    finally:
        s.close()
