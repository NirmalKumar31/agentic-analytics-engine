"""Durable accounting for paid runs.

Process-local counters cannot bound an anonymous paid endpoint. Render cold
starts reset them and a second instance never sees the first one's total, so
the ceiling a deployment believes it has is the ceiling times the number of
processes that happen to be running.

The ledger keeps its counters in Redis-compatible storage and admits work by
*reserving* the worst-case cost of a call before the call is made, then
reconciling against what the provider actually reported. Reserving after the
fact would mean the limit is discovered only once it has been exceeded.

Three rules the implementation follows throughout:

* money is integer microdollars, never a float;
* an ambiguous failure is charged, not refunded, because a request that
  timed out may still have been billed;
* if the store is unreachable, AI is refused. Deterministic analytics does
  not consult the ledger and is unaffected.

Nothing about a question, a dataset, a prompt or a response is stored. The
keys hold counters and the reservation amounts that back them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from agentic_analytics.logging import get_logger

log = get_logger(__name__)

#: How long a reservation survives without reconciliation. A process killed
#: mid-call must not hold budget forever, and a caller that comes back later
#: finds its reservation already expired rather than double-counted.
RESERVATION_TTL_SECONDS = 900
#: Daily counters expire on their own so the store does not grow without
#: bound and a missed reconciliation cannot poison tomorrow.
DAY_TTL_SECONDS = 172_800
HOUR_TTL_SECONDS = 7_200


class LedgerUnavailable(RuntimeError):
    """The durable store could not be reached, so AI must not run."""


@dataclass(frozen=True)
class Admission:
    """The outcome of asking for budget."""

    admitted: bool
    reason: str = ""
    reservation_id: str = ""
    reserved_microdollars: int = 0


#: Stable refusal reasons. The UI maps these; a visitor never sees a key.
GLOBAL_LIMIT = "ai_global_limit_reached"
DAILY_LIMIT = "ai_daily_limit_reached"
SESSION_LIMIT = "ai_session_limit_reached"
CLIENT_LIMIT = "ai_client_limit_reached"
RUN_LIMIT = "ai_run_budget_exceeded"
CONCURRENCY_LIMIT = "ai_concurrent_limit_reached"


class RedisLike(Protocol):
    """The three operations the ledger uses.

    Deliberately narrow. A wider protocol would have to match the client's
    full signatures, and nothing here needs them: admission and settlement
    happen inside Lua, so the application only evaluates scripts, pings and
    reads a counter.
    """

    def eval(self, script: str, numkeys: int, *args: Any) -> Any: ...
    def ping(self) -> Any: ...
    def get(self, name: Any) -> Any: ...


#: Admission and reservation in one atomic step.
#:
#: Checking then writing from the application would let two instances both
#: read "under the limit" and both proceed. The whole decision happens
#: inside the store, so concurrent callers serialise on it.
_RESERVE = """
local total_key   = KEYS[1]
local day_key     = KEYS[2]
local run_key     = KEYS[3]
local session_key = KEYS[4]
local client_key  = KEYS[5]
local resv_key    = KEYS[6]
local amount        = tonumber(ARGV[1])
local total_cap     = tonumber(ARGV[2])
local day_cap       = tonumber(ARGV[3])
local run_cap       = tonumber(ARGV[4])
local session_cap   = tonumber(ARGV[5])
local client_cap    = tonumber(ARGV[6])
local resv_ttl      = tonumber(ARGV[7])
local day_ttl       = tonumber(ARGV[8])
local hour_ttl      = tonumber(ARGV[9])
local new_run       = tonumber(ARGV[10])

if redis.call('EXISTS', resv_key) == 1 then
  return {1, 'duplicate', 0}
end

local total = tonumber(redis.call('GET', total_key) or '0')
local day   = tonumber(redis.call('GET', day_key) or '0')
local run   = tonumber(redis.call('GET', run_key) or '0')

if total + amount > total_cap then return {0, 'ai_global_limit_reached', 0} end
if day + amount > day_cap then return {0, 'ai_daily_limit_reached', 0} end
if run + amount > run_cap then return {0, 'ai_run_budget_exceeded', 0} end

if new_run == 1 then
  local session = tonumber(redis.call('GET', session_key) or '0')
  if session + 1 > session_cap then return {0, 'ai_session_limit_reached', 0} end
  local client = tonumber(redis.call('GET', client_key) or '0')
  if client + 1 > client_cap then return {0, 'ai_client_limit_reached', 0} end
  redis.call('INCRBY', session_key, 1)
  redis.call('EXPIRE', session_key, day_ttl)
  redis.call('INCRBY', client_key, 1)
  redis.call('EXPIRE', client_key, hour_ttl)
end

redis.call('INCRBY', total_key, amount)
redis.call('INCRBY', day_key, amount)
redis.call('EXPIRE', day_key, day_ttl)
redis.call('INCRBY', run_key, amount)
redis.call('EXPIRE', run_key, day_ttl)
redis.call('SET', resv_key, amount, 'EX', resv_ttl)
return {1, 'ok', amount}
"""

#: Reconciliation. Releases only the difference between what was reserved
#: and what was actually spent, and never below zero.
_SETTLE = """
local total_key = KEYS[1]
local day_key   = KEYS[2]
local run_key   = KEYS[3]
local resv_key  = KEYS[4]
local actual = tonumber(ARGV[1])

local reserved = tonumber(redis.call('GET', resv_key) or '-1')
if reserved < 0 then
  return {0, 'unknown_reservation'}
end
redis.call('DEL', resv_key)

local refund = reserved - actual
if refund <= 0 then
  -- Spent at least what was reserved: charge the difference too.
  local extra = -refund
  if extra > 0 then
    redis.call('INCRBY', total_key, extra)
    redis.call('INCRBY', day_key, extra)
    redis.call('INCRBY', run_key, extra)
  end
  return {1, 'charged'}
end
redis.call('DECRBY', total_key, refund)
redis.call('DECRBY', day_key, refund)
redis.call('DECRBY', run_key, refund)
return {1, 'released'}
"""


class CostLedger:
    """Durable admission control for AI runs."""

    def __init__(self, client: RedisLike, namespace: str = "aae:ai") -> None:
        self._redis = client
        self._ns = namespace

    # ------------------------------------------------------------- keys
    def _k(self, *parts: str) -> str:
        return ":".join((self._ns, *parts))

    @staticmethod
    def _today() -> str:
        return time.strftime("%Y%m%d", time.gmtime())

    @staticmethod
    def _hour() -> str:
        return time.strftime("%Y%m%d%H", time.gmtime())

    # -------------------------------------------------------- operations
    def healthy(self) -> bool:
        """Whether the store answers. Called before offering AI at all."""
        try:
            self._redis.ping()
            return True
        except Exception as exc:
            log.warning("ai_ledger_unreachable", error_type=type(exc).__name__)
            return False

    def reserve(
        self,
        *,
        run_id: str,
        call_id: str,
        session_id: str,
        client_id: str,
        amount_microdollars: int,
        caps: LedgerCaps,
        first_call_of_run: bool,
    ) -> Admission:
        """Take budget for one call before it is dispatched.

        `call_id` makes this idempotent: a retry of the same logical call
        reserves once. Counting a retried call twice would refuse work the
        visitor never got.
        """
        if amount_microdollars <= 0:
            return Admission(True, reservation_id="", reserved_microdollars=0)
        reservation_id = f"{run_id}:{call_id}"
        try:
            ok, reason, reserved = self._redis.eval(
                _RESERVE,
                6,
                self._k("total"),
                self._k("day", self._today()),
                self._k("run", run_id),
                self._k("session", session_id),
                self._k("client", self._hour(), client_id),
                self._k("resv", reservation_id),
                int(amount_microdollars),
                int(caps.total_microdollars),
                int(caps.daily_microdollars),
                int(caps.run_microdollars),
                int(caps.runs_per_session),
                int(caps.runs_per_client_hour),
                RESERVATION_TTL_SECONDS,
                DAY_TTL_SECONDS,
                HOUR_TTL_SECONDS,
                1 if first_call_of_run else 0,
            )
        except Exception as exc:
            log.warning("ai_ledger_reserve_failed", error_type=type(exc).__name__)
            raise LedgerUnavailable("the usage ledger is unavailable") from None

        reason_text = reason.decode() if isinstance(reason, bytes) else str(reason)
        if int(ok) != 1:
            return Admission(False, reason=reason_text)
        return Admission(
            True,
            reservation_id=reservation_id,
            reserved_microdollars=int(reserved),
        )

    def settle(self, *, run_id: str, reservation_id: str, actual_microdollars: int) -> str:
        """Reconcile a reservation against reported usage.

        Releases only the unused part. If the call actually cost more than
        was reserved, the difference is charged rather than forgiven.
        """
        if not reservation_id:
            return "no_reservation"
        try:
            ok, outcome = self._redis.eval(
                _SETTLE,
                4,
                self._k("total"),
                self._k("day", self._today()),
                self._k("run", run_id),
                self._k("resv", reservation_id),
                max(0, int(actual_microdollars)),
            )
        except Exception as exc:
            # The money is already reserved, so a failure here leaves the
            # ceiling conservative rather than permissive. The reservation
            # expires on its own.
            log.warning("ai_ledger_settle_failed", error_type=type(exc).__name__)
            return "settle_failed"
        text = outcome.decode() if isinstance(outcome, bytes) else str(outcome)
        return text if int(ok) == 1 else "unknown_reservation"

    def abandon(self, *, reservation_id: str) -> None:
        """Leave a reservation in place after an ambiguous failure.

        Deliberately not a refund. A request that timed out in transit may
        still have been billed by the provider, and the safe assumption for
        a spend ceiling is that it was. The reservation's TTL eventually
        reclaims it.
        """
        log.info("ai_ledger_reservation_abandoned", reservation=bool(reservation_id))

    def spent_microdollars(self, run_id: str) -> int:
        try:
            raw = self._redis.get(self._k("run", run_id))
        except Exception:
            return 0
        return int(raw or 0)


@dataclass(frozen=True)
class LedgerCaps:
    """The ceilings one admission decision is checked against."""

    total_microdollars: int
    daily_microdollars: int
    run_microdollars: int
    runs_per_session: int
    runs_per_client_hour: int

    @classmethod
    def from_settings(cls, cfg: Any) -> LedgerCaps:
        return cls(
            total_microdollars=cfg.ai_total_cost_microdollars,
            daily_microdollars=cfg.ai_daily_cost_microdollars,
            run_microdollars=cfg.ai_max_cost_microdollars,
            runs_per_session=cfg.ai_runs_per_session,
            runs_per_client_hour=cfg.ai_runs_per_ip_per_hour,
        )


def open_ledger(url: str | None, namespace: str = "aae:ai") -> CostLedger | None:
    """Connect to the durable store, or return `None` if it is not configured.

    `None` means AI is not offered. It never means "fall back to counting in
    memory": an in-memory fallback is precisely the control this replaces.
    """
    if not url:
        return None
    try:
        import redis  # lazy: a deterministic deployment needs no client
    except ModuleNotFoundError:
        log.warning("ai_ledger_client_missing")
        return None
    try:
        client = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
        ledger = CostLedger(client, namespace)
        return ledger if ledger.healthy() else None
    except Exception as exc:
        log.warning("ai_ledger_open_failed", error_type=type(exc).__name__)
        return None
