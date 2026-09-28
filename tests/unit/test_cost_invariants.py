"""Ceilings that cannot all hold must fail at startup.

Every check here is cheap arithmetic about the configuration, and every
one of them is a mid-run failure if it is discovered later: a run that
cannot finish inside its own cost ceiling stops somewhere in the middle,
having already spent money, and reports a budget error that reads like a
bug rather than a misconfiguration.

The public-demo defaults are asserted too. They are not arbitrary: the
provider project carries a $5 hard limit, OpenAI documents that
enforcement is not instantaneous, and the application therefore has to
stop first rather than race it.
  https://developers.openai.com/api/docs/guides/spend-limits
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from agentic_analytics.config import Settings
from agentic_analytics.llm.governed import RunBudget
from agentic_analytics.llm.ledger import LedgerCaps
from agentic_analytics.llm.pricing import price_for

#: The smallest configuration that turns the paid half on.
AI_ON: dict[str, Any] = {
    "ai_analytics_enabled": True,
    "cloud_api_key": "k",
    "cloud_model": "gpt-6-luna",
}

#: The hard limit the owner sets on the provider project, in microdollars.
PROVIDER_HARD_LIMIT = 5_000_000


def _settings(**overrides: Any) -> Settings:
    return Settings(**(AI_ON | overrides))


# ----------------------------------------------------------- the defaults
def test_the_lifetime_ceiling_stops_below_the_provider_hard_limit() -> None:
    """The gap is the point.

    Hard-limit enforcement can process a little extra usage while the limit
    state propagates, so an application ceiling equal to the provider's
    would be a race rather than a bound.
    """
    total = _settings().ai_total_cost_microdollars
    assert total <= 4_000_000
    assert total < PROVIDER_HARD_LIMIT


def test_the_daily_ceiling_is_much_smaller_than_the_lifetime_one() -> None:
    """A portfolio demo spending its whole budget in a day is not being
    read, it is being harvested."""
    cfg = _settings()
    assert cfg.ai_daily_cost_microdollars <= cfg.ai_total_cost_microdollars // 4


def test_the_worst_run_fits_inside_every_ceiling_above_it() -> None:
    cfg = _settings()
    worst = price_for(cfg.cloud_model).reservation_microdollars(
        cfg.ai_max_input_tokens, cfg.ai_max_output_tokens
    )
    assert worst <= cfg.ai_max_cost_microdollars
    assert cfg.ai_max_cost_microdollars <= cfg.ai_daily_cost_microdollars
    assert cfg.ai_daily_cost_microdollars <= cfg.ai_total_cost_microdollars


def test_the_lifetime_budget_affords_a_useful_number_of_runs() -> None:
    """A ceiling so tight that the demo cannot be demonstrated is also a
    misconfiguration, just a quieter one."""
    cfg = _settings()
    worst = price_for(cfg.cloud_model).reservation_microdollars(
        cfg.ai_max_input_tokens, cfg.ai_max_output_tokens
    )
    assert cfg.ai_total_cost_microdollars // worst >= 20


# --------------------------------------------------------- the invariants
@pytest.mark.parametrize(
    ("label", "overrides", "expected"),
    [
        (
            "the verifier reserve leaves the workers nothing",
            {"ai_verification_output_reserve": 64_000, "ai_max_output_tokens": 64_000},
            "must be smaller than",
        ),
        (
            "one run cannot finish inside a day",
            {"ai_max_cost_microdollars": 900_000},
            "exceeds ai_daily_cost_microdollars",
        ),
        (
            "one day could exhaust the lifetime budget",
            {"ai_daily_cost_microdollars": 5_000_000},
            "exceeds ai_total_cost_microdollars",
        ),
        (
            "the token ceilings imply a run above the cost ceiling",
            {
                "ai_max_cost_microdollars": 5_000,
                "ai_daily_cost_microdollars": 9_000,
                "ai_total_cost_microdollars": 9_000,
            },
            "raise the cost ceiling or lower the token ceilings",
        ),
    ],
)
def test_contradictory_ceilings_are_refused_at_startup(
    label: str, overrides: dict[str, Any], expected: str
) -> None:
    with pytest.raises(ValidationError, match=expected):
        _settings(**overrides)


def test_an_unpriced_model_is_not_a_startup_error() -> None:
    """Pricing is refused at preflight, where the resolved identity is
    known. Refusing here would stop a deployment over a model name that
    preflight would have rejected with a better message."""
    assert _settings(cloud_model="gpt-6-unlisted").cloud_model == "gpt-6-unlisted"


def test_a_deterministic_deployment_ignores_all_of_this(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The free half must not be taken down by the paid half's numbers.

    A deployment with AI off never reads these ceilings, so refusing to
    start over one would remove working Deterministic Analytics because of
    a setting nothing consults.
    """
    cfg = Settings(
        ai_analytics_enabled=False,
        ai_max_cost_microdollars=1,
        ai_daily_cost_microdollars=1,
        ai_total_cost_microdollars=1,
    )
    assert cfg.ai_analytics_enabled is False


# ─────────────────────────────────── the input split, and what it must obey
def _budget(**kw: object) -> RunBudget:
    base: dict[str, object] = {
        "max_attempts": 10,
        "max_input_tokens": 1_000_000,
        "max_output_tokens": 1_000_000,
        "max_runtime_seconds": 60.0,
        "caps": LedgerCaps(
            total_microdollars=4_000_000,
            daily_microdollars=500_000,
            run_microdollars=250_000,
            runs_per_session=3,
            runs_per_client_hour=5,
        ),
    }
    return RunBudget(**(base | kw))  # type: ignore[arg-type]


def test_the_three_input_categories_sum_to_the_total() -> None:
    """`ordinary = total - cached - cache_write`.

    The three are priced differently, so a report carrying only the total
    cannot be checked against an invoice. The first paid smoke recorded
    only the total and its cache behaviour had to be inferred.
    """
    budget = _budget(input_tokens=18_094, cached_input_tokens=4_000, cache_write_input_tokens=1_094)
    assert budget.ordinary_input_tokens == 18_094 - 4_000 - 1_094
    assert (
        budget.ordinary_input_tokens + budget.cached_input_tokens + budget.cache_write_input_tokens
        == budget.input_tokens
    )
    assert budget.usage_categories_are_coherent


def test_categories_that_exceed_their_total_are_incoherent() -> None:
    """A negative ordinary count means tokens were mis-attributed, and any
    cost derived from the split is wrong."""
    budget = _budget(input_tokens=100, cached_input_tokens=80, cache_write_input_tokens=40)
    assert budget.ordinary_input_tokens < 0
    assert not budget.usage_categories_are_coherent


def test_reasoning_tokens_live_inside_the_output_total() -> None:
    """Counted for observability and never added: reasoning is already
    billed as output, and adding it would charge the same generation
    twice."""
    budget = _budget(output_tokens=4_104, reasoning_tokens=1_200)
    assert budget.usage_categories_are_coherent
    assert budget.reasoning_tokens <= budget.output_tokens

    over = _budget(output_tokens=100, reasoning_tokens=200)
    assert not over.usage_categories_are_coherent


def test_the_usage_report_carries_everything_needed_to_recompute_cost() -> None:
    budget = _budget(
        input_tokens=18_094,
        output_tokens=4_104,
        cached_input_tokens=0,
        cache_write_input_tokens=0,
        reasoning_tokens=0,
        attempts=19,
        provider_requests=19,
        reservations=19,
        settlements=19,
        settled_microdollars=3_875,
    )
    report = budget.usage_report()
    for key in (
        "input_tokens",
        "ordinary_input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "provider_requests",
        "completion_attempts",
        "reservations",
        "settlements",
        "unusable_usage_calls",
        "settled_microdollars",
        "retained_microdollars",
        "cost_is_complete",
        "usage_categories_are_coherent",
    ):
        assert key in report, key
    # And nothing that could carry a prompt, a response or a credential.
    assert all(isinstance(v, int | bool) for v in report.values())


def test_a_run_with_unreadable_usage_reports_an_incomplete_cost() -> None:
    budget = _budget(unusable_usage_calls=1, retained_microdollars=47_000)
    assert not budget.cost_is_complete
    assert budget.usage_report()["cost_is_complete"] is False
