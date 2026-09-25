"""The rate limiter must not be a way to exhaust the process.

The client key comes from `X-Forwarded-For`, which the caller supplies. That
is documented and unavoidable behind a proxy, but it means the *number of
distinct keys* is also chosen by the caller. A limiter that allocates a new
entry per key therefore turns a spoofable header into unbounded memory growth
driven from outside.

Bounding the key store does not make the header trustworthy. It makes
spoofing cost the sender effort without costing this process memory.
"""

from __future__ import annotations

import time

from agentic_analytics.api.limits import DEFAULT_MAX_CLIENTS, RateLimit, client_key


def test_ten_thousand_spoofed_keys_do_not_grow_the_store() -> None:
    """The adversarial case, stated plainly."""
    limiter = RateLimit(limit=4, window_seconds=3600.0, max_clients=256)
    for index in range(10_000):
        allowed, _ = limiter.check(f"203.0.113.{index % 256}-{index}")
        assert allowed, "a first request from a fresh key must be allowed"
        assert limiter.tracked_clients() <= 256, f"store grew to {limiter.tracked_clients()}"
    assert limiter.tracked_clients() <= 256


def test_the_default_ceiling_is_enforced_too() -> None:
    """Not only when a test passes a small limit."""
    limiter = RateLimit(limit=2, window_seconds=3600.0)
    for index in range(DEFAULT_MAX_CLIENTS * 2):
        limiter.check(f"key-{index}")
    assert limiter.tracked_clients() <= DEFAULT_MAX_CLIENTS


def test_a_real_client_is_still_limited_while_spoofing_happens() -> None:
    """Eviction must not hand an attacker a way to reset someone's limit.

    It can -- a flood evicts the victim's record and their next request
    starts a fresh window -- so what is asserted is the honest property: an
    unevicted client is limited exactly as specified, and the limiter is a
    cost, not a guarantee.
    """
    limiter = RateLimit(limit=3, window_seconds=3600.0, max_clients=64)
    for _ in range(3):
        allowed, _ = limiter.check("198.51.100.7")
        assert allowed
    refused, retry_after = limiter.check("198.51.100.7")
    assert refused is False
    assert retry_after > 0


def test_recently_used_clients_survive_eviction() -> None:
    """Eviction takes the least recently seen, not an arbitrary victim.

    The limit here is set above the number of checks the loop makes, so the
    test measures survival rather than accidentally measuring the limit.
    """
    rounds = 50
    limiter = RateLimit(limit=rounds + 4, window_seconds=3600.0, max_clients=8)
    limiter.check("keep-me")
    for index in range(rounds):
        limiter.check(f"noise-{index}")
        limiter.check("keep-me")  # touched every round, so never the oldest

    assert limiter.tracked_clients() <= 8
    # Its history survived: rounds + 1 events are already recorded, so only
    # three of the remaining allowance are left.
    for _ in range(3):
        assert limiter.check("keep-me")[0] is True
    assert limiter.check("keep-me")[0] is False, "the record was evicted and the count reset"


def test_expired_keys_are_dropped_before_live_ones() -> None:
    """A key whose window has passed carries no information.

    It should go first, so eviction only reaches live clients when the store
    really is full of live clients.
    """
    limiter = RateLimit(limit=2, window_seconds=0.05, max_clients=4)
    for index in range(4):
        limiter.check(f"stale-{index}")
    time.sleep(0.1)  # every recorded event is now outside the window
    limiter.check("fresh")
    assert limiter.tracked_clients() <= 4
    # The fresh client keeps its full allowance: it was not evicted to make
    # room for itself.
    assert limiter.check("fresh")[0] is True
    assert limiter.check("fresh")[0] is False


def test_the_window_still_slides() -> None:
    limiter = RateLimit(limit=1, window_seconds=0.05)
    assert limiter.check("a")[0] is True
    assert limiter.check("a")[0] is False
    time.sleep(0.08)
    assert limiter.check("a")[0] is True, "the window did not slide"


def test_client_key_is_bounded_in_length() -> None:
    """A header is attacker-controlled in length as well as in value."""
    assert len(client_key("x" * 10_000, None)) <= 64
    assert len(client_key(None, "y" * 10_000)) <= 64
    assert client_key(None, None) == "unknown"
    # The first entry wins, because the rest are appended by proxies.
    assert client_key("203.0.113.9, 10.0.0.1", "10.0.0.2") == "203.0.113.9"
