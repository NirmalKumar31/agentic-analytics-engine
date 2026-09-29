"""Properties, checked by deterministic sweep rather than by hypothesis.

These are invariants over small, bounded domains: integer token counts, a
handful of price tiers, a fixed set of schema shapes. A seeded sweep over
several thousand combinations covers those domains as well as a property
engine would, reproduces exactly on failure, and adds no dependency to a
repository whose install is pinned. Where a domain is genuinely infinite
the sweep says which corner of it is being covered.

The properties are the ones whose violation would be invisible: money that
does not add up, a ceiling that can be crossed, a mapping that changes its
mind between identical calls.
"""

from __future__ import annotations

import random

import fakeredis
import pytest

from agentic_analytics.analytics.upload_plan import (
    QuestionMapping,
    build_sql,
    resolve_question,
)
from agentic_analytics.llm.ledger import CostLedger, LedgerCaps
from agentic_analytics.llm.pricing import (
    MICRODOLLARS_PER_DOLLAR,
    PRICES,
    price_for,
    usage_is_coherent,
)

MODEL = "gpt-6-luna"
SEED = 20260928

#: Token counts spanning both context tiers and the boundary between them.
TOKEN_SAMPLES = (0, 1, 2, 97, 1_000, 16_000, 119_999, 120_000, 271_999, 272_000, 272_001, 900_000)


def _rng() -> random.Random:
    return random.Random(SEED)


# ───────────────────────────────────────────────────────────────── pricing
def test_a_reservation_always_covers_every_settlement_it_could_face() -> None:
    """The property the ceiling rests on.

    For any counted input and output allowance, and any split of that input
    across the three billing categories, the settled cost must not exceed
    what was reserved. A single counterexample means a run can spend past
    its ceiling between dispatch and reconciliation.
    """
    price = price_for(MODEL)
    checked = 0
    for counted in TOKEN_SAMPLES:
        for allowed in TOKEN_SAMPLES:
            reserved = price.reservation_microdollars(counted, allowed)
            for cached_share in (0.0, 0.25, 0.5, 0.75, 1.0):
                cached = int(counted * cached_share)
                for write_share in (0.0, 0.5, 1.0):
                    written = int((counted - cached) * write_share)
                    if not usage_is_coherent(
                        input_tokens=counted,
                        cached_tokens=cached,
                        cache_write_tokens=written,
                        output_tokens=allowed,
                    ):
                        continue
                    settled = price.settlement_microdollars(
                        input_tokens=counted,
                        cached_tokens=cached,
                        cache_write_tokens=written,
                        output_tokens=allowed,
                    )
                    checked += 1
                    assert settled <= reserved, (
                        f"{counted} in ({cached} cached, {written} written), "
                        f"{allowed} out: settled {settled} > reserved {reserved}"
                    )
    assert checked > 500, f"the sweep only covered {checked} combinations"


def test_cost_never_decreases_as_tokens_increase() -> None:
    """Monotonic in both directions. A cheaper answer for more tokens would
    let a run game the ceiling by asking for more."""
    price = price_for(MODEL)
    previous = -1
    for counted in sorted(TOKEN_SAMPLES):
        cost = price.reservation_microdollars(counted, 0)
        assert cost >= previous, f"{counted} input cost less than a smaller request"
        previous = cost
    previous = -1
    for allowed in sorted(TOKEN_SAMPLES):
        cost = price.reservation_microdollars(0, allowed)
        assert cost >= previous
        previous = cost


def test_every_price_is_a_whole_number_of_microdollars() -> None:
    """Money is integer throughout. A float would accumulate error in
    exactly the direction that matters for a ceiling."""
    price = price_for(MODEL)
    rng = _rng()
    for _ in range(2_000):
        counted = rng.randrange(0, 400_000)
        allowed = rng.randrange(0, 80_000)
        for value in (
            price.reservation_microdollars(counted, allowed),
            price.settlement_microdollars(
                input_tokens=counted,
                cached_tokens=0,
                cache_write_tokens=0,
                output_tokens=allowed,
            ),
        ):
            assert isinstance(value, int)
            assert value >= 0


def test_any_nonzero_usage_costs_at_least_one_microdollar() -> None:
    """Rounded up, so a ceiling is never undershot by truncation."""
    price = price_for(MODEL)
    for tokens in (1, 2, 7):
        assert price.reservation_microdollars(tokens, 0) >= 1
        assert (
            price.settlement_microdollars(
                input_tokens=tokens, cached_tokens=0, cache_write_tokens=0, output_tokens=0
            )
            >= 1
        )


#: The documented boundary, written out rather than read off the price
#: table. Reading it off the object would make the test move with the
#: value it is meant to pin, which is how a threshold silently becomes
#: unreachable.
#: https://developers.openai.com/api/docs/pricing
LONG_CONTEXT_THRESHOLD = 272_000


def test_crossing_the_long_context_threshold_reprices_the_whole_request() -> None:
    """Above the boundary the long rates apply to every token, not to the
    excess. Pinned to the published threshold, so a table that moves the
    boundary out of reach fails here."""
    price = price_for(MODEL)
    assert price.long_context_threshold == LONG_CONTEXT_THRESHOLD

    def cost(tokens: int) -> int:
        return price.settlement_microdollars(
            input_tokens=tokens, cached_tokens=0, cache_write_tokens=0, output_tokens=0
        )

    below = cost(LONG_CONTEXT_THRESHOLD)
    above = cost(LONG_CONTEXT_THRESHOLD + 1)
    assert above > below
    # Repriced whole, so one more token roughly doubles the bill rather
    # than adding one token's worth.
    ratio = price.long.input_per_mtok / price.short.input_per_mtok
    assert above == pytest.approx(below * ratio, rel=0.001)


def test_the_short_tier_applies_at_and_below_the_threshold() -> None:
    """Off-by-one in the other direction: the boundary token itself is
    still cheap."""
    price = price_for(MODEL)
    for tokens in (1, 1_000, LONG_CONTEXT_THRESHOLD - 1, LONG_CONTEXT_THRESHOLD):
        assert price.tier_for(tokens) is price.short, tokens
    for tokens in (LONG_CONTEXT_THRESHOLD + 1, LONG_CONTEXT_THRESHOLD * 3):
        assert price.tier_for(tokens) is price.long, tokens


def test_every_listed_model_satisfies_the_same_properties() -> None:
    """Asserted for the table, not for one entry, so a model added without
    sane rates fails here."""
    for name, price in PRICES.items():
        for tier in (price.short, price.long):
            assert tier.cache_write_per_mtok >= tier.input_per_mtok, name
            assert tier.input_per_mtok >= tier.cached_input_per_mtok, name
            assert tier.output_per_mtok > 0, name
            assert tier.most_expensive_input_per_mtok == max(
                tier.input_per_mtok, tier.cached_input_per_mtok, tier.cache_write_per_mtok
            ), name
        assert price.long.input_per_mtok >= price.short.input_per_mtok, name
        assert price.long_context_threshold > 0, name
        assert price.source.startswith("https://"), name


# ─────────────────────────────────────────────────────────────── the ledger
def _caps(**kw: int) -> LedgerCaps:
    base = {
        "total_microdollars": 10_000_000,
        "daily_microdollars": 10_000_000,
        "run_microdollars": 1_000_000,
        "runs_per_session": 100,
        "runs_per_client_hour": 100,
    }
    return LedgerCaps(**(base | kw))  # type: ignore[arg-type]


def test_settlement_never_drives_a_counter_negative() -> None:
    """Whatever sequence of reserves and settles occurs, the durable total
    stays at or above zero. A negative counter would hand a run free
    budget."""
    rng = _rng()
    for trial in range(120):
        ledger = CostLedger(fakeredis.FakeRedis())
        run = f"run_{trial}"
        ledger.admit_run(run_id=run, session_id="ses_1", client_id="ip_1", caps=_caps())
        for call in range(rng.randrange(1, 6)):
            amount = rng.randrange(1, 40_000)
            admission = ledger.reserve(
                run_id=run,
                call_id=f"c{call}",
                session_id="ses_1",
                client_id="ip_1",
                amount_microdollars=amount,
                caps=_caps(),
                first_call_of_run=call == 0,
            )
            if not admission.admitted:
                continue
            # Settle to anything, including more than was reserved.
            actual = rng.randrange(0, amount * 2)
            ledger.settle(
                run_id=run,
                reservation_id=admission.reservation_id,
                actual_microdollars=actual,
            )
            assert ledger.spent_microdollars(run) >= 0, f"trial {trial}: negative run total"


def test_settling_the_same_reservation_twice_changes_nothing() -> None:
    """`unknown_reservation` covers expired and already-settled alike, and
    refunding either would silently raise the ceiling."""
    rng = _rng()
    for trial in range(60):
        ledger = CostLedger(fakeredis.FakeRedis())
        amount = rng.randrange(100, 50_000)
        ledger.admit_run(run_id="run_1", session_id="ses_1", client_id="ip_1", caps=_caps())
        admission = ledger.reserve(
            run_id="run_1",
            call_id="c1",
            session_id="ses_1",
            client_id="ip_1",
            amount_microdollars=amount,
            caps=_caps(),
            first_call_of_run=True,
        )
        assert admission.admitted
        actual = rng.randrange(0, amount)
        ledger.settle(
            run_id="run_1", reservation_id=admission.reservation_id, actual_microdollars=actual
        )
        after_first = ledger.spent_microdollars("run_1")
        for _ in range(3):
            outcome = ledger.settle(
                run_id="run_1",
                reservation_id=admission.reservation_id,
                actual_microdollars=actual,
            )
            assert outcome == "unknown_reservation"
        assert ledger.spent_microdollars("run_1") == after_first, f"trial {trial}"


def test_run_admission_is_never_granted_beyond_its_quota() -> None:
    """Whatever order sessions and clients arrive in, neither quota is
    exceeded."""
    rng = _rng()
    for trial in range(40):
        ledger = CostLedger(fakeredis.FakeRedis())
        per_session = rng.randrange(1, 5)
        caps = _caps(runs_per_session=per_session, runs_per_client_hour=1_000)
        granted = 0
        for attempt in range(per_session + 4):
            if ledger.admit_run(
                run_id=f"r{attempt}", session_id="ses_same", client_id="ip_1", caps=caps
            ).admitted:
                granted += 1
        assert granted == per_session, f"trial {trial}: {granted} of {per_session} allowed"


def test_a_whole_run_consumes_exactly_one_session_and_client_slot() -> None:
    """Admission and reservation together, which is how a run actually
    runs.

    Both were tested alone and both were correct alone. `_ADMIT` counted
    the slot and `_RESERVE` counted it again on the first billable call,
    so every run cost two of each. A cap of three runs per session
    admitted one run, then refused the second at its first reservation --
    after preflight had already made provider requests with the account's
    credential. The first paid run against a real provider found it; 1,580
    tests did not, because none of them exercised the two scripts in
    sequence.
    """
    rng = _rng()
    for trial in range(30):
        ledger = CostLedger(fakeredis.FakeRedis())
        caps = _caps(runs_per_session=3, runs_per_client_hour=5)
        run, session, client = f"r{trial}", f"s{trial}", f"c{trial}"

        assert ledger.admit_run(
            run_id=run, session_id=session, client_id=client, caps=caps
        ).admitted
        for call in range(rng.randrange(1, 5)):
            admission = ledger.reserve(
                run_id=run,
                call_id=f"call{call}",
                session_id=session,
                client_id=client,
                amount_microdollars=rng.randrange(1, 5_000),
                caps=caps,
                first_call_of_run=call == 0,
            )
            assert admission.admitted, admission.reason
            ledger.settle(
                run_id=run,
                reservation_id=admission.reservation_id,
                actual_microdollars=1,
            )

        raw = ledger._redis  # the counters the caps are enforced against
        assert int(raw.get(f"aae:ai:session:{session}") or 0) == 1, "session slot double-counted"
        hour = ledger._hour()
        assert int(raw.get(f"aae:ai:client:{hour}:{client}") or 0) == 1, (
            "client slot double-counted"
        )


def test_a_session_can_run_its_full_quota() -> None:
    """The consequence, stated as the visitor experiences it: a cap of
    three runs per session allows three runs, each of which completes."""
    ledger = CostLedger(fakeredis.FakeRedis())
    caps = _caps(runs_per_session=3, runs_per_client_hour=10)
    for index in range(3):
        run = f"run{index}"
        assert ledger.admit_run(
            run_id=run, session_id="one_session", client_id="one_client", caps=caps
        ).admitted, f"run {index} refused admission"
        admission = ledger.reserve(
            run_id=run,
            call_id="c0",
            session_id="one_session",
            client_id="one_client",
            amount_microdollars=1_000,
            caps=caps,
            first_call_of_run=True,
        )
        assert admission.admitted, f"run {index} admitted then refused: {admission.reason}"
    # And the fourth is refused, at admission rather than mid-run.
    assert not ledger.admit_run(
        run_id="run3", session_id="one_session", client_id="one_client", caps=caps
    ).admitted


def test_money_cannot_be_reserved_without_a_durable_admission() -> None:
    """Closing the duplicate count must not open a bypass.

    Moving session and client counting into `admit_run` removed the only
    thing `_RESERVE` checked about whether a run was authorised. Without
    this, a caller could skip admission entirely and spend against the
    money ceilings while consuming no run slot -- unlimited runs, each
    correctly billed, which is worse than the double count it replaced.
    """
    ledger = CostLedger(fakeredis.FakeRedis())
    unadmitted = ledger.reserve(
        run_id="never_admitted",
        call_id="c0",
        session_id="ses_1",
        client_id="ip_1",
        amount_microdollars=10_000,
        caps=_caps(),
        first_call_of_run=True,
    )
    assert not unadmitted.admitted
    assert unadmitted.reason == "ai_run_not_admitted"
    assert ledger.spent_microdollars("never_admitted") == 0

    # And the same call succeeds once the run is admitted.
    assert ledger.admit_run(
        run_id="never_admitted", session_id="ses_1", client_id="ip_1", caps=_caps()
    ).admitted
    assert ledger.reserve(
        run_id="never_admitted",
        call_id="c0",
        session_id="ses_1",
        client_id="ip_1",
        amount_microdollars=10_000,
        caps=_caps(),
        first_call_of_run=True,
    ).admitted


def test_admitting_the_same_run_repeatedly_consumes_one_slot() -> None:
    """Idempotent per run id: a retry must not cost a second slot."""
    ledger = CostLedger(fakeredis.FakeRedis())
    caps = _caps(runs_per_session=1)
    assert all(
        ledger.admit_run(
            run_id="same_run", session_id="ses_1", client_id="ip_1", caps=caps
        ).admitted
        for _ in range(5)
    )
    assert not ledger.admit_run(
        run_id="other_run", session_id="ses_1", client_id="ip_1", caps=caps
    ).admitted


# ───────────────────────────────────────────────────────── upload mapping
#: Schema shapes spanning the cases that decide a mapping: how many
#: measures, how many dimensions, whether a date exists at all.
SHAPES = (
    {"measures": ["amount"], "dimensions": ["region"], "time_fields": ["sold_on"]},
    {"measures": ["amount", "units"], "dimensions": ["region"], "time_fields": ["sold_on"]},
    {"measures": ["amount"], "dimensions": ["region", "channel"], "time_fields": []},
    {"measures": [], "dimensions": ["region", "channel"], "time_fields": ["sold_on"]},
    {"measures": ["amount", "units"], "dimensions": [], "time_fields": []},
    {"measures": [], "dimensions": [], "time_fields": []},
)

QUESTIONS = (
    "total amount by region",
    "average amount",
    "count by channel",
    "amount over time",
    "total amount in 2025",
    "total amount by nonexistent_column",
    "what is it",
    "top regions by amount",
    "total units by region",
)


def _schema(shape: dict[str, list[str]]) -> dict[str, object]:
    names = [*shape["measures"], *shape["dimensions"], *shape["time_fields"]]
    return {
        "table": "uploaded_data",
        "fields": [{"name": n} for n in names],
        "measures": shape["measures"],
        "dimensions": shape["dimensions"],
        "time_fields": shape["time_fields"],
        "aggregatable_if_named": [],
    }


@pytest.mark.parametrize("question", QUESTIONS)
def test_resolution_is_deterministic(question: str) -> None:
    """Identical calls must agree. A mapping that varied would make a token
    count taken over one version meaningless for the other."""
    for shape in SHAPES:
        schema = _schema(shape)
        first = resolve_question(question, schema)
        second = resolve_question(question, schema)
        assert first.as_dict() == second.as_dict(), (question, shape)


def test_a_mapping_only_ever_names_columns_the_table_has() -> None:
    """The property that stops a guess becoming a query.

    Whatever the question, a resolved measure, dimension or time field must
    be a column of the schema -- never invented, never carried over from
    another shape.
    """
    for shape in SHAPES:
        schema = _schema(shape)
        known = {str(f["name"]) for f in schema["fields"]}  # type: ignore[index,union-attr]
        for question in QUESTIONS:
            mapping = resolve_question(question, schema)
            for chosen in (mapping.measure, mapping.dimension, mapping.time_field):
                if chosen is not None:
                    assert chosen in known, (question, shape, chosen)


def test_only_a_confident_mapping_produces_sql() -> None:
    """A guess must never reach the database."""
    for shape in SHAPES:
        schema = _schema(shape)
        for question in QUESTIONS:
            mapping = resolve_question(question, schema)
            sql = build_sql(mapping)
            if not mapping.confident:
                assert sql is None, (question, shape)
            if sql is not None:
                assert mapping.confident
                assert "uploaded_data" in sql


def test_every_unresolved_question_falls_back_to_a_profile() -> None:
    """Why the test above cannot be the only guard.

    The resolver never emits a non-confident mapping with a real
    operation -- it degrades to `profile` instead. That makes the
    confidence check in `build_sql` unreachable from the resolver, so the
    sweep above cannot exercise it and the next test constructs the case
    by hand. Asserted rather than assumed, so if the resolver ever starts
    emitting one, this fails and says to widen the sweep.
    """
    seen = 0
    for shape in SHAPES:
        schema = _schema(shape)
        for question in QUESTIONS:
            mapping = resolve_question(question, schema)
            if not mapping.confident:
                seen += 1
                assert mapping.operation == "profile", (question, shape, mapping.operation)
    assert seen > 20, f"only {seen} non-confident mappings in the sweep"


@pytest.mark.parametrize("operation", ["aggregate", "breakdown", "trend", "ranking"])
def test_the_confidence_guard_refuses_even_a_fully_populated_mapping(operation: str) -> None:
    """The guard itself, exercised directly.

    Defence in depth for a mapping that names every column it needs and is
    still marked unconfident. Unreachable from today's resolver, which is
    precisely why it is tested here: a future resolver that emits one must
    not find the door open.
    """
    mapping = QuestionMapping(
        operation=operation,  # type: ignore[arg-type]
        table="uploaded_data",
        measure="amount",
        dimension="region",
        time_field="sold_on",
        confident=False,
    )
    assert build_sql(mapping) is None


def test_a_named_period_either_reaches_the_sql_or_is_refused() -> None:
    """Never silently dropped, which is what used to happen: a question
    about 1998 was answered over every row in the table."""
    for shape in SHAPES:
        schema = _schema(shape)
        mapping = resolve_question("total amount in 2025", schema)
        sql = build_sql(mapping)
        if sql is None:
            continue
        if mapping.period is not None:
            assert "2025-01-01" in sql
        else:
            # No period resolved, so the question must not have been
            # answered as though the year were irrelevant.
            assert not shape["time_fields"], (shape, mapping.as_dict())


def test_a_grouping_that_names_nothing_is_always_refused() -> None:
    for shape in SHAPES:
        mapping = resolve_question("total amount by nonexistent_column", _schema(shape))
        assert not mapping.confident, shape
        assert build_sql(mapping) is None


def test_generated_sql_is_always_a_single_read_only_statement() -> None:
    from agentic_analytics.warehouse.sqlguard import check_sql

    for shape in SHAPES:
        schema = _schema(shape)
        for question in QUESTIONS:
            sql = build_sql(resolve_question(question, schema))
            if sql is None:
                continue
            assert ";" not in sql.rstrip(";"), sql
            check_sql(sql, allowed_tables={"uploaded_data"})


def test_the_microdollar_unit_is_what_the_table_assumes() -> None:
    assert MICRODOLLARS_PER_DOLLAR == 1_000_000
