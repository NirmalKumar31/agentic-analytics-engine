"""Pricing, which is the part of a spend ceiling that can be silently wrong.

An input token is billed at exactly one of three rates -- ordinary, cache
read, or cache write, and the rates differ by more than tenfold. Nothing
before dispatch can know which a token will become, so the reservation has
to assume the dearest and settlement has to use the categories the provider
reports. Getting either half wrong produces a ceiling that holds in testing
and not in production.

Rates below are the published ones for `gpt-6-luna`, reviewed 2026-09-27:
https://developers.openai.com/api/docs/models/gpt-6-luna
"""

from __future__ import annotations

import pytest

from agentic_analytics.llm.pricing import (
    MICRODOLLARS_PER_DOLLAR,
    PRICES,
    UnknownModelPrice,
    is_priced,
    price_for,
    usage_is_coherent,
)

MODEL = "gpt-6-luna"
THRESHOLD = 272_000


def _price():  # type: ignore[no-untyped-def]
    return price_for(MODEL)


# --------------------------------------------------------- the table
def test_the_published_short_context_rates_are_recorded() -> None:
    """Transcribed from the provider's own page, in integer microdollars."""
    short = _price().short
    assert short.input_per_mtok == 100_000  # $0.10 / Mtok
    assert short.cached_input_per_mtok == 10_000  # $0.01 / Mtok
    assert short.cache_write_per_mtok == 125_000  # $0.125 / Mtok
    assert short.output_per_mtok == 500_000  # $0.50 / Mtok


def test_the_published_long_context_rates_are_recorded() -> None:
    """Documented as 2x input and cache rates, 1.5x output."""
    short, long = _price().short, _price().long
    assert long.input_per_mtok == 2 * short.input_per_mtok
    assert long.cached_input_per_mtok == 2 * short.cached_input_per_mtok
    assert long.cache_write_per_mtok == 2 * short.cache_write_per_mtok
    assert long.output_per_mtok == short.output_per_mtok * 3 // 2


def test_cache_writes_cost_more_than_ordinary_input() -> None:
    """The fact the whole reservation strategy rests on.

    If ordinary input were ever the dearest category, reserving at the
    cache-write rate would under-reserve.
    """
    short = _price().short
    assert short.most_expensive_input_per_mtok == short.cache_write_per_mtok
    assert short.cache_write_per_mtok > short.input_per_mtok > short.cached_input_per_mtok


def test_every_price_is_an_integer() -> None:
    """Floating-point money drifts in the direction that matters."""
    for price in PRICES.values():
        for tier in (price.short, price.long):
            for rate in (
                tier.input_per_mtok,
                tier.cached_input_per_mtok,
                tier.cache_write_per_mtok,
                tier.output_per_mtok,
            ):
                assert isinstance(rate, int)


def test_an_unknown_model_is_refused_rather_than_estimated() -> None:
    with pytest.raises(UnknownModelPrice, match="no pricing entry"):
        price_for("gpt-6-sol")
    assert is_priced(MODEL) is True
    assert is_priced("gpt-6-sol") is False


def test_a_family_prefix_is_not_assumed_to_share_a_price() -> None:
    """A dated snapshot may be priced differently from its alias."""
    with pytest.raises(UnknownModelPrice):
        price_for("gpt-6-luna-2026-01-01")


def test_the_table_records_its_source_and_review_date() -> None:
    price = _price()
    assert price.source.startswith("https://")
    assert price.reviewed == "2026-09-27"


# ----------------------------------------------------- context tiers
def test_a_request_at_the_threshold_is_still_short_context() -> None:
    """Documented as *more than* 272,000, so the boundary belongs below."""
    assert _price().tier_for(THRESHOLD) is _price().short
    assert _price().tier_for(THRESHOLD + 1) is _price().long


def test_long_context_prices_the_whole_request_not_the_excess() -> None:
    """A single token past the threshold repriced everything before it."""
    price = _price()
    just_under = price.settlement_microdollars(
        input_tokens=THRESHOLD, cached_tokens=0, cache_write_tokens=0, output_tokens=0
    )
    just_over = price.settlement_microdollars(
        input_tokens=THRESHOLD + 1, cached_tokens=0, cache_write_tokens=0, output_tokens=0
    )
    # Not one token dearer: twice the price of the entire request.
    assert just_over >= just_under * 2


def test_short_context_settlement_is_exact() -> None:
    price = _price()
    # 100k ordinary input at $0.10/M plus 100k output at $0.50/M. Kept well
    # under the long-context threshold, which would reprice the whole call.
    cost = price.settlement_microdollars(
        input_tokens=100_000,
        cached_tokens=0,
        cache_write_tokens=0,
        output_tokens=100_000,
    )
    assert cost == 10_000 + 50_000


def test_long_context_settlement_is_exact() -> None:
    price = _price()
    tokens = 300_000
    cost = price.settlement_microdollars(
        input_tokens=tokens, cached_tokens=0, cache_write_tokens=0, output_tokens=0
    )
    assert cost == tokens * 200_000 // MICRODOLLARS_PER_DOLLAR


# ------------------------------------------------------ reservations
def test_a_reservation_assumes_the_dearest_input_rate() -> None:
    """The point of the reservation: it cannot be surprised upward."""
    price = _price()
    reserved = price.reservation_microdollars(100_000, 0)
    assert reserved == 100_000 * price.short.cache_write_per_mtok // MICRODOLLARS_PER_DOLLAR
    assert reserved == 12_500  # $0.0125


def test_a_reservation_covers_the_whole_output_allowance() -> None:
    """Allowance, not expectation. The run is charged this until it settles."""
    price = _price()
    reserved = price.reservation_microdollars(0, 100_000)
    assert reserved == 100_000 * price.short.output_per_mtok // MICRODOLLARS_PER_DOLLAR
    assert reserved == 50_000  # $0.05


def test_a_reservation_is_never_less_than_the_settlement_it_covers() -> None:
    """The invariant that makes the ceiling real.

    Any combination of categories the provider can report must settle at or
    below what was reserved for that many tokens; otherwise a run could
    spend past its ceiling between the reservation and the report.
    """
    price = _price()
    for counted in (0, 1, 999, 120_000, THRESHOLD, THRESHOLD + 1):
        for allowed in (0, 1, 16_000):
            reserved = price.reservation_microdollars(counted, allowed)
            for cached in (0, counted // 2, counted):
                written = counted - cached
                settled = price.settlement_microdollars(
                    input_tokens=counted,
                    cached_tokens=cached,
                    cache_write_tokens=written,
                    output_tokens=allowed,
                )
                assert settled <= reserved, (
                    f"settlement {settled} exceeded reservation {reserved} "
                    f"for {counted} in / {allowed} out"
                )


def test_rounding_is_upward_so_a_ceiling_is_never_undershot() -> None:
    price = _price()
    # One token at $0.125/M is an eighth of a microdollar.
    assert price.reservation_microdollars(1, 0) == 1
    assert (
        price.settlement_microdollars(
            input_tokens=1, cached_tokens=1, cache_write_tokens=0, output_tokens=0
        )
        == 1
    )


def test_the_documented_worst_case_run_is_about_two_cents() -> None:
    """The number the deployment guide quotes, computed rather than copied.

    120,000 input at the cache-write rate plus 16,000 output.
    """
    reserved = _price().reservation_microdollars(120_000, 16_000)
    assert reserved == 15_000 + 8_000
    assert reserved / MICRODOLLARS_PER_DOLLAR == pytest.approx(0.023)


# ---------------------------------------------- settlement categories
def test_settlement_splits_input_into_its_three_categories() -> None:
    """`input_tokens` is the total and already contains the other two."""
    price = _price()
    cost = price.settlement_microdollars(
        input_tokens=100_000,
        cached_tokens=60_000,
        cache_write_tokens=20_000,
        output_tokens=0,
    )
    expected = (
        20_000 * price.short.input_per_mtok  # the ordinary remainder
        + 60_000 * price.short.cached_input_per_mtok
        + 20_000 * price.short.cache_write_per_mtok
    ) // MICRODOLLARS_PER_DOLLAR
    assert cost == expected


def test_a_fully_cached_request_is_an_order_of_magnitude_cheaper() -> None:
    price = _price()
    cold = price.settlement_microdollars(
        input_tokens=100_000, cached_tokens=0, cache_write_tokens=0, output_tokens=0
    )
    warm = price.settlement_microdollars(
        input_tokens=100_000, cached_tokens=100_000, cache_write_tokens=0, output_tokens=0
    )
    assert warm * 10 == cold


@pytest.mark.parametrize(
    ("kwargs", "coherent"),
    [
        ({"input_tokens": 100, "cached_tokens": 40, "cache_write_tokens": 10}, True),
        ({"input_tokens": 100, "cached_tokens": 100, "cache_write_tokens": 0}, True),
        # The categories cannot exceed the total they are part of.
        ({"input_tokens": 100, "cached_tokens": 80, "cache_write_tokens": 40}, False),
        ({"input_tokens": 10, "cached_tokens": 11, "cache_write_tokens": 0}, False),
        ({"input_tokens": -1, "cached_tokens": 0, "cache_write_tokens": 0}, False),
        ({"input_tokens": 10, "cached_tokens": -1, "cache_write_tokens": 0}, False),
    ],
)
def test_incoherent_usage_is_detected(kwargs: dict[str, int], coherent: bool) -> None:
    """Usage that does not add up cannot be settled honestly.

    The caller keeps its conservative reservation instead of charging a
    number derived from figures that contradict each other.
    """
    assert usage_is_coherent(output_tokens=0, **kwargs) is coherent


def test_a_negative_output_count_is_incoherent() -> None:
    assert (
        usage_is_coherent(input_tokens=10, cached_tokens=0, cache_write_tokens=0, output_tokens=-1)
        is False
    )
