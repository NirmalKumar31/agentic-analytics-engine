"""What a provider call costs, in integer microdollars.

One table, one place. Dollar constants scattered through call sites drift
apart and none of them is authoritative; a run that cannot price itself must
refuse rather than guess, so an unknown model is a refusal and not a
default.

Currency is integer microdollars throughout. Floating-point money
accumulates error in exactly the direction that matters for a spend ceiling.

Input tokens are not one price. A token is billed as *exactly one* of
ordinary input, a cache read, or a cache write -- the rates are alternatives
rather than additions, and which one it becomes is decided by the provider
when it serves the request, after this process has already had to decide
whether to allow it. Pricing therefore has to express all three, and the
reservation has to assume the worst of them.

A second axis: a request whose input exceeds the long-context threshold is
priced at a higher rate *for the whole request*, not just for the tokens
past the threshold.

Source: OpenAI model and pricing documentation.
  https://developers.openai.com/api/docs/models/gpt-6-luna
  https://developers.openai.com/api/docs/pricing
  https://developers.openai.com/api/docs/guides/prompt-caching
Reviewed: 2026-09-27. Re-check before enabling AI on a new model, and
whenever a resolved model identity is not already in this table.
"""

from __future__ import annotations

from dataclasses import dataclass

#: One US dollar, in the integer unit used everywhere below.
MICRODOLLARS_PER_DOLLAR = 1_000_000


class UnknownModelPrice(LookupError):
    """No reviewed price for a model, so no run may be admitted on it."""


@dataclass(frozen=True)
class TierPrice:
    """Per-million-token prices for one context tier, in microdollars.

    Four rates rather than two, because an input token is billed at whichever
    single category it lands in and the categories differ by an order of
    magnitude.
    """

    input_per_mtok: int
    cached_input_per_mtok: int
    cache_write_per_mtok: int
    output_per_mtok: int

    @property
    def most_expensive_input_per_mtok(self) -> int:
        """The rate a reservation must assume.

        Nothing before dispatch can know whether a token will be served from
        cache, written to it, or neither, so a reservation that guessed would
        be a ceiling that sometimes fails to hold. Derived by `max` rather
        than hardcoded to cache-write: if a future table lists an ordinary
        rate above the cache-write rate, this stays correct.
        """
        return max(self.input_per_mtok, self.cached_input_per_mtok, self.cache_write_per_mtok)


@dataclass(frozen=True)
class ModelPrice:
    """Prices for one exact model identity, across both context tiers."""

    model: str
    short: TierPrice
    long: TierPrice
    #: Input tokens *above* which the whole request is priced at `long`.
    long_context_threshold: int
    source: str
    reviewed: str

    def tier_for(self, input_tokens: int) -> TierPrice:
        """Which tier prices a request with this many input tokens."""
        return self.long if input_tokens > self.long_context_threshold else self.short

    def reservation_microdollars(self, counted_input: int, allowed_output: int) -> int:
        """The worst case for a call that has not been dispatched yet.

        Every counted input token at the dearest input category, plus the
        entire output allowance. Both halves are deliberately pessimistic:
        the run is charged this much until the response reports what it
        actually used, and a reservation that under-counts is a ceiling that
        can be exceeded.
        """
        tier = self.tier_for(counted_input)
        total = (
            counted_input * tier.most_expensive_input_per_mtok
            + allowed_output * tier.output_per_mtok
        )
        return _ceil_div(total)

    def settlement_microdollars(
        self,
        *,
        input_tokens: int,
        cached_tokens: int,
        cache_write_tokens: int,
        output_tokens: int,
    ) -> int:
        """What a completed call actually cost, from the provider's own counts.

        `input_tokens` is the total and already includes the cached and
        cache-written parts, so the ordinary part is what is left after
        removing them. Callers must validate the categories before calling;
        see `usage_is_coherent`.
        """
        tier = self.tier_for(input_tokens)
        ordinary = input_tokens - cached_tokens - cache_write_tokens
        total = (
            ordinary * tier.input_per_mtok
            + cached_tokens * tier.cached_input_per_mtok
            + cache_write_tokens * tier.cache_write_per_mtok
            + output_tokens * tier.output_per_mtok
        )
        return _ceil_div(total)


def usage_is_coherent(
    *, input_tokens: int, cached_tokens: int, cache_write_tokens: int, output_tokens: int
) -> bool:
    """Whether reported usage can be priced at all.

    A negative count, or cache categories that exceed the total they are
    supposed to be part of, means the response cannot be settled honestly.
    The caller keeps the conservative reservation instead of charging a
    number derived from figures that do not add up.
    """
    if min(input_tokens, cached_tokens, cache_write_tokens, output_tokens) < 0:
        return False
    return cached_tokens + cache_write_tokens <= input_tokens


def _ceil_div(total_token_microdollars: int) -> int:
    """Per-million-token arithmetic, rounded up.

    Rounded up so a ceiling is never undershot by truncation.
    """
    return -(-total_token_microdollars // MICRODOLLARS_PER_DOLLAR)


def _usd(dollars: float) -> int:
    """Dollars per million tokens as integer microdollars.

    The table below is copied from published prices written in dollars, so
    the conversion happens once, here, rather than by hand at each entry.
    """
    return round(dollars * MICRODOLLARS_PER_DOLLAR)


#: The documented threshold: a prompt above this is priced at the long-context
#: rates for the entire request.
#: https://developers.openai.com/api/docs/models/gpt-6-luna
GPT_6_LUNA_LONG_CONTEXT_THRESHOLD = 272_000

_LUNA_SOURCE = "https://developers.openai.com/api/docs/models/gpt-6-luna"
_REVIEWED = "2026-09-27"

#: Keyed by the exact model identifier the provider resolves. A family
#: mapping is deliberately not implied: `gpt-6-luna-2026-01-01` is not
#: assumed to cost the same as `gpt-6-luna` unless it is listed.
PRICES: dict[str, ModelPrice] = {
    "gpt-6-luna": ModelPrice(
        model="gpt-6-luna",
        short=TierPrice(
            input_per_mtok=_usd(0.10),
            cached_input_per_mtok=_usd(0.01),
            cache_write_per_mtok=_usd(0.125),
            output_per_mtok=_usd(0.50),
        ),
        # Documented as 2x input and cache rates and 1.5x output, applied to
        # the whole request rather than to the excess.
        long=TierPrice(
            input_per_mtok=_usd(0.20),
            cached_input_per_mtok=_usd(0.02),
            cache_write_per_mtok=_usd(0.25),
            output_per_mtok=_usd(0.75),
        ),
        long_context_threshold=GPT_6_LUNA_LONG_CONTEXT_THRESHOLD,
        source=_LUNA_SOURCE,
        reviewed=_REVIEWED,
    ),
}


def price_for(model: str) -> ModelPrice:
    """The reviewed price for an exact model identity, or a refusal."""
    try:
        return PRICES[model]
    except KeyError:
        raise UnknownModelPrice(
            f"no pricing entry for model {model!r}; add one to llm/pricing.py "
            "with its source and review date before enabling AI on it"
        ) from None


def is_priced(model: str) -> bool:
    """Whether a model has a reviewed entry, without raising.

    For configuration checks that want to report a missing price rather
    than fail on it.
    """
    return model in PRICES
