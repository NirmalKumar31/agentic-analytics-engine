# Production capacity rehearsal: 7 October 2026

This record describes one bounded breakage check. It is not a load test or a
throughput benchmark.

## Target

- Service: <https://agentic-analytics-engine.onrender.com>
- `/api/health` build: `b5a24b51963f69577f28b5002eacab0d7a24d9c1`
- Provider mode: `fake`; no provider request or spend was possible
- Command: `python3 scripts/capacity_smoke.py https://agentic-analytics-engine.onrender.com`

## What ran

The script opened two demo sessions and ran their analyses concurrently. It
then opened three small, distinguishable uploads and analysed them concurrently.
Finally it checked that capacity was available after cleanup. It uses the
public API and deletes every session it opens.

All ten assertions passed:

- the service was reachable before and after the check;
- concurrent analyses completed or were cleanly rate-limited, with no timeout
  or unreachable response;
- completed findings were supported;
- upload sessions completed or were cleanly rate-limited, with no data crossing
  between their marker values;
- capacity was available after the work stopped; and
- the instance identifier did not change during the check.

The two concurrent demo analyses completed in 8.8 seconds of wall-clock time.
That observation is included only to make the run reproducible. It is not a
requests-per-second measurement and must not be used as a performance claim.

## What this does not show

This one small concurrency check does not establish sustained-load behaviour,
throughput, latency under load, high availability, cold-start latency, or a
memory bound for the hosted instance. The constrained-container resource
rehearsal in `scripts/resource_rehearsal.py` remains the appropriate tool for
testing the configured session envelope before changing the hosting plan.
