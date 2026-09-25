"""Abuse controls for an unauthenticated public demo.

Anyone can upload and run an analysis, so these bound what one visitor can
consume. They are deliberately small and in-process: this is a single-container
portfolio deployment, and an in-memory counter is an honest fit for it. They
are **not** durable distributed quotas, and nothing here claims otherwise --
a restart resets them, and a second replica would count separately.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field

#: How many distinct client keys a limiter will remember. The key comes from
#: `X-Forwarded-For`, which the client supplies, so the number of distinct
#: keys is chosen by the caller rather than by the number of real visitors.
#: Without a hard ceiling, a single sender emitting a fresh spoofed address
#: per request grows this map for as long as it keeps going.
DEFAULT_MAX_CLIENTS = 4096


@dataclass
class RateLimit:
    """A fixed number of events per client within a sliding window.

    Best-effort abuse control, not authentication. Two properties it does
    hold: a client cannot exceed `limit` events per window under its own
    key, and the memory this costs is bounded no matter how many keys are
    invented.
    """

    limit: int
    window_seconds: float
    max_clients: int = DEFAULT_MAX_CLIENTS

    def __post_init__(self) -> None:
        # Ordered by least-recently-touched, so eviction has an obvious
        # victim and does not need to scan.
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, client: str) -> tuple[bool, float]:
        """Record an event. Returns (allowed, seconds until a slot frees)."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            events = self._events.get(client)
            if events is None:
                self._evict_locked(cutoff)
                events = deque()
                self._events[client] = events
            else:
                self._events.move_to_end(client)

            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return False, max(0.0, events[0] + self.window_seconds - now)
            events.append(now)
            return True, 0.0

    def _evict_locked(self, cutoff: float) -> None:
        """Make room for one new client. Caller holds the lock.

        Drops keys whose events have all expired first, because those carry
        no information at all. If that is not enough -- every remembered
        client is still inside its window -- the least recently seen is
        evicted. Evicting is the right trade: forgetting a real client lets
        it start a fresh window, which is a weaker limit, whereas growing
        without bound is a way to exhaust the process.
        """
        if len(self._events) < self.max_clients:
            return
        for key in [k for k, v in self._events.items() if not v or v[-1] < cutoff]:
            del self._events[key]
        while len(self._events) >= self.max_clients:
            self._events.popitem(last=False)

    def tracked_clients(self) -> int:
        """How many client keys are currently held. For tests and logging."""
        with self._lock:
            return len(self._events)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


@dataclass
class Capacity:
    """A ceiling on work happening at once, across all visitors."""

    limit: int
    _active: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        with self._lock:
            if self._active >= self.limit:
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)

    @property
    def active(self) -> int:
        return self._active


def client_key(forwarded_for: str | None, client_host: str | None) -> str:
    """Identify a client for rate limiting.

    Behind Render's proxy the peer address is the proxy, so the first entry of
    `X-Forwarded-For` is used when present. It is client-supplied and therefore
    spoofable; these limits raise the cost of casual abuse rather than
    preventing a determined one, and the documentation says so. What bounding
    the key store buys is that spoofing costs the *sender* effort without
    costing this process memory -- it does not make the header trustworthy.
    """
    if forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first[:64]
    return (client_host or "unknown")[:64]
