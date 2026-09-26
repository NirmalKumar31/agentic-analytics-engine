"""Synthetic datasets for real-model evaluation, deliberately varied.

The deterministic benchmark runs one warehouse the scripted provider was
written against. That is the right shape for regression safety and the wrong
shape for asking whether a real model can *operate* this architecture: a
model that only ever sees `revenue` and `category` tells you nothing about
what happens when the columns are called `spend_usd` and `campaign_channel`.

So these vary on the axes that actually stress planning and tool selection:

* **vocabulary** -- no shared column names across datasets, and none of them
  matches the demo warehouse's metric layer;
* **shape** -- wide and narrow, long and short, one with a deliberately
  awkward schema;
* **types** -- integers, floats, dates, booleans, high-cardinality strings,
  and one column that looks numeric but is an identifier;
* **format** -- one is Parquet rather than CSV.

They are small on purpose. This evaluation is about decision quality, and a
model makes the same planning mistakes on 5,000 rows as on five million.

Everything is generated from a fixed seed, so a rerun evaluates the same
data and any difference in outcome came from the model.
"""

from __future__ import annotations

import csv
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Fixed, so the datasets are the same on every run.
SEED = 4242


@dataclass(frozen=True)
class EvalQuestion:
    """One question, and what kind of demand it puts on the planner."""

    text: str
    #: aggregate | grouped | ranking | trend | statistical | ambiguous |
    #: unsupported. Used to read the results by category rather than to
    #: score an expected answer -- there is no answer key here.
    kind: str
    #: What a competent run would do. Not asserted; recorded next to what
    #: actually happened, so a human can see the gap.
    expectation: str = ""


@dataclass(frozen=True)
class EvalDataset:
    """A generated table plus the questions asked of it."""

    dataset_id: str
    description: str
    file_format: str
    build: Callable[[Path, random.Random], Path]
    questions: tuple[EvalQuestion, ...] = field(default_factory=tuple)


def _write_csv(path: Path, header: list[str], rows: list[list[Any]]) -> Path:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


# --------------------------------------------------------------- builders
def _sales(directory: Path, rng: random.Random) -> Path:
    regions = ["North", "South", "East", "West"]
    categories = ["Books", "Toys", "Home", "Garden", "Electronics"]
    rows = []
    for index in range(4_000):
        category = categories[index % len(categories)]
        # Electronics deliberately carries a lower unit price, so a ranking
        # question has a real answer rather than noise.
        base = 18.0 if category == "Electronics" else 42.0
        rows.append(
            [
                f"SO-{index:06d}",
                f"2025-{(index % 12) + 1:02d}-{(index % 27) + 1:02d}",
                regions[index % len(regions)],
                category,
                rng.randint(1, 9),
                round(base * rng.uniform(0.6, 1.8), 2),
            ]
        )
    return _write_csv(
        directory / "sales.csv",
        ["sales_order_ref", "booked_on", "territory", "product_family", "units_sold", "net_value"],
        rows,
    )


def _marketing(directory: Path, rng: random.Random) -> Path:
    channels = ["paid_search", "paid_social", "display", "affiliate", "email"]
    rows = []
    for index in range(2_600):
        channel = channels[index % len(channels)]
        spend = round(rng.uniform(80, 900), 2)
        # Display converts worst; affiliate best. A ranking or grouped
        # question therefore has a stable, checkable answer.
        rate = {"display": 0.004, "affiliate": 0.041}.get(channel, 0.016)
        clicks = max(1, int(spend * rng.uniform(0.8, 1.4)))
        rows.append(
            [
                f"2025-{(index % 12) + 1:02d}-{(index % 28) + 1:02d}",
                channel,
                f"CMP{(index % 37):03d}",
                spend,
                clicks,
                int(clicks * rate * rng.uniform(0.6, 1.5)),
            ]
        )
    return _write_csv(
        directory / "marketing.csv",
        ["activity_date", "acquisition_source", "campaign_code", "spend_usd", "clicks", "signups"],
        rows,
    )


def _shipping(directory: Path, rng: random.Random) -> Path:
    carriers = ["RapidPost", "MetroFreight", "BlueLine"]
    hubs = ["Newark", "Dallas", "Reno", "Atlanta"]
    rows = []
    for index in range(5_000):
        carrier = carriers[index % len(carriers)]
        # RapidPost is slow. A trend or ranking question should surface it.
        mean_days = 6.2 if carrier == "RapidPost" else 2.9
        days = max(1, int(rng.gauss(mean_days, 1.4)))
        rows.append(
            [
                f"SHP{index:07d}",
                carrier,
                hubs[index % len(hubs)],
                f"2025-{(index % 12) + 1:02d}-{(index % 26) + 1:02d}",
                days,
                1 if days > 5 else 0,
                round(rng.uniform(3.5, 48.0), 2),
            ]
        )
    return _write_csv(
        directory / "shipping.csv",
        [
            "consignment_id",
            "haulier",
            "origin_hub",
            "dispatched_on",
            "transit_days",
            "breached_sla",
            "freight_cost",
        ],
        rows,
    )


def _retention(directory: Path, rng: random.Random) -> Path:
    tiers = ["free", "starter", "growth", "enterprise"]
    rows = []
    for index in range(3_200):
        tier = tiers[index % len(tiers)]
        # Churn falls as the tier rises: a statistical comparison between
        # two tiers has a real difference to find.
        churn_p = {"free": 0.38, "starter": 0.22, "growth": 0.11, "enterprise": 0.05}[tier]
        rows.append(
            [
                f"ACC{index:06d}",
                tier,
                f"2024-{(index % 12) + 1:02d}-{(index % 26) + 1:02d}",
                rng.randint(1, 24),
                1 if rng.random() < churn_p else 0,
                round(rng.uniform(19, 940), 2),
                rng.choice(["EMEA", "AMER", "APAC"]),
            ]
        )
    return _write_csv(
        directory / "retention.csv",
        [
            "account_ref",
            "plan_tier",
            "signed_up_on",
            "tenure_months",
            "has_churned",
            "monthly_recurring_revenue",
            "sales_region",
        ],
        rows,
    )


def _messy(directory: Path, rng: random.Random) -> Path:
    """Awkward on purpose: the schema a real upload actually has.

    Spaces and punctuation in column names, a numeric-looking identifier, a
    column that is almost entirely null, a constant column, and a free-text
    field with one value per row. A planner that assumes tidy names or that
    every numeric column is a measure will show it here.
    """
    rows = []
    for index in range(1_800):
        rows.append(
            [
                f"{70000 + index}",  # numeric-looking, but an identifier
                rng.choice(["Alpha", "Beta", "Gamma"]),
                round(rng.uniform(10, 500), 2),
                "" if index % 9 else round(rng.uniform(1, 5), 2),
                "ACTIVE",
                f"note {index} about record {index}",
                f"2025-{(index % 12) + 1:02d}-15",
            ]
        )
    return _write_csv(
        directory / "messy.csv",
        [
            "Record #",
            "Segment Name",
            "Amount (USD)",
            "Optional Score",
            "status",
            "free text note",
            "date recorded",
        ],
        rows,
    )


def _parquet(directory: Path, rng: random.Random) -> Path:
    import pyarrow as pa
    import pyarrow.parquet as pq

    stores = [f"ST{index:03d}" for index in range(24)]
    path = directory / "inventory.parquet"
    rows = 4_500
    table = pa.table(
        {
            "sku": [f"SKU-{index % 800:04d}" for index in range(rows)],
            "store_code": [stores[index % len(stores)] for index in range(rows)],
            "counted_on": [
                f"2025-{(index % 12) + 1:02d}-{(index % 25) + 1:02d}" for index in range(rows)
            ],
            "on_hand_units": [rng.randint(0, 400) for _ in range(rows)],
            "shrinkage_units": [rng.randint(0, 12) for _ in range(rows)],
            "unit_cost": [round(rng.uniform(1.5, 90.0), 2) for _ in range(rows)],
        }
    )
    pq.write_table(table, path)
    return path


# -------------------------------------------------------------- registry
DATASETS: tuple[EvalDataset, ...] = (
    EvalDataset(
        dataset_id="sales",
        description="Order lines with territory, product family, units and value",
        file_format="csv",
        build=_sales,
        questions=(
            EvalQuestion("What is the total net value?", "aggregate", "sum of net_value"),
            EvalQuestion(
                "What is the total net value by territory?",
                "grouped",
                "sum of net_value grouped by territory",
            ),
            EvalQuestion(
                "Which product family has the highest net value?",
                "ranking",
                "ranking by summed net_value, descending",
            ),
            EvalQuestion(
                "How did net value change month by month?", "trend", "monthly series over booked_on"
            ),
            EvalQuestion(
                "How is it doing?",
                "ambiguous",
                "should ask for clarification or profile, not guess",
            ),
            EvalQuestion(
                "Which sales rep closed the most deals?",
                "unsupported",
                "no such column; must refuse rather than substitute one",
            ),
        ),
    ),
    EvalDataset(
        dataset_id="marketing",
        description="Daily ad spend, clicks and signups by acquisition source",
        file_format="csv",
        build=_marketing,
        questions=(
            EvalQuestion(
                "What is the total spend in usd by acquisition source?",
                "grouped",
                "sum of spend_usd by acquisition_source",
            ),
            EvalQuestion(
                "Which acquisition source produced the most signups?",
                "ranking",
                "ranking by summed signups",
            ),
            EvalQuestion("How did spend usd trend over time?", "trend", "monthly spend series"),
            EvalQuestion(
                "What is the average clicks by acquisition source?",
                "grouped",
                "mean of clicks by source",
            ),
            EvalQuestion(
                "Why is our marketing underperforming?",
                "ambiguous",
                "no target named; must not invent a cause",
            ),
        ),
    ),
    EvalDataset(
        dataset_id="shipping",
        description="Consignments with carrier, transit days and an SLA breach flag",
        file_format="csv",
        build=_shipping,
        questions=(
            EvalQuestion(
                "What is the average transit days by haulier?",
                "grouped",
                "mean transit_days by haulier; RapidPost is worst",
            ),
            EvalQuestion(
                "Which haulier has the highest freight cost?", "ranking", "ranking by freight_cost"
            ),
            EvalQuestion(
                "How many consignments breached sla by origin hub?",
                "grouped",
                "count or sum of breached_sla by hub",
            ),
            EvalQuestion(
                "Is the breached sla rate different between RapidPost and BlueLine?",
                "statistical",
                "two-proportion z on breached_sla; it is binary, so applicable",
            ),
            EvalQuestion("How did transit days trend over time?", "trend", "monthly series"),
        ),
    ),
    EvalDataset(
        dataset_id="retention",
        description="Accounts with plan tier, tenure, MRR and a churn flag",
        file_format="csv",
        build=_retention,
        questions=(
            EvalQuestion(
                "What is the total monthly recurring revenue by plan tier?",
                "grouped",
                "sum of MRR by tier",
            ),
            EvalQuestion(
                "Which plan tier has the highest churn?",
                "ranking",
                "ranking on has_churned; enterprise lowest, free highest",
            ),
            EvalQuestion(
                "Does the churn rate differ between the free and enterprise tiers?",
                "statistical",
                "two-proportion z on has_churned",
            ),
            EvalQuestion(
                "What is the average tenure months by sales region?", "grouped", "mean by region"
            ),
            EvalQuestion(
                "Should we raise prices?",
                "unsupported",
                "not answerable from this data; must say so",
            ),
        ),
    ),
    EvalDataset(
        dataset_id="messy",
        description="Awkward schema: spaced names, a numeric identifier, nulls, a constant",
        file_format="csv",
        build=_messy,
        questions=(
            EvalQuestion(
                "What is the total amount usd by segment name?",
                "grouped",
                "must not treat 'Record #' as a measure",
            ),
            EvalQuestion("How many records by status?", "grouped", "status is constant"),
            EvalQuestion(
                "What is the average optional score?",
                "aggregate",
                "mostly null; should still be computable or honestly refused",
            ),
            EvalQuestion(
                "Which record number is the biggest?",
                "unsupported",
                "an identifier is not a measure",
            ),
        ),
    ),
    EvalDataset(
        dataset_id="inventory_parquet",
        description="Parquet stock counts by SKU and store",
        file_format="parquet",
        build=_parquet,
        questions=(
            EvalQuestion(
                "What is the total on hand units by store code?", "grouped", "sum by store"
            ),
            EvalQuestion(
                "Which store code has the most shrinkage units?", "ranking", "ranking by shrinkage"
            ),
            EvalQuestion("What is the average unit cost?", "aggregate", "mean of unit_cost"),
            EvalQuestion(
                "How did on hand units change over time?", "trend", "monthly series over counted_on"
            ),
        ),
    ),
)

#: Questions asked of the built-in demo warehouse, which has a governed
#: metric layer and is the only dataset the scripted provider was written
#: for. Included so the evaluation covers both worlds.
WAREHOUSE_QUESTIONS: tuple[EvalQuestion, ...] = (
    EvalQuestion(
        "Revenue increased in Q3 2025, but gross margin fell. What caused it?",
        "grouped",
        "decomposition into rate and mix",
    ),
    EvalQuestion(
        "Which product category has the highest return rate?",
        "ranking",
        "Home & Kitchen",
    ),
    EvalQuestion(
        "Do shipping delays appear to affect repeat purchasing?",
        "statistical",
        "a two-proportion z, and any causal reading must be withheld",
    ),
    EvalQuestion(
        "How did monthly revenue change over 2025?",
        "trend",
        "monthly revenue series",
    ),
    EvalQuestion(
        "What should we do next quarter?",
        "unsupported",
        "a recommendation is not derivable from this data",
    ),
)


def build_all(directory: Path) -> dict[str, Path]:
    """Generate every dataset into `directory`, deterministically."""
    directory.mkdir(parents=True, exist_ok=True)
    built: dict[str, Path] = {}
    for dataset in DATASETS:
        rng = random.Random(SEED)
        built[dataset.dataset_id] = dataset.build(directory, rng)
    return built
