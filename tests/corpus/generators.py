"""Synthetic datasets for the upload acceptance corpus.

Every value here is invented. People, organisations, accounts, patients and
identifiers are visibly fictional, and nothing is derived from a real
record: a healthcare fixture has departments and wait times and no person
in it at all.

The point of the corpus is *semantic* diversity, so the generators differ
in the things that actually break a question-to-column mapping:

* grain -- one row per entity, per event, per entity-and-period;
* types -- integers, money, ratios, durations, booleans, categoricals,
  Likert scales, timestamps, dates, and columns with none of those;
* distributions -- skewed money, bimodal durations, sparse counters;
* null patterns -- absent at random, absent by group, absent by period;
* ambiguity -- several plausible measures, several plausible dates, no
  plausible measure at all.

Deterministic: every generator takes a seed and produces the same bytes for
the same seed, so a failing case can be reproduced exactly.
"""

from __future__ import annotations

import csv
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from tests.corpus.kinds import Domain, Family

#: Visibly fictional name fragments. Used for entity labels so that no
#: fixture can be mistaken for real data even at a glance.
_FAKE = ("Zyrex", "Quobble", "Farnly", "Vextor", "Umbriel", "Plinth", "Noxis", "Grellow")


@dataclass(frozen=True)
class Dataset:
    """One generated table and what it is."""

    name: str
    domain: Domain
    family: Family
    header: list[str]
    rows: list[list[object]]

    def write_csv(self, directory: Path) -> Path:
        path = directory / f"{self.name}.csv"
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(self.header)
            writer.writerows(self.rows)
        return path

    def write_parquet(self, directory: Path) -> Path:
        """Parquet via DuckDB, which the engine already depends on.

        Avoids adding pyarrow table construction to the test path, and
        exercises the same reader the upload route uses.
        """
        import duckdb

        csv_path = self.write_csv(directory)
        path = directory / f"{self.name}.parquet"
        con = duckdb.connect()
        try:
            # `COPY ... TO` does not accept a placeholder for its target, so
            # the paths are inlined. Both are temporary paths this module
            # generated; nothing here comes from a question or an upload.
            source = str(csv_path).replace("'", "''")
            target = str(path).replace("'", "''")
            con.execute(
                f"copy (select * from read_csv_auto('{source}', header=true)) "
                f"to '{target}' (format parquet)"
            )
        finally:
            con.close()
        csv_path.unlink()
        return path


def _dates(start: date, count: int, step_days: int = 1) -> list[date]:
    return [start + timedelta(days=i * step_days) for i in range(count)]


def _money(rng: random.Random, low: float = 5.0, high: float = 4000.0) -> float:
    """Right-skewed, like most money. A uniform spend column is a column
    no real question has ever been asked about.

    A floor is applied because a log-uniform draw needs a positive lower
    bound, and some callers legitimately want zero-inclusive amounts.
    """
    floor = max(low, 0.01)
    value = floor * (high / floor) ** (rng.random() ** 2)
    return round(value if low > 0 else value - floor, 2)


# ─────────────────────────────────────────────────────── transaction ledgers
def retail_orders(rng: random.Random, n: int = 400) -> Dataset:
    days = _dates(date(2025, 1, 1), 330)
    header = [
        "order_ref",
        "placed_on",
        "store_code",
        "product_line",
        "units_sold",
        "gross_amount",
        "discount_pct",
        "returned",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        gross = _money(rng, 8, 900)
        rows.append(
            [
                f"ORD-{100000 + i}",
                rng.choice(days).isoformat(),
                rng.choice(["ST-NORTH", "ST-SOUTH", "ST-EAST", "ST-WEST"]),
                rng.choice(["Footwear", "Outerwear", "Accessories", "Homeware"]),
                rng.randint(1, 9),
                gross,
                rng.choice([0.0, 0.05, 0.1, 0.2]),
                rng.random() < 0.08,
            ]
        )
    return Dataset("retail_orders", Domain.RETAIL, Family.TRANSACTION_LEDGER, header, rows)


def banking_transactions(rng: random.Random, n: int = 500) -> Dataset:
    """Signed amounts, so a naive sum is meaningful and a naive average is
    not. Accounts are fictional by construction."""
    days = _dates(date(2025, 3, 1), 200)
    header = [
        "txn_id",
        "posted_date",
        "account_ref",
        "txn_category",
        "direction",
        "amount_gbp",
        "channel",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        outgoing = rng.random() < 0.72
        rows.append(
            [
                f"TXN-{i:06d}",
                rng.choice(days).isoformat(),
                f"ACC-FICTIONAL-{rng.randint(1, 40):03d}",
                rng.choice(["Groceries", "Transport", "Utilities", "Salary", "Transfer"]),
                "debit" if outgoing else "credit",
                round(-_money(rng, 2, 600) if outgoing else _money(rng, 400, 3200), 2),
                rng.choice(["card", "direct_debit", "faster_payment", "atm"]),
            ]
        )
    return Dataset("banking_transactions", Domain.BANKING, Family.TRANSACTION_LEDGER, header, rows)


def accounting_ledger(rng: random.Random, n: int = 360) -> Dataset:
    """Debits and credits in separate columns: two plausible measures where
    only their difference means anything."""
    days = _dates(date(2025, 1, 1), 300)
    header = [
        "entry_no",
        "entry_date",
        "account_code",
        "account_group",
        "debit_amount",
        "credit_amount",
        "cost_centre",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        debit = rng.random() < 0.5
        value = _money(rng, 10, 5000)
        rows.append(
            [
                i + 1,
                rng.choice(days).isoformat(),
                f"{rng.randint(1000, 8999)}",
                rng.choice(["Assets", "Liabilities", "Income", "Expenses"]),
                value if debit else 0.0,
                0.0 if debit else value,
                rng.choice(["CC-OPS", "CC-SALES", "CC-ADMIN"]),
            ]
        )
    return Dataset("accounting_ledger", Domain.ACCOUNTING, Family.TRANSACTION_LEDGER, header, rows)


def marketplace_transactions(rng: random.Random, n: int = 420) -> Dataset:
    header = [
        "listing_ref",
        "sold_on",
        "seller_ref",
        "buyer_region",
        "item_category",
        "sale_price",
        "platform_fee",
        "days_listed",
    ]
    days = _dates(date(2024, 9, 1), 420)
    rows: list[list[object]] = []
    for i in range(n):
        price = _money(rng, 3, 1500)
        rows.append(
            [
                f"LST-{i:05d}",
                rng.choice(days).isoformat(),
                f"{rng.choice(_FAKE)}-Trading",
                rng.choice(["Mainland", "Islands", "Overseas"]),
                rng.choice(["Vinyl", "Cameras", "Bicycles", "Books"]),
                price,
                round(price * 0.09, 2),
                rng.randint(0, 90),
            ]
        )
    return Dataset(
        "marketplace_transactions", Domain.MARKETPLACE, Family.TRANSACTION_LEDGER, header, rows
    )


# ────────────────────────────────────────────────────────── entity snapshots
def saas_accounts(rng: random.Random, n: int = 260) -> Dataset:
    """One row per account. MRR is a level, not a sum over time, so summing
    it across rows is meaningful and summing it across months is not."""
    header = [
        "account_ref",
        "signed_on",
        "plan_tier",
        "seats",
        "mrr_gbp",
        "lifecycle_stage",
        "churned_on",
        "expansion_events",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        signed = date(2023, 1, 1) + timedelta(days=rng.randint(0, 900))
        churned = signed + timedelta(days=rng.randint(30, 700)) if rng.random() < 0.22 else None
        rows.append(
            [
                f"ACCT-{i:04d}",
                signed.isoformat(),
                rng.choice(["Free", "Team", "Business", "Enterprise"]),
                rng.choice([1, 3, 5, 12, 40, 120]),
                round(rng.choice([0, 29, 99, 499, 2400]) * rng.uniform(0.9, 1.4), 2),
                rng.choice(["trial", "active", "dormant", "churned"]),
                churned.isoformat() if churned else "",
                rng.randint(0, 6),
            ]
        )
    return Dataset("saas_accounts", Domain.SAAS, Family.ENTITY_SNAPSHOT, header, rows)


def hr_headcount(rng: random.Random, n: int = 300) -> Dataset:
    """Compensation is sensitive-shaped even when synthetic, so a
    group-of-one aggregate here is the documented identifying case."""
    header = [
        "employee_ref",
        "joined_on",
        "department",
        "job_family",
        "grade",
        "annual_salary",
        "tenure_months",
        "promoted_last_year",
        "left_on",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        joined = date(2018, 1, 1) + timedelta(days=rng.randint(0, 2500))
        left = joined + timedelta(days=rng.randint(200, 1800)) if rng.random() < 0.18 else None
        rows.append(
            [
                f"EMP-{i:04d}",
                joined.isoformat(),
                rng.choice(["Engineering", "Support", "Finance", "People", "Field Ops"]),
                rng.choice(["IC", "Manager", "Specialist"]),
                rng.choice(["G3", "G4", "G5", "G6"]),
                round(rng.uniform(28000, 145000), 2),
                rng.randint(1, 84),
                rng.random() < 0.13,
                left.isoformat() if left else "",
            ]
        )
    return Dataset("hr_headcount", Domain.HR, Family.ENTITY_SNAPSHOT, header, rows)


def real_estate_portfolio(rng: random.Random, n: int = 180) -> Dataset:
    header = [
        "property_ref",
        "acquired_on",
        "asset_class",
        "region_name",
        "units",
        "monthly_rent_roll",
        "occupancy_rate",
        "open_maintenance_jobs",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        rows.append(
            [
                f"PROP-{i:04d}",
                (date(2015, 1, 1) + timedelta(days=rng.randint(0, 3600))).isoformat(),
                rng.choice(["Residential", "Retail", "Light Industrial"]),
                rng.choice(["Harbourside", "Uplands", "Old Quarter", "Fenmoor"]),
                rng.randint(1, 60),
                round(rng.uniform(600, 48000), 2),
                round(rng.uniform(0.55, 1.0), 3),
                rng.randint(0, 14),
            ]
        )
    return Dataset(
        "real_estate_portfolio", Domain.REAL_ESTATE, Family.ENTITY_SNAPSHOT, header, rows
    )


def insurance_policies(rng: random.Random, n: int = 240) -> Dataset:
    header = [
        "policy_ref",
        "written_on",
        "product",
        "risk_band",
        "annual_premium",
        "claims_filed",
        "claims_paid_total",
        "policy_status",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        filed = rng.choice([0, 0, 0, 1, 1, 2, 4])
        rows.append(
            [
                f"POL-{i:05d}",
                (date(2022, 1, 1) + timedelta(days=rng.randint(0, 1100))).isoformat(),
                rng.choice(["Household", "Motor", "Travel", "Smallholding"]),
                rng.choice(["A", "B", "C", "D"]),
                round(rng.uniform(90, 2600), 2),
                filed,
                round(sum(_money(rng, 50, 9000) for _ in range(filed)), 2),
                rng.choice(["in_force", "lapsed", "cancelled"]),
            ]
        )
    return Dataset("insurance_policies", Domain.INSURANCE, Family.ENTITY_SNAPSHOT, header, rows)


# ────────────────────────────────────────────────────────────── event logs
def support_tickets(rng: random.Random, n: int = 380) -> Dataset:
    """Resolution time is bimodal: most tickets close fast, a tail does
    not. A mean over this is misleading and a median is not, which is the
    kind of thing the engine must not silently choose."""
    header = [
        "ticket_ref",
        "opened_at",
        "queue",
        "priority",
        "resolution_hours",
        "satisfaction_score",
        "reopened",
        "first_response_minutes",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        slow = rng.random() < 0.18
        opened = date(2025, 2, 1) + timedelta(days=rng.randint(0, 210))
        rows.append(
            [
                f"TKT-{i:05d}",
                f"{opened.isoformat()} {rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:00",
                rng.choice(["Billing", "Technical", "Onboarding", "Data"]),
                rng.choice(["P1", "P2", "P3", "P4"]),
                round(rng.uniform(40, 400) if slow else rng.uniform(0.2, 12), 2),
                rng.choice([1, 2, 3, 4, 5, None]),
                rng.random() < 0.09,
                rng.randint(1, 600),
            ]
        )
    return Dataset("support_tickets", Domain.SUPPORT, Family.EVENT_LOG, header, rows)


def security_events(rng: random.Random, n: int = 450) -> Dataset:
    header = [
        "event_id",
        "detected_at",
        "asset_group",
        "severity",
        "rule_name",
        "resolved_minutes",
        "false_positive",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        detected = date(2025, 5, 1) + timedelta(days=rng.randint(0, 120))
        rows.append(
            [
                f"EVT-{i:06d}",
                f"{detected.isoformat()} {rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:00",
                rng.choice(["edge", "core", "endpoint", "identity"]),
                rng.choice(["low", "medium", "high", "critical"]),
                rng.choice(["impossible_travel", "brute_force", "stale_cert", "port_scan"]),
                rng.randint(1, 4000),
                rng.random() < 0.31,
            ]
        )
    return Dataset("security_events", Domain.SECURITY, Family.EVENT_LOG, header, rows)


def web_sessions(rng: random.Random, n: int = 520) -> Dataset:
    header = [
        "session_id",
        "started_at",
        "traffic_source",
        "landing_path",
        "device_kind",
        "pages_viewed",
        "session_seconds",
        "converted",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        started = date(2025, 6, 1) + timedelta(days=rng.randint(0, 90))
        rows.append(
            [
                f"SES-{i:06d}",
                f"{started.isoformat()} {rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:00",
                rng.choice(["organic", "paid_social", "referral", "email", "direct"]),
                rng.choice(["/", "/pricing", "/docs", "/blog/launch"]),
                rng.choice(["desktop", "mobile", "tablet"]),
                rng.randint(1, 24),
                rng.randint(3, 2400),
                rng.random() < 0.06,
            ]
        )
    return Dataset("web_sessions", Domain.WEB, Family.EVENT_LOG, header, rows)


def engineering_delivery(rng: random.Random, n: int = 300) -> Dataset:
    header = [
        "item_key",
        "created_at",
        "work_type",
        "service_name",
        "cycle_time_hours",
        "review_comments",
        "deployed",
        "caused_incident",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        created = date(2025, 1, 6) + timedelta(days=rng.randint(0, 240))
        rows.append(
            [
                f"ENG-{i:05d}",
                created.isoformat(),
                rng.choice(["feature", "bug", "chore", "security"]),
                rng.choice(["gateway", "billing-api", "web-app", "etl"]),
                round(rng.uniform(0.5, 320), 2),
                rng.randint(0, 28),
                rng.random() < 0.82,
                rng.random() < 0.05,
            ]
        )
    return Dataset("engineering_delivery", Domain.ENGINEERING, Family.EVENT_LOG, header, rows)


def public_service_requests(rng: random.Random, n: int = 340) -> Dataset:
    header = [
        "case_ref",
        "received_on",
        "service_area",
        "channel",
        "processing_days",
        "outcome",
        "escalated",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        rows.append(
            [
                f"CASE-{i:06d}",
                (date(2025, 1, 1) + timedelta(days=rng.randint(0, 300))).isoformat(),
                rng.choice(["Waste", "Housing", "Licensing", "Highways"]),
                rng.choice(["online", "phone", "in_person", "post"]),
                rng.randint(1, 120),
                rng.choice(["resolved", "referred", "withdrawn"]),
                rng.random() < 0.14,
            ]
        )
    return Dataset("public_service_requests", Domain.GOVERNMENT, Family.EVENT_LOG, header, rows)


# ──────────────────────────────────────────────────────────── time series
def energy_meter_readings(rng: random.Random, n: int = 365) -> Dataset:
    """Regularly sampled, one reading per facility per day, with a seasonal
    shape so a period comparison is meaningful."""
    header = ["reading_date", "facility_code", "kwh_consumed", "peak_demand_kw", "unit_cost_gbp"]
    rows: list[list[object]] = []
    for day in _dates(date(2025, 1, 1), n):
        seasonal = 1.0 + 0.35 * ((day.month - 6) / 6.0) ** 2
        for facility in ("FAC-A", "FAC-B", "FAC-C"):
            rows.append(
                [
                    day.isoformat(),
                    facility,
                    round(rng.uniform(800, 2400) * seasonal, 1),
                    round(rng.uniform(40, 190) * seasonal, 1),
                    round(rng.uniform(0.11, 0.34), 4),
                ]
            )
    return Dataset("energy_meter_readings", Domain.ENERGY, Family.REGULAR_TIMESERIES, header, rows)


def iot_sensor_readings(rng: random.Random, n: int = 600) -> Dataset:
    """Irregular: gaps where a device was offline, which is what makes a
    "trend over time" question genuinely hard."""
    header = ["observed_at", "device_ref", "metric_name", "metric_value", "alert_state"]
    rows: list[list[object]] = []
    clock = date(2025, 4, 1)
    for _ in range(n):
        clock = clock + timedelta(days=rng.choice([0, 0, 1, 1, 3, 11]))
        rows.append(
            [
                f"{clock.isoformat()} {rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:00",
                f"DEV-{rng.randint(1, 18):03d}",
                rng.choice(["temperature_c", "humidity_pct", "vibration_mm_s"]),
                round(rng.uniform(-4, 96), 3),
                rng.choice(["ok", "ok", "ok", "warning", "alarm"]),
            ]
        )
    return Dataset("iot_sensor_readings", Domain.IOT, Family.IRREGULAR_TIMESERIES, header, rows)


def environmental_monitoring(rng: random.Random, n: int = 420) -> Dataset:
    header = [
        "sampled_on",
        "site_name",
        "pollutant",
        "concentration_ug_m3",
        "threshold_ug_m3",
        "exceeded",
    ]
    rows: list[list[object]] = []
    for day in _dates(date(2025, 1, 1), n // 3):
        for pollutant, threshold in (("NO2", 40.0), ("PM10", 50.0), ("SO2", 20.0)):
            value = round(rng.uniform(2, threshold * 1.6), 2)
            rows.append(
                [
                    day.isoformat(),
                    rng.choice(["Kiln Lane", "Harbour Road", "Fenmoor Park"]),
                    pollutant,
                    value,
                    threshold,
                    value > threshold,
                ]
            )
    return Dataset(
        "environmental_monitoring", Domain.ENVIRONMENT, Family.REGULAR_TIMESERIES, header, rows
    )


def telecom_usage(rng: random.Random, n: int = 400) -> Dataset:
    header = [
        "billing_month",
        "subscriber_ref",
        "plan_name",
        "data_gb",
        "voice_minutes",
        "dropped_calls",
        "support_incidents",
    ]
    rows: list[list[object]] = []
    for month in range(1, 13):
        for _ in range(n // 12):
            rows.append(
                [
                    f"2025-{month:02d}",
                    f"SUB-{rng.randint(1, 120):05d}",
                    rng.choice(["Basic", "Standard", "Unlimited"]),
                    round(rng.uniform(0.2, 180), 2),
                    rng.randint(0, 2400),
                    rng.randint(0, 22),
                    rng.randint(0, 5),
                ]
            )
    return Dataset("telecom_usage", Domain.TELECOM, Family.PANEL, header, rows)


# ─────────────────────────────────────────── cohorts, funnels and panels
def product_retention_cohorts(rng: random.Random, n: int = 0) -> Dataset:
    """One row per cohort per period offset -- the shape where "retention"
    is a ratio of two columns and not a column of its own."""
    header = ["cohort_month", "period_offset", "cohort_size", "active_users", "feature_adopters"]
    rows: list[list[object]] = []
    for month in range(1, 10):
        size = rng.randint(120, 900)
        for offset in range(0, 7):
            active = int(size * (0.92**offset) * rng.uniform(0.9, 1.05))
            rows.append(
                [
                    f"2025-{month:02d}",
                    offset,
                    size,
                    active,
                    int(active * rng.uniform(0.1, 0.6)),
                ]
            )
    return Dataset("product_retention_cohorts", Domain.PRODUCT, Family.COHORT, header, rows)


def recruiting_funnel(rng: random.Random, n: int = 0) -> Dataset:
    header = ["requisition_ref", "stage_name", "stage_order", "candidates", "median_days_in_stage"]
    stages = (
        ("applied", 1),
        ("screened", 2),
        ("interviewed", 3),
        ("offered", 4),
        ("accepted", 5),
    )
    rows: list[list[object]] = []
    for req in range(1, 22):
        remaining = rng.randint(60, 400)
        for stage, order in stages:
            rows.append(
                [
                    f"REQ-{req:03d}",
                    stage,
                    order,
                    remaining,
                    round(rng.uniform(1, 21), 1),
                ]
            )
            remaining = max(1, int(remaining * rng.uniform(0.18, 0.6)))
    return Dataset("recruiting_funnel", Domain.RECRUITING, Family.FUNNEL, header, rows)


def ab_experiment_assignments(rng: random.Random, n: int = 500) -> Dataset:
    header = [
        "subject_ref",
        "experiment_key",
        "variant",
        "exposed_on",
        "converted",
        "revenue_per_subject",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        variant = rng.choice(["control", "treatment"])
        lift = 0.04 if variant == "treatment" else 0.0
        rows.append(
            [
                f"SUBJ-{i:06d}",
                rng.choice(["EXP-checkout-copy", "EXP-onboarding-order"]),
                variant,
                (date(2025, 7, 1) + timedelta(days=rng.randint(0, 40))).isoformat(),
                rng.random() < (0.11 + lift),
                round(_money(rng, 0, 120), 2),
            ]
        )
    return Dataset(
        "ab_experiment_assignments", Domain.EXPERIMENT, Family.TREATMENT_CONTROL, header, rows
    )


def education_enrolment(rng: random.Random, n: int = 420) -> Dataset:
    """Repeated measures: the same learner across several terms."""
    header = [
        "learner_ref",
        "term",
        "course_code",
        "attendance_pct",
        "assessment_score",
        "completed",
    ]
    rows: list[list[object]] = []
    for learner in range(1, 71):
        for term in ("2024-Autumn", "2025-Spring", "2025-Summer"):
            rows.append(
                [
                    f"LRN-{learner:04d}",
                    term,
                    rng.choice(["MATH-101", "HIST-210", "BIO-115", "ART-090"]),
                    round(rng.uniform(40, 100), 1),
                    round(rng.uniform(18, 99), 1),
                    rng.random() < 0.78,
                ]
            )
    return Dataset("education_enrolment", Domain.EDUCATION, Family.PANEL, header, rows)


# ─────────────────────────────────────────── balances, budgets, hierarchies
def inventory_balances(rng: random.Random, n: int = 260) -> Dataset:
    """A balance, not a flow. Summing on-hand across dates double-counts,
    which is the mistake a mixed measure invites."""
    header = [
        "snapshot_date",
        "sku_ref",
        "warehouse",
        "on_hand_units",
        "reorder_level",
        "units_shipped_today",
        "shortage_flag",
    ]
    rows: list[list[object]] = []
    for day in _dates(date(2025, 6, 1), 40, step_days=7):
        for sku in range(1, 8):
            on_hand = rng.randint(0, 900)
            reorder = rng.choice([50, 120, 300])
            rows.append(
                [
                    day.isoformat(),
                    f"SKU-{sku:04d}",
                    rng.choice(["WH-1", "WH-2"]),
                    on_hand,
                    reorder,
                    rng.randint(0, 120),
                    on_hand < reorder,
                ]
            )
    return Dataset("inventory_balances", Domain.INVENTORY, Family.BALANCE_SNAPSHOT, header, rows)


def finance_budget_vs_actual(rng: random.Random, n: int = 0) -> Dataset:
    header = [
        "period",
        "cost_centre",
        "expense_category",
        "budget_amount",
        "actual_amount",
        "variance_amount",
    ]
    rows: list[list[object]] = []
    for month in range(1, 13):
        for centre in ("CC-OPS", "CC-SALES", "CC-TECH", "CC-ADMIN"):
            for category in ("Payroll", "Software", "Travel", "Facilities"):
                budget = round(rng.uniform(2000, 80000), 2)
                actual = round(budget * rng.uniform(0.6, 1.5), 2)
                rows.append(
                    [
                        f"2025-{month:02d}",
                        centre,
                        category,
                        budget,
                        actual,
                        round(actual - budget, 2),
                    ]
                )
    return Dataset(
        "finance_budget_vs_actual", Domain.FINANCE, Family.BUDGET_VS_ACTUAL, header, rows
    )


def procurement_hierarchy(rng: random.Random, n: int = 300) -> Dataset:
    """Three nested category levels, so "by category" is ambiguous until
    the level is named."""
    header = [
        "po_ref",
        "raised_on",
        "supplier_name",
        "category_l1",
        "category_l2",
        "category_l3",
        "order_value",
        "days_late",
    ]
    rows: list[list[object]] = []
    tree = {
        "Indirect": {"IT": ["Laptops", "Licences"], "Facilities": ["Cleaning", "Utilities"]},
        "Direct": {"Raw": ["Steel", "Polymer"], "Packaging": ["Cartons", "Film"]},
    }
    for i in range(n):
        l1 = rng.choice(list(tree))
        l2 = rng.choice(list(tree[l1]))
        rows.append(
            [
                f"PO-{i:05d}",
                (date(2025, 1, 1) + timedelta(days=rng.randint(0, 300))).isoformat(),
                f"{rng.choice(_FAKE)} Supplies Ltd",
                l1,
                l2,
                rng.choice(tree[l1][l2]),
                _money(rng, 100, 90000),
                rng.choice([0, 0, 0, 2, 5, 21]),
            ]
        )
    return Dataset("procurement_hierarchy", Domain.PROCUREMENT, Family.HIERARCHICAL, header, rows)


def project_tasks_parent_child(rng: random.Random, n: int = 280) -> Dataset:
    """Parent and child in one flat table, so a count of rows is not a
    count of tasks unless the level is chosen."""
    header = [
        "task_ref",
        "parent_task_ref",
        "milestone",
        "owner_team",
        "status",
        "estimate_hours",
        "actual_hours",
        "due_on",
    ]
    rows: list[list[object]] = []
    parents = [f"TASK-{i:04d}" for i in range(1, 41)]
    for parent in parents:
        rows.append(
            [
                parent,
                "",
                rng.choice(["M1 Discovery", "M2 Build", "M3 Launch"]),
                rng.choice(["Platform", "Design", "Data"]),
                rng.choice(["done", "in_progress", "blocked"]),
                round(rng.uniform(4, 80), 1),
                round(rng.uniform(2, 140), 1),
                (date(2025, 3, 1) + timedelta(days=rng.randint(0, 200))).isoformat(),
            ]
        )
    for i in range(n - len(parents)):
        rows.append(
            [
                f"TASK-{1000 + i:04d}",
                rng.choice(parents),
                rng.choice(["M1 Discovery", "M2 Build", "M3 Launch"]),
                rng.choice(["Platform", "Design", "Data"]),
                rng.choice(["done", "in_progress", "blocked"]),
                round(rng.uniform(1, 24), 1),
                round(rng.uniform(0.5, 60), 1),
                (date(2025, 3, 1) + timedelta(days=rng.randint(0, 200))).isoformat(),
            ]
        )
    return Dataset("project_tasks", Domain.PROJECTS, Family.PARENT_CHILD, header, rows)


# ────────────────────────────────────── surveys, long form, sparse, denorm
def survey_responses_wide(rng: random.Random, n: int = 220) -> Dataset:
    """Likert columns, a free-text column, and a categorical. Averaging a
    Likert scale is a choice, not a fact."""
    header = [
        "response_ref",
        "submitted_on",
        "respondent_segment",
        "q1_satisfaction",
        "q2_ease",
        "q3_recommend",
        "q4_value",
        "q5_support",
        "open_comment",
    ]
    comments = (
        "Nothing to report.",
        "The onboarding felt long.",
        "Very quick once set up.",
        "",
        "Pricing page was confusing.",
    )
    rows: list[list[object]] = []
    for i in range(n):
        rows.append(
            [
                f"RESP-{i:05d}",
                (date(2025, 5, 1) + timedelta(days=rng.randint(0, 60))).isoformat(),
                rng.choice(["New", "Established", "Lapsed"]),
                *[rng.choice([1, 2, 3, 4, 5, None]) for _ in range(5)],
                rng.choice(comments),
            ]
        )
    return Dataset("survey_responses", Domain.SURVEY, Family.WIDE_SURVEY, header, rows)


def manufacturing_long_form(rng: random.Random, n: int = 480) -> Dataset:
    """Long-form key/value: the measure name is data, not a column, so no
    column is a measure on its own."""
    header = ["batch_ref", "recorded_at", "measure_name", "measure_value", "measure_unit"]
    rows: list[list[object]] = []
    for batch in range(1, 61):
        for name, unit, low, high in (
            ("yield_pct", "percent", 70, 99.5),
            ("defect_count", "count", 0, 40),
            ("downtime_minutes", "minutes", 0, 300),
            ("line_speed", "units_per_min", 20, 120),
        ):
            rows.append(
                [
                    f"BATCH-{batch:04d}",
                    (date(2025, 2, 1) + timedelta(days=batch)).isoformat(),
                    name,
                    round(rng.uniform(low, high), 2),
                    unit,
                ]
            )
    return Dataset(
        "manufacturing_measures", Domain.MANUFACTURING, Family.LONG_KEY_VALUE, header, rows
    )


def media_sparse_features(rng: random.Random, n: int = 300) -> Dataset:
    """Most cells empty. A group's average over a sparse column is an
    average over whoever happened to have a value."""
    header = [
        "publication_ref",
        "published_on",
        "content_category",
        "views",
        "comments",
        "shares",
        "newsletter_signups",
        "paywall_conversions",
        "video_completions",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        row: list[object] = [
            f"PUB-{i:05d}",
            (date(2025, 1, 1) + timedelta(days=rng.randint(0, 250))).isoformat(),
            rng.choice(["Analysis", "Interview", "Briefing", "Review"]),
            rng.randint(50, 90000),
        ]
        for _ in range(5):
            row.append(rng.randint(1, 4000) if rng.random() < 0.22 else "")
        rows.append(row)
    return Dataset("media_engagement", Domain.MEDIA, Family.SPARSE_FEATURES, header, rows)


def logistics_denormalised(rng: random.Random, n: int = 380) -> Dataset:
    """Carrier attributes repeated on every shipment row, so a mean over
    rows is weighted by shipment count rather than by carrier."""
    header = [
        "shipment_ref",
        "dispatched_on",
        "carrier_name",
        "carrier_tier",
        "carrier_hub",
        "route_code",
        "distance_km",
        "transit_hours",
        "delivered_late",
        "failed_delivery",
    ]
    carriers = {
        "Quobble Freight": ("gold", "HUB-N"),
        "Farnly Haulage": ("silver", "HUB-S"),
        "Vextor Express": ("gold", "HUB-E"),
    }
    rows: list[list[object]] = []
    for i in range(n):
        carrier = rng.choice(list(carriers))
        tier, hub = carriers[carrier]
        rows.append(
            [
                f"SHP-{i:06d}",
                (date(2025, 4, 1) + timedelta(days=rng.randint(0, 150))).isoformat(),
                carrier,
                tier,
                hub,
                f"RT-{rng.randint(1, 24):02d}",
                round(rng.uniform(8, 1400), 1),
                round(rng.uniform(2, 96), 1),
                rng.random() < 0.17,
                rng.random() < 0.03,
            ]
        )
    return Dataset("logistics_shipments", Domain.LOGISTICS, Family.DENORMALISED, header, rows)


def hospitality_mixed_granularity(rng: random.Random, n: int = 300) -> Dataset:
    """Some rows are a single booking, some are a nightly roll-up. Summing
    the revenue column mixes the two."""
    header = [
        "record_ref",
        "record_grain",
        "stay_date",
        "room_type",
        "rooms_counted",
        "revenue_amount",
        "cancelled",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        nightly = rng.random() < 0.4
        rows.append(
            [
                f"REC-{i:05d}",
                "nightly_rollup" if nightly else "single_booking",
                (date(2025, 6, 1) + timedelta(days=rng.randint(0, 120))).isoformat(),
                rng.choice(["Standard", "Double", "Suite"]),
                rng.randint(4, 40) if nightly else 1,
                round(_money(rng, 60, 9000) if nightly else _money(rng, 60, 700), 2),
                rng.random() < 0.11,
            ]
        )
    return Dataset(
        "hospitality_records", Domain.HOSPITALITY, Family.MIXED_GRANULARITY, header, rows
    )


# ──────────────────────────────────────────────── deliberately unanswerable
def quality_no_measure(rng: random.Random, n: int = 200) -> Dataset:
    """Every column is categorical or an identifier. There is nothing to
    total, so "what is the total" has no honest answer."""
    header = [
        "inspection_ref",
        "inspected_on",
        "line_code",
        "inspector_initials",
        "outcome",
        "defect_category",
        "shift",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        passed = rng.random() < 0.86
        rows.append(
            [
                f"INS-{i:05d}",
                (date(2025, 3, 1) + timedelta(days=rng.randint(0, 180))).isoformat(),
                rng.choice(["LINE-1", "LINE-2", "LINE-3"]),
                rng.choice(["AB", "CD", "EF"]),
                "pass" if passed else "fail",
                "" if passed else rng.choice(["surface", "dimension", "assembly"]),
                rng.choice(["early", "late", "night"]),
            ]
        )
    return Dataset("quality_inspections", Domain.QUALITY, Family.NO_MEASURE, header, rows)


def agriculture_no_dimension(rng: random.Random, n: int = 240) -> Dataset:
    """Numbers and a date, and every categorical column is unique per row.
    There is nothing to group by."""
    header = ["sample_ref", "sampled_on", "rainfall_mm", "yield_tonnes", "input_cost_gbp"]
    rows: list[list[object]] = []
    for i in range(n):
        rows.append(
            [
                f"SMP-{i:06d}",
                (date(2025, 1, 1) + timedelta(days=rng.randint(0, 300))).isoformat(),
                round(rng.uniform(0, 90), 1),
                round(rng.uniform(0.4, 12), 3),
                round(rng.uniform(80, 4200), 2),
            ]
        )
    return Dataset("agriculture_samples", Domain.AGRICULTURE, Family.NO_DIMENSION, header, rows)


def restaurant_no_time_field(rng: random.Random, n: int = 260) -> Dataset:
    """No date anywhere, so a trend question cannot be answered."""
    header = [
        "order_ref",
        "menu_category",
        "service_period",
        "items_count",
        "prep_minutes",
        "order_total",
        "rating",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        rows.append(
            [
                f"RST-{i:05d}",
                rng.choice(["Starters", "Mains", "Desserts", "Drinks"]),
                rng.choice(["lunch", "dinner"]),
                rng.randint(1, 12),
                round(rng.uniform(2, 55), 1),
                round(_money(rng, 6, 260), 2),
                rng.choice([1, 2, 3, 4, 5]),
            ]
        )
    return Dataset("restaurant_orders", Domain.RESTAURANT, Family.NO_TIME_FIELD, header, rows)


def transport_ambiguous(rng: random.Random, n: int = 320) -> Dataset:
    """Three plausible measures, three plausible dates and three plausible
    groupings. Nothing here can be chosen without guessing."""
    header = [
        "trip_ref",
        "booked_on",
        "started_on",
        "completed_on",
        "vehicle_class",
        "driver_group",
        "zone",
        "fare_amount",
        "distance_km",
        "duration_minutes",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        booked = date(2025, 2, 1) + timedelta(days=rng.randint(0, 200))
        rows.append(
            [
                f"TRIP-{i:06d}",
                booked.isoformat(),
                (booked + timedelta(days=rng.randint(0, 3))).isoformat(),
                (booked + timedelta(days=rng.randint(0, 4))).isoformat(),
                rng.choice(["saloon", "estate", "minibus"]),
                rng.choice(["GRP-A", "GRP-B", "GRP-C"]),
                rng.choice(["Z1", "Z2", "Z3", "Z4"]),
                round(_money(rng, 4, 180), 2),
                round(rng.uniform(0.6, 220), 2),
                rng.randint(3, 400),
            ]
        )
    return Dataset("transport_trips", Domain.TRANSPORT, Family.AMBIGUOUS_EVERYTHING, header, rows)


def ecommerce_returns_null_heavy(rng: random.Random, n: int = 340) -> Dataset:
    """Nulls by group, not at random: one channel almost never records a
    reason, so a breakdown by reason silently omits it."""
    header = [
        "return_ref",
        "returned_on",
        "order_channel",
        "return_reason",
        "refund_amount",
        "restocked",
        "days_since_purchase",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        channel = rng.choice(["web", "app", "phone", "marketplace"])
        missing_reason = channel == "marketplace" and rng.random() < 0.85
        rows.append(
            [
                f"RET-{i:05d}",
                (date(2025, 1, 1) + timedelta(days=rng.randint(0, 260))).isoformat(),
                channel,
                "" if missing_reason else rng.choice(["damaged", "wrong_size", "changed_mind"]),
                round(_money(rng, 4, 300), 2) if rng.random() < 0.9 else "",
                rng.random() < 0.7,
                rng.randint(1, 120) if rng.random() < 0.8 else "",
            ]
        )
    return Dataset("ecommerce_returns", Domain.RETURNS, Family.SPARSE_FEATURES, header, rows)


def nonprofit_donations(rng: random.Random, n: int = 300) -> Dataset:
    header = [
        "donation_ref",
        "received_on",
        "campaign_name",
        "donor_cohort",
        "amount_gbp",
        "gift_aid",
        "recurring",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        rows.append(
            [
                f"DON-{i:05d}",
                (date(2025, 1, 1) + timedelta(days=rng.randint(0, 300))).isoformat(),
                rng.choice(["Winter Appeal", "Spring Match", "Legacy"]),
                rng.choice(["first_time", "returning", "major"]),
                _money(rng, 2, 25000),
                rng.random() < 0.55,
                rng.random() < 0.3,
            ]
        )
    return Dataset("nonprofit_donations", Domain.NONPROFIT, Family.TRANSACTION_LEDGER, header, rows)


def healthcare_operations(rng: random.Random, n: int = 360) -> Dataset:
    """Operational only. No patient, no identifier, no clinical detail --
    departments, wait times and outcomes, all invented."""
    header = [
        "appointment_ref",
        "scheduled_on",
        "department",
        "appointment_type",
        "wait_days",
        "duration_minutes",
        "attended",
        "outcome_group",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        rows.append(
            [
                f"APPT-{i:06d}",
                (date(2025, 1, 6) + timedelta(days=rng.randint(0, 250))).isoformat(),
                rng.choice(["Radiology", "Physiotherapy", "Dermatology", "Audiology"]),
                rng.choice(["first", "follow_up", "review"]),
                rng.randint(0, 120),
                rng.choice([10, 15, 20, 30, 45]),
                rng.random() < 0.88,
                rng.choice(["discharged", "follow_up_booked", "referred"]),
            ]
        )
    return Dataset("healthcare_operations", Domain.HEALTHCARE, Family.EVENT_LOG, header, rows)


def marketing_campaigns(rng: random.Random, n: int = 300) -> Dataset:
    header = [
        "campaign_ref",
        "ran_from",
        "ran_to",
        "channel_name",
        "audience_segment",
        "spend_gbp",
        "impressions",
        "leads",
        "conversions",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        start = date(2025, 1, 1) + timedelta(days=rng.randint(0, 260))
        impressions = rng.randint(1200, 900000)
        leads = int(impressions * rng.uniform(0.0004, 0.02))
        rows.append(
            [
                f"CMP-{i:05d}",
                start.isoformat(),
                (start + timedelta(days=rng.randint(3, 40))).isoformat(),
                rng.choice(["search", "social", "display", "affiliate", "email"]),
                rng.choice(["prospect", "lookalike", "retarget"]),
                _money(rng, 40, 30000),
                impressions,
                leads,
                int(leads * rng.uniform(0.02, 0.35)),
            ]
        )
    return Dataset("marketing_campaigns", Domain.MARKETING, Family.MIXED_GRANULARITY, header, rows)


def restaurant_boundary_sized(rng: random.Random, n: int = 120_000) -> Dataset:
    """Large enough to exercise the row ceiling. Generated into a temporary
    directory during the test rather than committed."""
    header = ["ticket_ref", "opened_on", "section", "covers", "ticket_total"]
    rows: list[list[object]] = []
    days = _dates(date(2025, 1, 1), 300)
    for i in range(n):
        rows.append(
            [
                f"TIK-{i:07d}",
                days[i % len(days)].isoformat(),
                f"SEC-{i % 9}",
                rng.randint(1, 8),
                round(_money(rng, 8, 400), 2),
            ]
        )
    return Dataset("restaurant_boundary", Domain.RESTAURANT, Family.BOUNDARY_SIZED, header, rows)


#: Every generator, by the name a case refers to it by.
GENERATORS: dict[str, Callable[[random.Random], Dataset]] = {
    "retail_orders": retail_orders,
    "banking_transactions": banking_transactions,
    "accounting_ledger": accounting_ledger,
    "marketplace_transactions": marketplace_transactions,
    "saas_accounts": saas_accounts,
    "hr_headcount": hr_headcount,
    "real_estate_portfolio": real_estate_portfolio,
    "insurance_policies": insurance_policies,
    "support_tickets": support_tickets,
    "security_events": security_events,
    "web_sessions": web_sessions,
    "engineering_delivery": engineering_delivery,
    "public_service_requests": public_service_requests,
    "energy_meter_readings": energy_meter_readings,
    "iot_sensor_readings": iot_sensor_readings,
    "environmental_monitoring": environmental_monitoring,
    "telecom_usage": telecom_usage,
    "product_retention_cohorts": product_retention_cohorts,
    "recruiting_funnel": recruiting_funnel,
    "ab_experiment_assignments": ab_experiment_assignments,
    "education_enrolment": education_enrolment,
    "inventory_balances": inventory_balances,
    "finance_budget_vs_actual": finance_budget_vs_actual,
    "procurement_hierarchy": procurement_hierarchy,
    "project_tasks_parent_child": project_tasks_parent_child,
    "survey_responses_wide": survey_responses_wide,
    "manufacturing_long_form": manufacturing_long_form,
    "media_sparse_features": media_sparse_features,
    "logistics_denormalised": logistics_denormalised,
    "hospitality_mixed_granularity": hospitality_mixed_granularity,
    "quality_no_measure": quality_no_measure,
    "agriculture_no_dimension": agriculture_no_dimension,
    "restaurant_no_time_field": restaurant_no_time_field,
    "transport_ambiguous": transport_ambiguous,
    "ecommerce_returns_null_heavy": ecommerce_returns_null_heavy,
    "nonprofit_donations": nonprofit_donations,
    "healthcare_operations": healthcare_operations,
    "marketing_campaigns": marketing_campaigns,
    "restaurant_boundary_sized": restaurant_boundary_sized,
}


def build(name: str, seed: int = 20260928) -> Dataset:
    """One dataset, deterministically. Same seed, same bytes."""
    try:
        generator = GENERATORS[name]
    except KeyError:
        raise KeyError(f"no generator named {name!r}; have {sorted(GENERATORS)}") from None
    return generator(random.Random(seed))


# ══════════════════════════════════════════════ second home for each family
#
# A structural family that appears in exactly one domain proves only that
# the engine copes with that domain's vocabulary. These put each shape in a
# different subject area, with genuinely different columns rather than the
# same table renamed.


def education_wide_survey(rng: random.Random, n: int = 200) -> Dataset:
    """Wide survey again, but course feedback: ratings plus a duration and
    a free-text field, so the plausible measures differ in kind."""
    header = [
        "feedback_ref",
        "collected_on",
        "faculty",
        "clarity_rating",
        "pace_rating",
        "workload_rating",
        "would_retake",
        "study_hours_week",
        "comment_text",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        rows.append(
            [
                f"FB-{i:05d}",
                (date(2025, 4, 1) + timedelta(days=rng.randint(0, 80))).isoformat(),
                rng.choice(["Sciences", "Humanities", "Engineering"]),
                rng.choice([1, 2, 3, 4, 5, None]),
                rng.choice([1, 2, 3, 4, 5]),
                rng.choice([1, 2, 3, 4, 5, None]),
                rng.random() < 0.7,
                round(rng.uniform(1, 30), 1),
                rng.choice(["", "Slides were dense.", "Labs were the best part."]),
            ]
        )
    return Dataset("education_feedback", Domain.EDUCATION, Family.WIDE_SURVEY, header, rows)


def retail_denormalised(rng: random.Random, n: int = 320) -> Dataset:
    """Store attributes repeated on every line, so a per-row mean is
    weighted by line count rather than by store."""
    header = [
        "line_ref",
        "sold_on",
        "store_code",
        "store_format",
        "store_region",
        "store_opened_year",
        "sku_ref",
        "line_quantity",
        "line_value",
    ]
    stores = {
        "ST-101": ("superstore", "North", 2011),
        "ST-102": ("convenience", "North", 2019),
        "ST-201": ("superstore", "South", 2004),
    }
    rows: list[list[object]] = []
    for i in range(n):
        code = rng.choice(list(stores))
        fmt, region, year = stores[code]
        rows.append(
            [
                f"LN-{i:06d}",
                (date(2025, 2, 1) + timedelta(days=rng.randint(0, 200))).isoformat(),
                code,
                fmt,
                region,
                year,
                f"SKU-{rng.randint(1, 60):04d}",
                rng.randint(1, 14),
                round(_money(rng, 1, 260), 2),
            ]
        )
    return Dataset("retail_lines", Domain.RETAIL, Family.DENORMALISED, header, rows)


def environment_irregular_timeseries(rng: random.Random, n: int = 380) -> Dataset:
    """Gaps because a gauge was flooded, not because a device slept."""
    header = ["gauged_at", "river_reach", "level_metres", "flow_cumecs", "gauge_status"]
    rows: list[list[object]] = []
    clock = date(2025, 1, 5)
    for _ in range(n):
        clock = clock + timedelta(days=rng.choice([0, 1, 1, 2, 9, 25]))
        rows.append(
            [
                f"{clock.isoformat()} {rng.randint(0, 23):02d}:00:00",
                rng.choice(["Upper Fen", "Mill Bend", "Tidal Cut"]),
                round(rng.uniform(0.2, 5.4), 3),
                round(rng.uniform(0.5, 240), 2),
                rng.choice(["ok", "ok", "estimated", "offline"]),
            ]
        )
    return Dataset("river_gauges", Domain.ENVIRONMENT, Family.IRREGULAR_TIMESERIES, header, rows)


def saas_cohorts(rng: random.Random, n: int = 0) -> Dataset:
    """Revenue cohorts rather than user cohorts, so the retained quantity
    is money and the denominator is a starting value."""
    header = [
        "signup_quarter",
        "months_since_signup",
        "accounts_at_start",
        "accounts_retained",
        "mrr_retained_gbp",
    ]
    rows: list[list[object]] = []
    for quarter in ("2024-Q1", "2024-Q2", "2024-Q3", "2024-Q4", "2025-Q1"):
        start = rng.randint(40, 300)
        for month in range(0, 9):
            retained = int(start * (0.94**month) * rng.uniform(0.95, 1.02))
            rows.append(
                [quarter, month, start, retained, round(retained * rng.uniform(40, 220), 2)]
            )
    return Dataset("saas_cohorts", Domain.SAAS, Family.COHORT, header, rows)


def web_conversion_funnel(rng: random.Random, n: int = 0) -> Dataset:
    header = ["landing_group", "funnel_step", "step_index", "sessions", "drop_off_pct"]
    steps = (("view", 1), ("add_to_basket", 2), ("checkout", 3), ("paid", 4))
    rows: list[list[object]] = []
    for group in ("organic", "paid_social", "email", "referral"):
        remaining = rng.randint(2000, 40000)
        for step, index in steps:
            nxt = max(1, int(remaining * rng.uniform(0.2, 0.7)))
            rows.append([group, step, index, remaining, round(100 * (1 - nxt / remaining), 2)])
            remaining = nxt
    return Dataset("web_funnel", Domain.WEB, Family.FUNNEL, header, rows)


def marketing_holdout(rng: random.Random, n: int = 420) -> Dataset:
    """Treatment and control again, but a geographic holdout rather than a
    per-subject assignment."""
    header = [
        "region_code",
        "week_beginning",
        "group_label",
        "audience_size",
        "impressions_served",
        "orders_placed",
        "revenue_gbp",
    ]
    rows: list[list[object]] = []
    for week in range(0, 14):
        for region in ("R-01", "R-02", "R-03", "R-04", "R-05", "R-06"):
            treated = region in ("R-01", "R-03", "R-05")
            rows.append(
                [
                    region,
                    (date(2025, 4, 7) + timedelta(weeks=week)).isoformat(),
                    "treatment" if treated else "holdout",
                    rng.randint(8000, 60000),
                    rng.randint(0, 400000) if treated else 0,
                    rng.randint(20, 900),
                    round(_money(rng, 500, 90000), 2),
                ]
            )
    return Dataset("marketing_holdout", Domain.MARKETING, Family.TREATMENT_CONTROL, header, rows)


def banking_balances(rng: random.Random, n: int = 240) -> Dataset:
    """A balance snapshot in money rather than units: summing across dates
    double-counts the same funds."""
    header = [
        "as_at_date",
        "account_ref",
        "product_type",
        "closing_balance",
        "overdraft_limit",
        "in_arrears",
    ]
    rows: list[list[object]] = []
    for day in _dates(date(2025, 1, 31), 10, step_days=30):
        for account in range(1, 25):
            rows.append(
                [
                    day.isoformat(),
                    f"ACC-FICTIONAL-{account:03d}",
                    rng.choice(["current", "savings", "loan"]),
                    round(rng.uniform(-4000, 38000), 2),
                    rng.choice([0, 500, 1500]),
                    rng.random() < 0.07,
                ]
            )
    return Dataset("banking_balances", Domain.BANKING, Family.BALANCE_SNAPSHOT, header, rows)


def project_budget_vs_actual(rng: random.Random, n: int = 0) -> Dataset:
    header = [
        "reporting_period",
        "programme",
        "workstream",
        "planned_days",
        "actual_days",
        "planned_cost",
        "actual_cost",
    ]
    rows: list[list[object]] = []
    for month in range(1, 11):
        for programme in ("Replatform", "Compliance", "Growth"):
            for workstream in ("Delivery", "Assurance"):
                planned_days = rng.randint(20, 300)
                rows.append(
                    [
                        f"2025-{month:02d}",
                        programme,
                        workstream,
                        planned_days,
                        int(planned_days * rng.uniform(0.7, 1.6)),
                        round(planned_days * 480.0, 2),
                        round(planned_days * 480.0 * rng.uniform(0.7, 1.7), 2),
                    ]
                )
    return Dataset("project_budget", Domain.PROJECTS, Family.BUDGET_VS_ACTUAL, header, rows)


def retail_category_hierarchy(rng: random.Random, n: int = 280) -> Dataset:
    """Four nested levels this time, so "by category" is ambiguous in a
    different way from the three-level procurement tree."""
    header = [
        "sale_ref",
        "sold_on",
        "division",
        "department_name",
        "class_name",
        "subclass_name",
        "net_sales",
    ]
    rows: list[list[object]] = []
    tree = {
        "Softlines": {"Apparel": {"Tops": ["Tees", "Shirts"], "Knitwear": ["Jumpers"]}},
        "Hardlines": {"Garden": {"Tools": ["Spades", "Shears"], "Growing": ["Seeds"]}},
    }
    for i in range(n):
        division = rng.choice(list(tree))
        department = rng.choice(list(tree[division]))
        klass = rng.choice(list(tree[division][department]))
        rows.append(
            [
                f"SL-{i:06d}",
                (date(2025, 1, 1) + timedelta(days=rng.randint(0, 260))).isoformat(),
                division,
                department,
                klass,
                rng.choice(tree[division][department][klass]),
                round(_money(rng, 2, 340), 2),
            ]
        )
    return Dataset("retail_hierarchy", Domain.RETAIL, Family.HIERARCHICAL, header, rows)


def support_ticket_threads(rng: random.Random, n: int = 260) -> Dataset:
    """Parent and child again, but a ticket and its follow-ups, where the
    child rows carry no duration of their own."""
    header = [
        "message_ref",
        "thread_parent_ref",
        "queue",
        "author_kind",
        "message_length",
        "handled_minutes",
    ]
    rows: list[list[object]] = []
    parents = [f"THR-{i:04d}" for i in range(1, 46)]
    for parent in parents:
        rows.append(
            [
                parent,
                "",
                rng.choice(["Billing", "Technical", "Data"]),
                "customer",
                rng.randint(40, 900),
                round(rng.uniform(2, 180), 1),
            ]
        )
    for i in range(n - len(parents)):
        rows.append(
            [
                f"MSG-{i:05d}",
                rng.choice(parents),
                rng.choice(["Billing", "Technical", "Data"]),
                rng.choice(["agent", "customer"]),
                rng.randint(10, 600),
                "",
            ]
        )
    return Dataset("support_threads", Domain.SUPPORT, Family.PARENT_CHILD, header, rows)


def quality_long_form(rng: random.Random, n: int = 400) -> Dataset:
    """Long form again, but with a unit column whose values differ per
    measure, so a cross-measure total is meaningless."""
    header = ["check_ref", "checked_on", "characteristic", "reading", "unit", "within_tolerance"]
    rows: list[list[object]] = []
    for check in range(1, 101):
        for name, unit, low, high in (
            ("bore_diameter", "mm", 9.8, 10.2),
            ("surface_roughness", "micron", 0.2, 3.5),
            ("torque", "Nm", 8, 24),
            ("mass", "g", 190, 210),
        ):
            reading = round(rng.uniform(low, high), 3)
            rows.append(
                [
                    f"QC-{check:05d}",
                    (date(2025, 3, 1) + timedelta(days=check % 90)).isoformat(),
                    name,
                    reading,
                    unit,
                    low * 1.02 <= reading <= high * 0.98,
                ]
            )
    return Dataset("quality_characteristics", Domain.QUALITY, Family.LONG_KEY_VALUE, header, rows)


def government_no_measure(rng: random.Random, n: int = 210) -> Dataset:
    """Categoricals and dates only. Nothing to total."""
    header = [
        "notice_ref",
        "issued_on",
        "notice_type",
        "ward",
        "responsible_team",
        "status",
        "appeal_lodged",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        rows.append(
            [
                f"NOT-{i:05d}",
                (date(2025, 1, 1) + timedelta(days=rng.randint(0, 280))).isoformat(),
                rng.choice(["Planning", "Enforcement", "Licensing"]),
                rng.choice(["Fenmoor", "Harbourside", "Uplands"]),
                rng.choice(["Team A", "Team B"]),
                rng.choice(["open", "closed", "withdrawn"]),
                rng.choice(["yes", "no"]),
            ]
        )
    return Dataset("government_notices", Domain.GOVERNMENT, Family.NO_MEASURE, header, rows)


def energy_no_dimension(rng: random.Random, n: int = 220) -> Dataset:
    """Numbers and a timestamp; every label column is unique per row."""
    header = ["reading_ref", "captured_at", "voltage_v", "current_a", "power_factor"]
    rows: list[list[object]] = []
    for i in range(n):
        rows.append(
            [
                f"RD-{i:07d}",
                (date(2025, 2, 1) + timedelta(days=rng.randint(0, 200))).isoformat(),
                round(rng.uniform(228, 252), 2),
                round(rng.uniform(0.2, 64), 3),
                round(rng.uniform(0.72, 1.0), 3),
            ]
        )
    return Dataset("energy_readings_flat", Domain.ENERGY, Family.NO_DIMENSION, header, rows)


def hospitality_no_time_field(rng: random.Random, n: int = 240) -> Dataset:
    """No date column at all, so no trend is available."""
    header = [
        "reservation_ref",
        "room_type",
        "rate_plan",
        "guests",
        "nights",
        "total_charge",
        "channel",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        rows.append(
            [
                f"RSV-{i:05d}",
                rng.choice(["Standard", "Double", "Suite", "Family"]),
                rng.choice(["flexible", "advance", "corporate"]),
                rng.randint(1, 6),
                rng.randint(1, 14),
                round(_money(rng, 60, 3400), 2),
                rng.choice(["direct", "ota", "phone"]),
            ]
        )
    return Dataset(
        "hospitality_reservations", Domain.HOSPITALITY, Family.NO_TIME_FIELD, header, rows
    )


def finance_ambiguous(rng: random.Random, n: int = 300) -> Dataset:
    """Several equally plausible measures and dates, in a finance
    vocabulary rather than a transport one."""
    header = [
        "invoice_ref",
        "issued_on",
        "due_on",
        "settled_on",
        "entity",
        "currency",
        "net_amount",
        "tax_amount",
        "gross_amount",
        "days_outstanding",
    ]
    rows: list[list[object]] = []
    for i in range(n):
        issued = date(2025, 1, 1) + timedelta(days=rng.randint(0, 250))
        net = _money(rng, 40, 40000)
        tax = round(net * 0.2, 2)
        rows.append(
            [
                f"INV-{i:06d}",
                issued.isoformat(),
                (issued + timedelta(days=30)).isoformat(),
                (issued + timedelta(days=rng.randint(5, 90))).isoformat(),
                rng.choice(["Zyrex Ltd", "Quobble BV", "Farnly GmbH"]),
                rng.choice(["GBP", "EUR", "USD"]),
                net,
                tax,
                round(net + tax, 2),
                rng.randint(0, 120),
            ]
        )
    return Dataset("finance_invoices", Domain.FINANCE, Family.AMBIGUOUS_EVERYTHING, header, rows)


GENERATORS.update(
    {
        "education_wide_survey": education_wide_survey,
        "retail_denormalised": retail_denormalised,
        "environment_irregular_timeseries": environment_irregular_timeseries,
        "saas_cohorts": saas_cohorts,
        "web_conversion_funnel": web_conversion_funnel,
        "marketing_holdout": marketing_holdout,
        "banking_balances": banking_balances,
        "project_budget_vs_actual": project_budget_vs_actual,
        "retail_category_hierarchy": retail_category_hierarchy,
        "support_ticket_threads": support_ticket_threads,
        "quality_long_form": quality_long_form,
        "government_no_measure": government_no_measure,
        "energy_no_dimension": energy_no_dimension,
        "hospitality_no_time_field": hospitality_no_time_field,
        "finance_ambiguous": finance_ambiguous,
    }
)


def geology_core_assays(rng: random.Random, n: int = 340) -> Dataset:
    """Drill-core assays, where the grain is a depth interval.

    Materially different from every other fixture in one way that breaks
    mappings: `from_depth_m` and `to_depth_m` are *coordinates*. They are
    floats, they are not identifiers, and they look exactly like measures
    to anything reading types alone -- but summing them is meaningless, and
    the only additive column is the length the interval spans.

    The grades compound it. `gold_gpt` and `copper_pct` are intensities,
    not amounts: they are nulled where a sample was not assayed, so a mean
    over them is a mean over whichever intervals happened to be tested.
    """
    header = [
        "sample_ref",
        "hole_id",
        "logged_on",
        "from_depth_m",
        "to_depth_m",
        "interval_length_m",
        "lithology_code",
        "gold_gpt",
        "copper_pct",
        "assay_lab",
    ]
    lithologies = ("BSLT", "GRNT", "SCHS", "QRTZ", "GNSS", "DLRT")
    labs = ("Zyrex Assay", "Quobble Labs", "Farnly Geoscience")
    rows: list[list[object]] = []
    # Twenty-four holes, each logged as a run of contiguous intervals, so
    # the depth columns genuinely increase down a hole rather than being
    # unrelated draws.
    depth = 0.0
    hole = 0
    for i in range(n):
        if i % 14 == 0:
            hole += 1
            depth = round(rng.uniform(0, 12), 1)
        span = round(rng.uniform(0.5, 3.0), 1)
        top, bottom = depth, round(depth + span, 1)
        depth = bottom
        lab = labs[hole % len(labs)]
        # Assayed by lab: one lab reports no copper at all, so the nulls
        # are absent-by-group rather than absent-at-random.
        gold = round(rng.lognormvariate(0.1, 0.9), 3) if rng.random() > 0.18 else None
        copper = (
            round(rng.uniform(0.01, 3.4), 3)
            if lab != "Farnly Geoscience" and rng.random() > 0.1
            else None
        )
        rows.append(
            [
                f"SMP-{i:06d}",
                f"DDH-{hole:03d}",
                (date(2025, 1, 6) + timedelta(days=(i * 2) % 250)).isoformat(),
                top,
                bottom,
                span,
                lithologies[(hole + i) % len(lithologies)],
                gold,
                copper,
                lab,
            ]
        )
    return Dataset("geology_core_assays", Domain.GEOLOGY, Family.INTERVAL_GRAINED, header, rows)


def aviation_flight_legs(rng: random.Random, n: int = 420) -> Dataset:
    """Flight legs: a time span per row, and a pair of places per row.

    The same interval grain as the assay table, expressed in dates rather
    than metres, plus a structure nothing else in the corpus has -- the
    row is an *edge*. `origin_airport` and `destination_airport` are two
    columns drawn from one vocabulary, so "by airport" names neither of
    them unambiguously, and a question naming one must not be answered
    with the other.

    Cancelled legs carry no actual departure and no delay, which makes the
    nulls mean something: a mean delay that silently skips them is a mean
    over the flights that managed to leave.
    """
    header = [
        "leg_ref",
        "flight_no",
        "scheduled_departure",
        "scheduled_arrival",
        "actual_departure",
        "origin_airport",
        "destination_airport",
        "aircraft_tail",
        "leg_status",
        "block_minutes",
        "delay_minutes",
        "passengers",
    ]
    airports = ("ZYX", "QBL", "FNY", "VXT", "UMB", "PLN", "NXS", "GRW")
    rows: list[list[object]] = []
    for i in range(n):
        origin = airports[i % len(airports)]
        destination = airports[(i * 3 + 1) % len(airports)]
        if destination == origin:  # a leg never starts where it ends
            destination = airports[(i * 3 + 2) % len(airports)]
        departure = date(2025, 1, 2) + timedelta(days=(i * 2) % 260)
        block = rng.randint(45, 380)
        cancelled = rng.random() < 0.07
        delay = None if cancelled else max(0, int(rng.lognormvariate(2.2, 1.1)) - 4)
        rows.append(
            [
                f"LEG-{i:06d}",
                f"ZY{rng.randint(100, 999)}",
                departure.isoformat(),
                (departure + timedelta(days=1 if block > 300 else 0)).isoformat(),
                None if cancelled else departure.isoformat(),
                origin,
                destination,
                f"G-{_FAKE[i % len(_FAKE)][:2].upper()}{i % 12:02d}",
                "cancelled" if cancelled else ("delayed" if (delay or 0) > 15 else "on_time"),
                block,
                delay,
                0 if cancelled else rng.randint(38, 189),
            ]
        )
    return Dataset("aviation_flight_legs", Domain.AVIATION, Family.INTERVAL_GRAINED, header, rows)


GENERATORS.update(
    {
        "geology_core_assays": geology_core_assays,
        "aviation_flight_legs": aviation_flight_legs,
    }
)
