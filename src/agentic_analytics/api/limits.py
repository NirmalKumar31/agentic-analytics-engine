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
from collections import deque
from dataclasses import dataclass, field


@dataclass
class RateLimit:
    """A fixed number of events per client within a sliding window."""

    limit: int
    window_seconds: float

    def __post_init__(self) -> None:
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, client: str) -> tuple[bool, float]:
        """Record an event. Returns (allowed, seconds until a slot frees)."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            events = self._events.setdefault(client, deque())
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return False, max(0.0, events[0] + self.window_seconds - now)
            events.append(now)
            # Opportunistic cleanup so an idle key set cannot grow forever.
            if len(self._events) > 4096:
                for key in [k for k, v in self._events.items() if not v]:
                    self._events.pop(key, None)
            return True, 0.0

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
    preventing a determined one, and the documentation says so.
    """
    if forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first[:64]
    return (client_host or "unknown")[:64]
