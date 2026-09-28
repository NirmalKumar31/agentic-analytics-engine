# Deployment

One service, one origin, per-run provider selection. See
[ADR 0001](adr/0001-dual-mode-topology.md) for why.

Deterministic Analytics needs no secret and no external service. AI
Analytics needs four things together, and is unavailable unless it has all
of them.

## Environment variables

Authoritative. Anything not listed here has a working default in
`src/agentic_analytics/config.py`.

### Core service

| Variable | Default | Purpose |
|---|---|---|
| `AAE_PROVIDER_MODE` | `fake` | Default provider for the CLI and the evaluation harness. The web application ignores it and builds the provider from the per-run mode. |
| `AAE_LIVE_ANALYTICS_ENABLED` | `false` | Whether visitors may run new analyses. False serves recorded runs only. |
| `AAE_UPLOADS_ENABLED` | `true` | Whether visitors may upload CSV or Parquet. |
| `AAE_BIND_HOST` | `127.0.0.1` | The MCP host policy reads this. |
| `AAE_MCP_ALLOWED_HOSTS` | *(empty)* | Empty withdraws the public MCP endpoint. A network binding with no allow-list serves 503 rather than serving unvalidated. |
| `AAE_SESSION_COOKIE_SECURE` | `false` | Set true behind TLS. |
| `AAE_SESSION_TTL_SECONDS` | `1800` | How long an idle dataset session survives. |

### AI Analytics

All four of the first block are required together. Missing any one leaves AI
unavailable with a sanitized reason and leaves Deterministic Analytics
working.

| Variable | Default | Purpose |
|---|---|---|
| `AAE_AI_ANALYTICS_ENABLED` | `false` | Master switch. |
| `AAE_CLOUD_API_KEY` | *(unset)* | Provider credential. Secret: dashboard only, never in a blueprint. |
| `AAE_CLOUD_MODEL` | `gpt-6-luna` | Must match a pricing entry exactly. An unpriced model is refused. |
| `AAE_AI_QUOTA_REDIS_URL` | *(unset)* | Redis-compatible durable ledger. Secret. Without it AI stays off: process-local counters reset on cold start and are not shared between instances. |
| `AAE_CLOUD_BASE_URL` | `https://api.openai.com` | Provider endpoint. |
| `AAE_CLOUD_TIMEOUT_SECONDS` | `120` | Application ceiling on one call, enforced independently of the HTTP client. |
| `AAE_CLOUD_REASONING_EFFORT` | `low` | How much the model may reason before answering. Reasoning tokens are billed as output. No sampling parameter is configurable: the engine's determinism comes from the analytics layer, not from model decoding. |

Per-run ceilings for an AI run. Deterministic runs are unaffected.

| Variable | Default | Purpose |
|---|---|---|
| `AAE_AI_MAX_LLM_CALLS` | `24` | Provider attempts, including failures and retries. |
| `AAE_AI_MAX_INPUT_TOKENS` | `120000` | |
| `AAE_AI_MAX_OUTPUT_TOKENS` | `16000` | |
| `AAE_AI_MAX_RUNTIME_SECONDS` | `180` | |
| `AAE_AI_MAX_COST_MICRODOLLARS` | `250000` | $0.25. Integer microdollars; money is never a float. |

Public-demo ceilings, enforced in the durable ledger so a restart or a
second instance cannot reset them.

| Variable | Default | Purpose |
|---|---|---|
| `AAE_AI_RUNS_PER_SESSION` | `3` | |
| `AAE_AI_RUNS_PER_IP_PER_HOUR` | `5` | |
| `AAE_AI_CONCURRENT_RUNS` | `2` | |
| `AAE_AI_DAILY_COST_MICRODOLLARS` | `2000000` | $2.00 a day. |
| `AAE_AI_TOTAL_COST_MICRODOLLARS` | `20000000` | $20.00 lifetime. |

### What resets, and what does not

The daily counter is keyed by UTC date and expires on its own. The lifetime
total is not keyed by anything and never expires: when it is reached, AI
stays off until you delete `aae:ai:total` in the Key Value instance
deliberately. That is the intended behaviour for a public demo — a cap that
rolls over on a schedule can be reached on every schedule, unattended.

The lifetime total counts **worst case, not settled cost**, for any run that
did not reconcile. A reservation is counted before the call is dispatched
and released afterwards against reported usage; if the process dies in
between, the receipt expires and the estimate stays charged. So the counter
drifts above true spend over time, always upward. Compare it against the
provider's own usage page before assuming the deployment has spent what this
says.

These are application controls. They are not a billing guarantee: a request
that times out in transit may still have been billed, and an application
cannot refund what a provider charged. **Set a hard spend cap on the
provider account as well.** That is the only external backstop.

For OpenAI that is a per-project limit: Project settings → Limits → Spend →
Edit spend limit, with **Enforce a hard limit** turned on. A spend alert is
not a limit. Use a project created for this demo rather than the default
one, so the ceiling cannot be raised by unrelated work sharing it.

OpenAI documents that enforcement is not instantaneous — a small amount of
extra usage can be processed while the limit state propagates, so recorded
spend can slightly exceed the configured amount. Size the limit with that in
mind rather than treating it as exact.
https://developers.openai.com/api/docs/guides/spend-limits

### What a run costs

Priced from the published rates for `gpt-6-luna`, reviewed 2026-09-27
(https://developers.openai.com/api/docs/models/gpt-6-luna). Input is billed
at one of three rates — ordinary, cache read, or cache write — and nothing
before dispatch can know which, so the application reserves at the dearest
of them and releases the difference once the provider reports what it
actually used.

At the ceilings above, the conservative maximum for one short-context run is
120,000 input tokens at the cache-write rate ($0.125/M) plus 16,000 output
tokens ($0.50/M): **about $0.023**. The `$0.25` per-run ceiling is therefore
ample, and is a backstop rather than a binding constraint.

Requests above 272,000 input tokens are priced at the long-context rates for
the whole request, not just the excess. The per-run input ceiling is well
below that threshold, so a run cannot reach it without the ceiling being
raised deliberately.

## Checklist

Deterministic first. AI only after the deterministic service is healthy.

1. **Web service** — deploy from `render.yaml`. Docker runtime, health check
   `/api/ready`, manual deploys.
2. **Verify deterministic** — `/api/ready` returns 200, `/api/config` shows
   `deterministic` available and `ai` unavailable with a reason, a demo
   question runs end to end.
3. **Key Value instance** — provision Redis-compatible storage in the same
   region. Note the connection string; it is a secret.
4. **Provider credential** — create the key with the smallest scope that
   works. Put it in the dashboard only.
5. **Provider-side spend cap** — set a hard monthly cap on the account or
   project. Do this *before* enabling AI, not after.
6. **Verify the model** — confirm the exact identifier is accessible to that
   credential, and that `src/agentic_analytics/llm/pricing.py` has an entry
   for it. An unpriced model is refused at runtime.
7. **Confirm pricing** — check the entry against the provider's published
   prices and update the `reviewed` date.
8. **Set the ceilings** — every `AAE_AI_*` value above, deliberately, not by
   omission.
9. **Enable** — `AAE_AI_ANALYTICS_ENABLED=true`, redeploy.
10. **Verify capabilities** — `/api/config` shows `ai` available and
    `compare_available` true.
11. **One bounded AI question** — a single run. Check the response, the
    recorded usage and cost, and the logs for credential leakage.
12. **One Compare Both** — confirm two independent panes and that only the
    AI side consumed quota.
13. **Quota exhaustion** — lower a ceiling temporarily and confirm the 429
    message, then confirm Deterministic Analytics still works.
14. **Restart durability** — restart the service and confirm the daily
    counter did not reset.

## Rollback

Set `AAE_AI_ANALYTICS_ENABLED=false` and redeploy. AI becomes unavailable
with a stated reason; Deterministic Analytics is unaffected, because it
never reads the credential or the ledger. No data migration is involved: the
ledger holds counters only.
