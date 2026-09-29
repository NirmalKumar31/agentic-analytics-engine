"""The three axes the corpus covers, as closed vocabularies.

Named rather than described in prose so coverage can be counted, and so a
case that claims a structural family the corpus has no generator for fails
rather than quietly reducing coverage.
"""

from __future__ import annotations

from enum import StrEnum


class Domain(StrEnum):
    """What the data is about. Subject matter drives vocabulary, which is
    what the question-to-column mapping has to cope with."""

    RETAIL = "retail_sales"
    SAAS = "saas_subscriptions"
    MARKETING = "marketing_campaigns"
    SUPPORT = "support_tickets"
    INVENTORY = "inventory_movements"
    PROCUREMENT = "procurement_orders"
    LOGISTICS = "logistics_shipments"
    MANUFACTURING = "manufacturing_batches"
    QUALITY = "quality_inspections"
    FINANCE = "finance_budgets"
    ACCOUNTING = "accounting_ledger"
    BANKING = "banking_transactions"
    INSURANCE = "insurance_claims"
    HEALTHCARE = "healthcare_operations"
    HR = "hr_headcount"
    RECRUITING = "recruiting_pipeline"
    EDUCATION = "education_enrolment"
    IOT = "iot_sensor_readings"
    ENERGY = "energy_consumption"
    TELECOM = "telecom_usage"
    WEB = "web_analytics"
    PRODUCT = "product_analytics"
    EXPERIMENT = "ab_experiments"
    SECURITY = "security_events"
    PROJECTS = "project_tasks"
    ENGINEERING = "engineering_delivery"
    REAL_ESTATE = "real_estate_portfolio"
    HOSPITALITY = "hospitality_reservations"
    RESTAURANT = "restaurant_operations"
    TRANSPORT = "transport_trips"
    GOVERNMENT = "public_service_requests"
    NONPROFIT = "nonprofit_fundraising"
    MEDIA = "media_engagement"
    RETURNS = "ecommerce_returns"
    MARKETPLACE = "marketplace_transactions"
    AGRICULTURE = "agriculture_yield"
    ENVIRONMENT = "environmental_monitoring"
    SURVEY = "survey_responses"
    GEOLOGY = "geology_core_assays"
    AVIATION = "aviation_flight_legs"


class Family(StrEnum):
    """The shape of the table, independent of what it is about.

    Shape is what actually breaks a mapping: a snapshot with one row per
    entity and an event log with many rows per entity answer "how many"
    differently, and a table with no trustworthy measure cannot answer it
    at all.
    """

    TRANSACTION_LEDGER = "transaction_ledger"
    ENTITY_SNAPSHOT = "entity_snapshot"
    EVENT_LOG = "append_only_event_log"
    REGULAR_TIMESERIES = "regular_timeseries"
    IRREGULAR_TIMESERIES = "irregular_timeseries_with_gaps"
    COHORT = "cohort_table"
    FUNNEL = "funnel_stage_table"
    BALANCE_SNAPSHOT = "inventory_balance_snapshot"
    BUDGET_VS_ACTUAL = "budget_versus_actual"
    TREATMENT_CONTROL = "treatment_control"
    PANEL = "repeated_measures_panel"
    WIDE_SURVEY = "wide_survey"
    LONG_KEY_VALUE = "long_form_key_value"
    HIERARCHICAL = "hierarchical_categories"
    PARENT_CHILD = "parent_child_in_one_table"
    MIXED_GRANULARITY = "mixed_granularity_rows"
    DENORMALISED = "denormalised_export"
    SPARSE_FEATURES = "sparse_feature_table"
    NO_MEASURE = "no_trustworthy_measure"
    NO_DIMENSION = "no_trustworthy_dimension"
    NO_TIME_FIELD = "no_time_field"
    AMBIGUOUS_EVERYTHING = "several_equally_plausible_columns"
    BOUNDARY_SIZED = "boundary_sized"
    #: A row describes a *span*, not a point: a depth interval, a
    #: departure-to-arrival window. The bound columns read as numbers or
    #: dates and are not measures -- "total from_depth_m" is meaningless,
    #: and an average over an intensity needs weighting by the span it was
    #: measured over. No other family puts a range in the grain.
    INTERVAL_GRAINED = "interval_grained_rows"


class QuestionKind(StrEnum):
    """What is being asked, and therefore what a correct outcome is.

    The expected outcome is a property of the kind, not of the dataset:
    an ambiguous question must be refused whatever it is about.
    """

    ANSWERABLE = "clearly_answerable"
    AMBIGUOUS = "ambiguous_must_refuse"
    IRRELEVANT = "irrelevant_publishes_nothing"
    CAUSAL = "unsupported_causal"
    NEEDS_MEASURE_AND_DIMENSION = "requires_explicit_measure_and_dimension"
    PERIOD_SENSITIVE = "period_sensitive"
    SYNONYM = "terminology_differs_from_columns"
    PHANTOM_COLUMN = "references_nonexistent_column"
    EMPTY_RESULT = "zero_matching_rows"
    NULL_HEAVY = "nulls_or_incomplete_groups"
    #: A question that restricts the rows before aggregating. Its own kind
    #: because the failure it guards against is invisible: a dropped
    #: restriction returns real numbers for a population nobody asked
    #: about, which looks exactly like an answer.
    NUMERIC_RANGE = "numeric_range_filter"
    STRICT_BOUND = "strictly_bounded_filter"
    CATEGORY_FILTER = "categorical_equality_filter"
    MULTI_FILTER = "several_filters_at_once"
    FILTER_AND_PERIOD = "numeric_filter_with_period"
    EMPTY_POPULATION = "filter_matches_no_rows"
    ABSENT_FILTER_COLUMN = "filter_on_a_column_that_is_absent"
    AMBIGUOUS_FILTER = "filter_column_cannot_be_chosen"
    MALFORMED_CONSTRAINT = "constraint_cannot_be_read"


class Outcome(StrEnum):
    """What the engine is allowed to do with a case.

    `VERIFIED_ANSWER` and `SAFE_REFUSAL` are both successes. So is
    `NO_FINDINGS`, which is the right answer to a question this dataset
    cannot support. Anything else is a failure of the contract.
    """

    VERIFIED_ANSWER = "verified_answer"
    SAFE_REFUSAL = "safe_refusal"
    NO_FINDINGS = "zero_finding_completion"
