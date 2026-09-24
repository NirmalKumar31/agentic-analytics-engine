"""Deterministic generator for the built-in commerce warehouse.

Everything is drawn from a single seeded ``numpy`` Generator, so the same seed
always produces byte-identical Parquet files. There is no network dependency
and no external dataset.

The generator also injects the phenomena listed in
:mod:`agentic_analytics.data.ground_truth`. Those injections are the reason
the demo has something to find; the answer key lives in that module and is
never shown to an agent.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from agentic_analytics.data.ground_truth import (
    MARGIN_EVENT_END,
    MARGIN_EVENT_START,
    PERIOD_END,
    PERIOD_START,
)

DEFAULT_SEED = 20260924

REGIONS = ["Northeast", "Southeast", "Midwest", "West", "Southwest"]
ACQUISITION_CHANNELS = ["organic", "paid_search", "social", "affiliate", "email", "referral"]
PAID_CHANNELS = ["paid_search", "social", "affiliate", "email"]
ORDER_CHANNELS = ["web", "mobile_app", "marketplace"]
SEGMENTS = ["new", "returning", "loyal", "vip"]
CARRIERS = ["RapidPost", "NorthStar Freight", "Vanguard Logistics", "MetroShip"]

# Cost ratio per category drives gross margin. Electronics is the deliberate
# low-margin category that the Q3 mix shift moves volume into.
CATEGORIES: dict[str, float] = {
    "Electronics": 0.78,
    "Home & Kitchen": 0.55,
    "Apparel": 0.42,
    "Beauty": 0.35,
    "Sports & Outdoors": 0.55,
    "Toys & Games": 0.50,
}
BRANDS = [
    "Northwind",
    "Contoso",
    "Fabrikam",
    "Adventure Works",
    "Tailspin",
    "Litware",
    "Proseware",
    "Wingtip",
    "Lamna",
    "Relecloud",
]

RETURN_REASONS = [
    "damaged_in_transit",
    "wrong_item",
    "not_as_described",
    "changed_mind",
    "defective",
    "arrived_late",
]

TABLE_NAMES = (
    "customers",
    "products",
    "orders",
    "order_items",
    "returns",
    "shipping_events",
    "marketing_daily",
)


@dataclass(frozen=True)
class GeneratorConfig:
    """Row-count targets. Defaults land inside the ranges the spec asks for."""

    seed: int = DEFAULT_SEED
    n_customers: int = 30_000
    n_products: int = 800
    target_orders: int = 100_000


def _day_index(d: date) -> int:
    return (d - PERIOD_START).days


def _seasonal_weights(n_days: int) -> np.ndarray:
    """Per-day demand multiplier: a Q4 peak, a smaller July bump, weekly cycle."""
    days = np.arange(n_days)
    dates = [PERIOD_START + timedelta(days=int(i)) for i in days]
    months = np.array([d.month for d in dates])
    weekdays = np.array([d.weekday() for d in dates])

    weights = np.ones(n_days, dtype=np.float64)
    weights[np.isin(months, [11, 12])] *= 1.60
    weights[months == 7] *= 1.18
    weights[np.isin(months, [1, 2])] *= 0.85
    # Weekend lift, small but consistent.
    weights[weekdays >= 5] *= 1.12
    # Gentle year-over-year growth so a YoY comparison is not flat.
    weights *= 1.0 + (days / max(n_days - 1, 1)) * 0.22
    return weights


def _sample_days_in_window(
    rng: np.random.Generator, cum: np.ndarray, lo: np.ndarray, hi: np.ndarray
) -> np.ndarray:
    """Inverse-CDF sample of a day index inside each ``[lo, hi]`` window.

    ``cum`` is the cumulative seasonal weight with a leading zero. Sampling
    through the cumulative curve keeps the seasonal shape exact instead of
    approximating it by resampling, and stays fully vectorised.
    """
    lo = np.clip(lo, 0, len(cum) - 2)
    hi = np.clip(hi, 0, len(cum) - 2)
    hi = np.maximum(hi, lo)
    base = cum[lo]
    span = cum[hi + 1] - base
    # A degenerate window (zero span) collapses to its lower bound.
    span = np.where(span <= 0, 1e-9, span)
    target = base + rng.random(len(lo)) * span
    idx = np.searchsorted(cum, target, side="right") - 1
    return np.clip(idx, lo, hi).astype(np.int32)


def _discount_rate(
    rng: np.random.Generator,
    order_day: np.ndarray,
    acq_channel_idx: np.ndarray,
) -> np.ndarray:
    """Order-level discount rate, with the Q3-2025 promotion injected."""
    rate = rng.beta(2.2, 30.0, size=len(order_day)) + 0.01

    # Affiliate traffic is bought with a coupon; that is the whole point of
    # the weak-contribution pattern.
    affiliate = acq_channel_idx == ACQUISITION_CHANNELS.index("affiliate")
    rate = np.where(affiliate, rate + rng.beta(2.0, 14.0, size=len(order_day)), rate)

    event_lo, event_hi = _day_index(MARGIN_EVENT_START), _day_index(MARGIN_EVENT_END)
    in_event = (order_day >= event_lo) & (order_day <= event_hi)
    rate = np.where(in_event, rate + rng.beta(4.0, 12.0, size=len(order_day)) * 0.21, rate)

    # Holiday promotions, present in both years so they do not confound the
    # single-quarter event.
    dates = PERIOD_START + order_day.astype("timedelta64[D]").astype(object)
    months = np.array([d.month for d in dates])
    rate = np.where(months == 12, rate + 0.03, rate)
    return np.clip(rate, 0.0, 0.62)


def generate_warehouse(out_dir: Path, config: GeneratorConfig | None = None) -> dict[str, int]:
    """Generate every table and write it as Parquet. Returns row counts."""
    cfg = config or GeneratorConfig()
    rng = np.random.default_rng(cfg.seed)
    out_dir.mkdir(parents=True, exist_ok=True)

    n_days = (PERIOD_END - PERIOD_START).days + 1
    weights = _seasonal_weights(n_days)
    cum = np.concatenate([[0.0], np.cumsum(weights)])

    # ---------------------------------------------------------------- customers
    n_cust = cfg.n_customers
    # Signups spread across the window, with a third of the base already
    # present on day zero so the first months are not artificially empty.
    preexisting = int(n_cust * 0.33)
    signup_day = np.empty(n_cust, dtype=np.int32)
    signup_day[:preexisting] = -rng.integers(1, 540, size=preexisting)
    signup_day[preexisting:] = _sample_days_in_window(
        rng,
        cum,
        np.zeros(n_cust - preexisting, dtype=np.int32),
        np.full(n_cust - preexisting, n_days - 30, dtype=np.int32),
    )
    rng.shuffle(signup_day)

    region_idx = rng.choice(len(REGIONS), size=n_cust, p=[0.22, 0.21, 0.19, 0.24, 0.14])
    acq_idx = rng.choice(
        len(ACQUISITION_CHANNELS), size=n_cust, p=[0.21, 0.19, 0.15, 0.21, 0.13, 0.11]
    )
    segment_idx = rng.choice(len(SEGMENTS), size=n_cust, p=[0.42, 0.31, 0.19, 0.08])

    customer_ids = np.arange(1, n_cust + 1, dtype=np.int64)
    customers = pa.table(
        {
            "customer_id": pa.array(customer_ids, pa.int64()),
            "signup_date": pa.array(
                [PERIOD_START + timedelta(days=int(d)) for d in signup_day], pa.date32()
            ),
            "customer_segment": pa.array([SEGMENTS[i] for i in segment_idx], pa.string()),
            "region": pa.array([REGIONS[i] for i in region_idx], pa.string()),
            "acquisition_channel": pa.array(
                [ACQUISITION_CHANNELS[i] for i in acq_idx], pa.string()
            ),
        }
    )

    # ----------------------------------------------------------------- products
    n_prod = cfg.n_products
    cat_names = list(CATEGORIES)
    prod_cat_idx = rng.choice(len(cat_names), size=n_prod, p=[0.20, 0.17, 0.22, 0.15, 0.14, 0.12])
    list_price = np.round(np.exp(rng.normal(3.35, 0.72, size=n_prod)), 2)
    list_price = np.clip(list_price, 4.99, 1899.0)
    cost_ratio = np.array([CATEGORIES[cat_names[i]] for i in prod_cat_idx])
    # Per-product jitter so a category is a tendency, not a constant.
    cost_ratio = np.clip(cost_ratio * rng.normal(1.0, 0.05, size=n_prod), 0.18, 0.92)
    unit_cost = np.round(list_price * cost_ratio, 2)
    brand_idx = rng.choice(len(BRANDS), size=n_prod)
    product_ids = np.arange(1, n_prod + 1, dtype=np.int64)
    products = pa.table(
        {
            "product_id": pa.array(product_ids, pa.int64()),
            "product_name": pa.array(
                [
                    f"{BRANDS[brand_idx[i]]} {cat_names[prod_cat_idx[i]].split()[0]} {i + 1:04d}"
                    for i in range(n_prod)
                ],
                pa.string(),
            ),
            "category": pa.array([cat_names[i] for i in prod_cat_idx], pa.string()),
            "brand": pa.array([BRANDS[i] for i in brand_idx], pa.string()),
            "list_price": pa.array(list_price, pa.float64()),
            "unit_cost": pa.array(unit_cost, pa.float64()),
        }
    )

    # ------------------------------------------------------------- first orders
    # Every customer whose signup leaves room in the window places one first
    # order; repeat counts are drawn afterwards so that a late first delivery
    # can suppress them.
    first_gap = rng.integers(0, 21, size=n_cust)
    first_day = np.maximum(signup_day, 0) + first_gap
    # Customers who signed up before the window open their account history
    # somewhere inside it rather than all on day zero, which would put a
    # spike in the first month that no seasonal term explains.
    pre_window = signup_day < 0
    n_pre = int(pre_window.sum())
    if n_pre:
        first_day[pre_window] = _sample_days_in_window(
            rng,
            cum,
            np.zeros(n_pre, dtype=np.int32),
            np.full(n_pre, n_days - 1, dtype=np.int32),
        )
    eligible = first_day <= n_days - 1
    first_day = first_day[eligible]
    first_cust = customer_ids[eligible]
    first_region = region_idx[eligible]
    first_acq = acq_idx[eligible]
    first_seg = segment_idx[eligible]

    # ------------------------------------------- first-order shipping lateness
    n_first = len(first_cust)
    carrier_p = np.array([0.30, 0.26, 0.24, 0.20])
    first_carrier = rng.choice(len(CARRIERS), size=n_first, p=carrier_p)
    first_late = _draw_lateness(rng, first_day, first_carrier, first_region)

    # --------------------------------------------------------- repeat orders
    seg_lambda = np.array([0.72, 2.72, 5.95, 11.90])
    lam = seg_lambda[first_seg].astype(np.float64)
    # Affiliate-acquired customers churn faster than their revenue suggests.
    lam *= np.where(first_acq == ACQUISITION_CHANNELS.index("affiliate"), 0.90, 1.0)
    # Injected: a late first delivery depresses the repeat rate.
    lam *= np.where(first_late, 0.42, 1.0)
    # Less remaining window means less opportunity to come back.
    remaining = np.clip(n_days - 1 - first_day, 0, None) / float(n_days)
    lam *= 0.25 + 1.25 * remaining

    n_repeat = rng.poisson(lam)
    total_repeat = int(n_repeat.sum())
    repeat_cust = np.repeat(first_cust, n_repeat)
    repeat_owner = np.repeat(np.arange(n_first), n_repeat)
    repeat_lo = np.repeat(first_day, n_repeat) + rng.integers(7, 60, size=total_repeat)
    repeat_day = _sample_days_in_window(
        rng, cum, repeat_lo, np.full(total_repeat, n_days - 1, dtype=np.int32)
    )
    repeat_region = first_region[repeat_owner]
    repeat_acq = first_acq[repeat_owner]

    # ------------------------------------------------------------ order table
    order_cust = np.concatenate([first_cust, repeat_cust])
    order_day = np.concatenate([first_day, repeat_day]).astype(np.int32)
    order_region = np.concatenate([first_region, repeat_region])
    order_acq = np.concatenate([first_acq, repeat_acq])
    is_first = np.concatenate([np.ones(n_first, bool), np.zeros(total_repeat, bool)])

    order_sort = np.lexsort((order_cust, order_day))
    order_cust = order_cust[order_sort]
    order_day = order_day[order_sort]
    order_region = order_region[order_sort]
    order_acq = order_acq[order_sort]
    is_first = is_first[order_sort]
    n_orders = len(order_cust)
    order_ids = np.arange(1_000_001, 1_000_001 + n_orders, dtype=np.int64)

    order_channel = rng.choice(len(ORDER_CHANNELS), size=n_orders, p=[0.48, 0.38, 0.14])
    discount_rate = _discount_rate(rng, order_day, order_acq)
    shipping_cost = np.round(np.clip(rng.gamma(3.0, 1.9, size=n_orders), 0.0, 39.0), 2)
    status_draw = rng.random(n_orders)
    status = np.where(
        status_draw < 0.962, "completed", np.where(status_draw < 0.988, "cancelled", "pending")
    )

    # ------------------------------------------------------------- order items
    items_per_order = rng.choice([1, 2, 3, 4], size=n_orders, p=[0.30, 0.34, 0.24, 0.12])
    # Injected: affiliate traffic converts on bigger baskets, so the channel
    # looks strong on revenue even though its contribution is the worst.
    affiliate_order = order_acq == ACQUISITION_CHANNELS.index("affiliate")
    items_per_order = items_per_order + (affiliate_order & (rng.random(n_orders) < 0.55)).astype(
        items_per_order.dtype
    )
    n_items = int(items_per_order.sum())
    item_order_pos = np.repeat(np.arange(n_orders), items_per_order)
    item_order_id = order_ids[item_order_pos]
    item_day = order_day[item_order_pos]

    # Injected: product mix shifts toward Electronics during the margin event.
    elec_idx = cat_names.index("Electronics")
    prod_choice = rng.choice(n_prod, size=n_items)
    event_lo, event_hi = _day_index(MARGIN_EVENT_START), _day_index(MARGIN_EVENT_END)
    in_event = (item_day >= event_lo) & (item_day <= event_hi)
    # Re-draw a share of in-event items from Electronics only.
    elec_products = product_ids[prod_cat_idx == elec_idx] - 1
    reroll = in_event & (rng.random(n_items) < 0.14)
    if elec_products.size:
        prod_choice = np.where(reroll, rng.choice(elec_products, size=n_items), prod_choice)

    item_product_id = product_ids[prod_choice]
    item_list_price = list_price[prod_choice]
    item_unit_cost = unit_cost[prod_choice]
    item_discount_rate = discount_rate[item_order_pos]
    item_unit_price = np.round(item_list_price * (1.0 - item_discount_rate), 2)
    quantity = rng.choice([1, 2, 3], size=n_items, p=[0.74, 0.20, 0.06]).astype(np.int64)

    order_items = pa.table(
        {
            "order_item_id": pa.array(np.arange(1, n_items + 1, dtype=np.int64), pa.int64()),
            "order_id": pa.array(item_order_id, pa.int64()),
            "product_id": pa.array(item_product_id, pa.int64()),
            "quantity": pa.array(quantity, pa.int64()),
            "unit_price": pa.array(item_unit_price, pa.float64()),
            "unit_cost": pa.array(item_unit_cost, pa.float64()),
            "list_price": pa.array(item_list_price, pa.float64()),
            "discount_amount": pa.array(
                np.round((item_list_price - item_unit_price) * quantity, 2), pa.float64()
            ),
        }
    )

    orders = pa.table(
        {
            "order_id": pa.array(order_ids, pa.int64()),
            "customer_id": pa.array(order_cust, pa.int64()),
            "order_date": pa.array(
                [PERIOD_START + timedelta(days=int(d)) for d in order_day], pa.date32()
            ),
            "channel": pa.array([ORDER_CHANNELS[i] for i in order_channel], pa.string()),
            "discount_rate": pa.array(np.round(discount_rate, 4), pa.float64()),
            "shipping_cost": pa.array(shipping_cost, pa.float64()),
            "status": pa.array(status, pa.string()),
            "is_first_order": pa.array(is_first, pa.bool_()),
        }
    )

    # --------------------------------------------------------- shipping events
    ship_carrier = rng.choice(len(CARRIERS), size=n_orders, p=carrier_p)
    # Re-use the already-drawn first-order lateness so the repeat-suppression
    # pattern stays consistent with the shipping table the agent can query.
    ship_late = _draw_lateness(rng, order_day, ship_carrier, order_region)
    # Overwrite the first orders with the lateness that drove repeat counts.
    inv = np.empty(n_orders, dtype=np.int64)
    inv[order_sort] = np.arange(n_orders)
    first_slots = inv[:n_first]
    ship_late[first_slots] = first_late
    ship_carrier[first_slots] = first_carrier

    ship_days = np.where(
        ship_late,
        rng.integers(6, 15, size=n_orders),
        rng.integers(1, 5, size=n_orders),
    ).astype(np.int64)
    promised_days = np.full(n_orders, 5, dtype=np.int64)
    shipped_day = order_day + rng.integers(0, 2, size=n_orders)
    delivered_day = shipped_day + ship_days
    shipping_events = pa.table(
        {
            "order_id": pa.array(order_ids, pa.int64()),
            "carrier": pa.array([CARRIERS[i] for i in ship_carrier], pa.string()),
            "region": pa.array([REGIONS[i] for i in order_region], pa.string()),
            "shipped_date": pa.array(
                [PERIOD_START + timedelta(days=int(d)) for d in shipped_day], pa.date32()
            ),
            "promised_days": pa.array(promised_days, pa.int64()),
            "delivered_date": pa.array(
                [PERIOD_START + timedelta(days=int(d)) for d in delivered_day], pa.date32()
            ),
            "delivery_days": pa.array(ship_days, pa.int64()),
            "is_late": pa.array(ship_late, pa.bool_()),
        }
    )

    # ----------------------------------------------------------------- returns
    returns = _build_returns(
        rng=rng,
        item_order_id=item_order_id,
        item_product_id=item_product_id,
        item_day=item_day,
        item_unit_price=item_unit_price,
        quantity=quantity,
        prod_cat_idx=prod_cat_idx[prod_choice],
        cat_names=cat_names,
        order_cust=order_cust,
        order_acq=order_acq,
        segment_idx=segment_idx,
        item_order_pos=item_order_pos,
        ship_late=ship_late,
        n_days=n_days,
    )

    # --------------------------------------------------------- marketing_daily
    marketing = _build_marketing(rng, n_days, weights)

    tables = {
        "customers": customers,
        "products": products,
        "orders": orders,
        "order_items": order_items,
        "returns": returns,
        "shipping_events": shipping_events,
        "marketing_daily": marketing,
    }
    counts: dict[str, int] = {}
    for name, table in tables.items():
        # Deterministic file bytes: no dictionary-encoding heuristics that
        # depend on data order, fixed compression, fixed row group size.
        pq.write_table(
            table,
            out_dir / f"{name}.parquet",
            compression="zstd",
            compression_level=6,
            use_dictionary=False,
            write_statistics=True,
            row_group_size=64_000,
            store_schema=True,
        )
        counts[name] = table.num_rows
    return counts


def _draw_lateness(
    rng: np.random.Generator,
    day: np.ndarray,
    carrier: np.ndarray,
    region: np.ndarray,
) -> np.ndarray:
    """Late-delivery indicator, with the RapidPost/Northeast degradation."""
    p = np.full(len(day), 0.085)
    # A mild permanent carrier ranking, so the injected shift is a change
    # rather than the only difference between carriers.
    p += np.array([0.02, 0.0, 0.005, 0.015])[carrier]
    degrade_from = _day_index(date(2025, 5, 1))
    hit = (
        (carrier == CARRIERS.index("RapidPost"))
        & (region == REGIONS.index("Northeast"))
        & (day >= degrade_from)
    )
    p = np.where(hit, p + 0.34, p)
    # Peak season strains everyone a little.
    dates = PERIOD_START + day.astype("timedelta64[D]").astype(object)
    months = np.array([d.month for d in dates])
    p = np.where(np.isin(months, [11, 12]), p + 0.05, p)
    return rng.random(len(day)) < np.clip(p, 0.0, 0.95)


def _build_returns(
    *,
    rng: np.random.Generator,
    item_order_id: np.ndarray,
    item_product_id: np.ndarray,
    item_day: np.ndarray,
    item_unit_price: np.ndarray,
    quantity: np.ndarray,
    prod_cat_idx: np.ndarray,
    cat_names: list[str],
    order_cust: np.ndarray,
    order_acq: np.ndarray,
    segment_idx: np.ndarray,
    item_order_pos: np.ndarray,
    ship_late: np.ndarray,
    n_days: int,
) -> pa.Table:
    """Item-level returns with the Home & Kitchen / new-customer concentration."""
    n_items = len(item_order_id)
    item_cust = order_cust[item_order_pos]
    item_seg = segment_idx[item_cust - 1]
    item_acq = order_acq[item_order_pos]
    item_late = ship_late[item_order_pos]

    p = np.full(n_items, 0.052)
    hk = prod_cat_idx == cat_names.index("Home & Kitchen")
    new_seg = item_seg == SEGMENTS.index("new")
    # Injected: elevated base for the category, amplified for new customers.
    p = np.where(hk, p * 2.4, p)
    p = np.where(hk & new_seg, p * 1.9, p)
    p = np.where(item_acq == ACQUISITION_CHANNELS.index("affiliate"), p * 1.45, p)
    p = np.where(item_late, p * 1.6, p)
    returned = rng.random(n_items) < np.clip(p, 0.0, 0.85)

    idx = np.flatnonzero(returned)
    n_ret = len(idx)
    reason_p_default = np.array([0.14, 0.17, 0.22, 0.27, 0.14, 0.06])
    reason_p_hk = np.array([0.52, 0.10, 0.14, 0.12, 0.09, 0.03])
    reasons = np.empty(n_ret, dtype=object)
    hk_ret = hk[idx]
    n_hk = int(hk_ret.sum())
    if n_hk:
        reasons[hk_ret] = rng.choice(RETURN_REASONS, size=n_hk, p=reason_p_hk)
    if n_ret - n_hk:
        reasons[~hk_ret] = rng.choice(RETURN_REASONS, size=n_ret - n_hk, p=reason_p_default)
    late_ret = item_late[idx]
    reasons = np.where(late_ret & (rng.random(n_ret) < 0.35), "arrived_late", reasons)

    lag = rng.integers(3, 45, size=n_ret)
    ret_day = np.clip(item_day[idx] + lag, 0, n_days - 1)
    refund = np.round(item_unit_price[idx] * quantity[idx], 2)

    return pa.table(
        {
            "return_id": pa.array(np.arange(1, n_ret + 1, dtype=np.int64), pa.int64()),
            "order_id": pa.array(item_order_id[idx], pa.int64()),
            "order_item_id": pa.array((idx + 1).astype(np.int64), pa.int64()),
            "product_id": pa.array(item_product_id[idx], pa.int64()),
            "customer_id": pa.array(item_cust[idx], pa.int64()),
            "return_date": pa.array(
                [PERIOD_START + timedelta(days=int(d)) for d in ret_day], pa.date32()
            ),
            "return_reason": pa.array([str(r) for r in reasons], pa.string()),
            "refund_amount": pa.array(refund, pa.float64()),
        }
    )


def _build_marketing(rng: np.random.Generator, n_days: int, weights: np.ndarray) -> pa.Table:
    """Daily paid-channel spend, impressions and clicks."""
    rows_date: list[date] = []
    rows_channel: list[str] = []
    rows_campaign: list[str] = []
    spend: list[float] = []
    impressions: list[int] = []
    clicks: list[int] = []

    base_spend = {"paid_search": 620.0, "social": 430.0, "affiliate": 540.0, "email": 95.0}
    cpm = {"paid_search": 11.0, "social": 6.5, "affiliate": 9.0, "email": 2.0}
    ctr = {"paid_search": 0.031, "social": 0.011, "affiliate": 0.024, "email": 0.052}

    for d in range(n_days):
        day = PERIOD_START + timedelta(days=d)
        for ch in PAID_CHANNELS:
            s = base_spend[ch] * weights[d] * rng.normal(1.0, 0.12)
            # Injected: affiliate spend climbs faster than the revenue it buys.
            if ch == "affiliate":
                s *= 1.0 + 0.45 * (d / max(n_days - 1, 1))
            s = float(max(s, 25.0))
            imp = int(max(s / cpm[ch] * 1000.0 * rng.normal(1.0, 0.08), 1))
            clk = int(max(imp * ctr[ch] * rng.normal(1.0, 0.10), 0))
            rows_date.append(day)
            rows_channel.append(ch)
            rows_campaign.append(f"{ch}_{day.year}q{(day.month - 1) // 3 + 1}")
            spend.append(round(s, 2))
            impressions.append(imp)
            clicks.append(clk)

    return pa.table(
        {
            "date": pa.array(rows_date, pa.date32()),
            "channel": pa.array(rows_channel, pa.string()),
            "campaign": pa.array(rows_campaign, pa.string()),
            "spend": pa.array(spend, pa.float64()),
            "impressions": pa.array(impressions, pa.int64()),
            "clicks": pa.array(clicks, pa.int64()),
        }
    )


def warehouse_fingerprint(directory: Path) -> str:
    """Content hash over every Parquet file, used as the dataset fingerprint.

    Hashes file bytes rather than a summary so that any change to the data --
    including one that preserves row counts -- changes the fingerprint.
    """
    digest = hashlib.sha256()
    for name in TABLE_NAMES:
        path = directory / f"{name}.parquet"
        if not path.exists():
            continue
        digest.update(name.encode())
        digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()[:32]}"
