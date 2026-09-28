"""What a provider call costs, in integer microdollars.

One table, one place. Dollar constants scattered through call sites drift
apart and none of them is authoritative; a run that cannot price itself must
refuse rather than guess, so an unknown model is a refusal and not a
default.

Currency is integer microdollars throughout. Floating-point money
accumulates error in exactly the direction that matters for a spend ceiling.

Source: Anthropic model documentation and pricing overview.
  https://platform.claude.com/docs/en/about-claude/models/overview
  https://www.anthropic.com/pricing
Reviewed: 2026-09-27. Re-check before enabling AI on a new model, and
whenever a resolved model identity is not already in this table.
"""

from __future__ import annotations

from dataclasses import dataclass

#: One US dollar, in the integer unit used everywhere below.
MICRODOLLARS_PER_DOLLAR = 1_000_000


@dataclass(frozen=True)
class ModelPrice:
    """Per-million-token prices, stored as integer microdollars."""

    model: str
    input_per_mtok: int
    output_per_mtok: int
    source: str = "https://platform.claude.com/docs/en/about-claude/models/overview"
    reviewed: str = "2026-09-27"

    def cost_microdollars(self, input_tokens: int, output_tokens: int) -> int:
        """Cost of one call, rounded up so a ceiling is never undershot."""
        total = input_tokens * self.input_per_mtok + output_tokens * self.output_per_mtok
        return -(-total // 1_000_000)


#: Keyed by the exact model identifier the provider resolves. A family
#: mapping is deliberately not implied: `claude-sonnet-5-20260101` is not
#: assumed to cost the same as `claude-sonnet-5` unless it is listed.
PRICES: dict[str, ModelPrice] = {
    # Standard rate. An earlier revision of this table said $3/$15, which
    # was wrong and would have under-reserved every call.
    "claude-sonnet-5": ModelPrice(
        model="claude-sonnet-5",
        input_per_mtok=2 * MICRODOLLARS_PER_DOLLAR,
        output_per_mtok=10 * MICRODOLLARS_PER_DOLLAR,
    ),
}


class UnknownModelPrice(LookupError):
    """No pricing entry, so no run. Refusing beats guessing a spend."""

    def __init__(self, model: str) -> None:
        super().__init__(
            f"no pricing entry for model {model!r}; add one to llm/pricing.py "
            "before enabling AI Analytics with it"
        )
        self.model = model


def price_for(model: str) -> ModelPrice:
    try:
        return PRICES[model]
    except KeyError:
        raise UnknownModelPrice(model) from None


def is_priced(model: str) -> bool:
    return model in PRICES


def as_dollars(microdollars: int) -> str:
    """For a report or a log line. Never for arithmetic."""
    return f"${microdollars / MICRODOLLARS_PER_DOLLAR:.4f}"
