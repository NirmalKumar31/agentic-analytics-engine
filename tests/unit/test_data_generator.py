"""The demo warehouse must be reproducible and must actually contain the
patterns the evaluation suite looks for."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from agentic_analytics.data.generator import (
    GeneratorConfig,
    generate_warehouse,
    warehouse_fingerprint,
)
from agentic_analytics.data.ground_truth import PATTERNS, PATTERNS_BY_ID

SMALL = GeneratorConfig(n_customers=4_000, n_products=200, seed=7)


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("wh")
    generate_warehouse(out, SMALL)
    return out


@pytest.fixture(scope="module")
def con(warehouse: Path) -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    for name in (
        "customers",
        "products",
        "orders",
        "order_items",
        "returns",
        "shipping_events",
        "marketing_daily",
    ):
        c.execute(f"create view {name} as select * from read_parquet('{warehouse / name}.parquet')")
    return c


def test_same_seed_produces_identical_bytes(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    generate_warehouse(a, SMALL)
    generate_warehouse(b, SMALL)
    assert warehouse_fingerprint(a) == warehouse_fingerprint(b)
    for f in sorted(a.glob("*.parquet")):
        assert f.read_bytes() == (b / f.name).read_bytes(), f.name


def test_different_seed_produces_different_data(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    generate_warehouse(a, SMALL)
    generate_warehouse(b, GeneratorConfig(**{**SMALL.__dict__, "seed": SMALL.seed + 1}))
    assert warehouse_fingerprint(a) != warehouse_fingerprint(b)


def test_referential_integrity(con: duckdb.DuckDBPyConnection) -> None:
    orphan_items = con.execute(
        "select count(*) from order_items i left join orders o using(order_id) "
        "where o.order_id is null"
    ).fetchone()
    assert orphan_items is not None and orphan_items[0] == 0
    orphan_returns = con.execute(
        "select count(*) from returns r left join order_items i using(order_item_id) "
        "where i.order_item_id is null"
    ).fetchone()
    assert orphan_returns is not None and orphan_returns[0] == 0
    orphan_orders = con.execute(
        "select count(*) from orders o left join customers c using(customer_id) "
        "where c.customer_id is null"
    ).fetchone()
    assert orphan_orders is not None and orphan_orders[0] == 0


def test_no_negative_money(con: duckdb.DuckDBPyConnection) -> None:
    bad = con.execute(
        "select count(*) from order_items where unit_price < 0 or unit_cost < 0 or quantity <= 0"
    ).fetchone()
    assert bad is not None and bad[0] == 0


def test_pattern_q3_margin_compression(con: duckdb.DuckDBPyConnection) -> None:
    """Revenue up, gross margin down, in the injected window."""
    rows = con.execute(
        """
        select date_trunc('quarter', o.order_date) q,
               sum(i.quantity * i.unit_price) revenue,
               100 * (sum(i.quantity * i.unit_price) - sum(i.quantity * i.unit_cost))
                   / sum(i.quantity * i.unit_price) gm_pct
        from orders o join order_items i using(order_id)
        where o.status = 'completed'
          and o.order_date >= date '2025-04-01' and o.order_date < date '2025-10-01'
        group by 1 order by 1
        """
    ).fetchall()
    assert len(rows) == 2, rows
    (_, q2_rev, q2_gm), (_, q3_rev, q3_gm) = rows
    assert q3_rev > q2_rev, "Q3 revenue should rise"
    assert q2_gm - q3_gm > 3.0, f"Q3 margin should fall materially, got {q2_gm - q3_gm:.2f}pp"


def test_pattern_q3_mix_shift_to_electronics(con: duckdb.DuckDBPyConnection) -> None:
    rows = con.execute(
        """
        select date_trunc('quarter', o.order_date) q,
               100.0 * sum(case when p.category = 'Electronics' then i.quantity else 0 end)
                     / sum(i.quantity) as elec_share
        from orders o join order_items i using(order_id) join products p using(product_id)
        where o.order_date >= date '2025-04-01' and o.order_date < date '2025-10-01'
        group by 1 order by 1
        """
    ).fetchall()
    (_, q2_share), (_, q3_share) = rows
    assert q3_share > q2_share + 5.0, (q2_share, q3_share)


def test_pattern_home_kitchen_returns(con: duckdb.DuckDBPyConnection) -> None:
    rows = con.execute(
        """
        select p.category, 100.0 * count(r.return_id) / count(*) rate
        from order_items i join products p using(product_id)
        left join returns r on r.order_item_id = i.order_item_id
        group by 1 order by 2 desc
        """
    ).fetchall()
    assert rows[0][0] == "Home & Kitchen"
    assert rows[0][1] > 2.0 * rows[1][1], rows

    by_segment = con.execute(
        """
        select cu.customer_segment, 100.0 * count(r.return_id) / count(*) rate
        from order_items i join products p using(product_id)
        join orders o on o.order_id = i.order_id
        join customers cu on cu.customer_id = o.customer_id
        left join returns r on r.order_item_id = i.order_item_id
        where p.category = 'Home & Kitchen'
        group by 1 order by 2 desc
        """
    ).fetchall()
    assert by_segment[0][0] == "new", by_segment


def test_pattern_carrier_region_delay(con: duckdb.DuckDBPyConnection) -> None:
    rows = con.execute(
        """
        select carrier, region, 100.0 * avg(case when is_late then 1 else 0 end) late_pct
        from shipping_events where shipped_date >= date '2025-05-01'
        group by 1, 2 having count(*) > 50 order by 3 desc
        """
    ).fetchall()
    assert rows[0][0] == "RapidPost" and rows[0][1] == "Northeast", rows[0]
    assert rows[0][2] > 2.0 * rows[1][2], rows[:2]


def test_pattern_late_delivery_suppresses_repeat(con: duckdb.DuckDBPyConnection) -> None:
    rows = con.execute(
        """
        with first_ship as (
            select o.customer_id, s.is_late
            from orders o join shipping_events s using(order_id)
            where o.is_first_order
        ), counts as (
            select customer_id, count(*) n from orders group by 1
        )
        select f.is_late, 100.0 * avg(case when c.n > 1 then 1 else 0 end) repeat_pct
        from first_ship f join counts c using(customer_id)
        group by 1 order by 1
        """
    ).fetchall()
    on_time = next(r[1] for r in rows if not r[0])
    late = next(r[1] for r in rows if r[0])
    assert late < on_time - 5.0, (on_time, late)


def test_pattern_affiliate_weak_contribution(con: duckdb.DuckDBPyConnection) -> None:
    rows = con.execute(
        """
        select cu.acquisition_channel ch,
               sum(i.quantity * i.unit_price) revenue,
               100 * (sum(i.quantity * i.unit_price) - sum(i.quantity * i.unit_cost))
                   / sum(i.quantity * i.unit_price) gm_pct
        from orders o join order_items i using(order_id) join customers cu using(customer_id)
        where o.status = 'completed' group by 1
        """
    ).fetchall()
    by_revenue = sorted(rows, key=lambda r: -r[1])
    by_margin = sorted(rows, key=lambda r: r[2])
    # Strong on the top line, last on margin. That contrast is the pattern.
    assert by_revenue[0][0] == "affiliate", by_revenue
    assert by_margin[0][0] == "affiliate", by_margin


def test_pattern_q4_seasonality(con: duckdb.DuckDBPyConnection) -> None:
    rows = con.execute(
        "select month(order_date) m, count(*) n from orders "
        "where year(order_date) = 2025 group by 1 order by 1"
    ).fetchall()
    by_month = dict(rows)
    baseline = sum(by_month[m] for m in (3, 4, 5, 6)) / 4
    assert by_month[11] > 1.3 * baseline, by_month
    assert by_month[12] > 1.3 * baseline, by_month


def test_ground_truth_patterns_are_well_formed() -> None:
    assert len(PATTERNS) == 6
    assert len(PATTERNS_BY_ID) == 6
    for p in PATTERNS:
        assert p.description.strip()
        assert p.accept_terms, p.pattern_id
