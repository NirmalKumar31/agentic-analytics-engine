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
| `AAE_API_DOCS_ENABLED` | `true` | Interactive API docs and OpenAPI schema. Set false on an anonymous public deployment. |
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
| `AAE_AI_QUOTA_REDIS_URL` | *(unset)* | Redis-compatible shared ledger. Secret. The public Blueprint resolves it internally from `agentic-research-quota`; without a reachable store AI stays off. |
| `AAE_CLOUD_BASE_URL` | `https://api.openai.com` | Provider endpoint. |
| `AAE_CLOUD_TIMEOUT_SECONDS` | `120` | Application ceiling on one call, enforced independently of the HTTP client. |
| `AAE_CLOUD_REASONING_EFFORT` | `low` | How much the model may reason before answering. Reasoning tokens are billed as output. No sampling parameter is configurable: the engine's determinism comes from the analytics layer, not from model decoding. |

Per-run ceilings for an AI run. Deterministic runs are unaffected.

| Variable | Default | Purpose |
|---|---|---|
| `AAE_AI_MAX_LLM_CALLS` | `64` | Provider attempts, including failures and retries. Allows for the verification tail: one call per proposed finding, on top of planning and the tool loop. |
| `AAE_AI_MAX_INPUT_TOKENS` | `120000` | |
| `AAE_AI_MAX_OUTPUT_TOKENS` | `64000` | Sized for a reasoning model, whose hidden reasoning counts toward output. |
| `AAE_AI_VERIFICATION_OUTPUT_RESERVE` | `24000` | Output tokens only verification may spend. Not extra budget — a claim on part of the ceiling that the proposing stages cannot take. |
| `AAE_AI_MAX_RUNTIME_SECONDS` | `180` | |
| `AAE_AI_MAX_COST_MICRODOLLARS` | `250000` | $0.25. Integer microdollars; money is never a float. |

Public-demo ceilings, enforced in the durable ledger so a restart or a
second instance cannot reset them.

| Variable | Default | Purpose |
|---|---|---|
| `AAE_AI_RUNS_PER_SESSION` | `3` | |
| `AAE_AI_RUNS_PER_IP_PER_HOUR` | `5` | |
| `AAE_AI_CONCURRENT_RUNS` | `2` | |
| `AAE_AI_DAILY_COST_MICRODOLLARS` | `500000` | $0.50 a day. |
| `AAE_AI_TOTAL_COST_MICRODOLLARS` | `4000000` | $4.00 lifetime, against a $5 provider hard limit. Contradictory ceilings are refused at startup. |

### What resets, and what does not

The public analytics and research demos share one free Render Key Value
instance because the workspace permits only one. Their keys cannot collide:
analytics uses `aae:ai:*` and research uses `are:live-runs:*`. They do share a
failure domain. A restart of the free datastore erases both projects'
counters, so the provider's $5 hard limit remains the non-resetting external
backstop.

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
120,000 input tokens at the cache-write rate ($0.125/M) plus 64,000 output
tokens ($0.50/M): **about $0.047**. The `$0.25` per-run ceiling is therefore
ample, and is a backstop rather than a binding constraint.

A successful one-question smoke against `gpt-6-luna` on the demo warehouse
settled **$0.003156** across 15 completion attempts. It recorded 8,542
ordinary input, 6,018 cache-write input, 0 cached input, 3,084 output and
1,223 reasoning tokens; every reservation settled and none was retained. That
is evidence for that run, not a price forecast for an arbitrary upload. The
provider dashboard exposed only cent-granularity cumulative spend, so it
could not independently confirm a roughly three-tenths-of-a-cent delta; the
application figure reconciled to the provider-reported token categories plus
conservative per-call microdollar rounding.

Requests above 272,000 input tokens are priced at the long-context rates for
the whole request, not just the excess. The per-run input ceiling is well
below that threshold, so a run cannot reach it without the ceiling being
raised deliberately.

## What the instance can hold

The service runs on Render's **free** instance type: 512 MB, and it spins
down after 15 minutes without traffic, waking in about a minute. That
spin-down is the point — this is a portfolio demo, not a service with an
availability target — and it is safe here because nothing needs to survive
it. The demo warehouse is baked into the image at build time, uploads live
under `/tmp` for the life of a session, and the AI ledger is in Key Value.

`/api/health`, `/api/config` and every run payload expose the immutable build
revision from `RENDER_GIT_COMMIT` (or `AAE_BUILD_SHA` outside Render). The
package version identifies the release line; `build_sha` identifies the exact
source revision serving a request. Deployment verification should compare the
latter rather than infer a revision from visible UI changes.

512 MB is the constraint that shapes the rest of the configuration. Each
live session holds a DuckDB connection over the warehouse, costing roughly
55 MB. Measured on the demo warehouse, with two analyses running against a
full session table:

| live sessions | peak RSS | on a 512 MB instance |
|---|---|---|
| 3 | 438 MB | fits |
| 4 | 497 MB | within 15 MB of the limit |
| 6 | 600 MB | killed |
| 8 | 708 MB | killed |

Memory does not grow across runs — a single session doing twelve analyses
two at a time plateaus at 391 MB — so the cap is about how many sessions
are *open*, not how much work they do.

Hence `AAE_MAX_CONCURRENT_SESSIONS=3`. A fourth visitor evicts the least
recently used session rather than taking the instance down. **Raising it
without raising the plan is how this deployment dies under its first bit
of attention**, and there is a test asserting the ceiling for that reason.

If you want more than three concurrent visitors, move to a paid instance
type — `1c-2g` gives 2 GB — and raise the caps together. Paid instances do
not spin down, so that trades the scale-to-zero away.

## Checklist

Deterministic first. AI only after the deterministic service is healthy.

1. **Web service** — deploy from `render.yaml`. Docker runtime, health check
   `/api/ready`, manual deploys.
2. **Verify deterministic** — `/api/ready` returns 200, `/api/config` shows
   `deterministic` available and `ai` unavailable with a reason, a demo
   question runs end to end, and `scripts/live_acceptance.py` passes with
   `--expect-api-docs-disabled`.
3. **Key Value instance** — the public Blueprint resolves the existing
   `agentic-research-quota` connection internally. In another workspace,
   replace that service reference with a same-region store you control.
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
12. **One planning-strategy comparison** — confirm both strategy reports are
    available through the report switcher and that only the AI side consumed
    quota.
13. **Quota exhaustion** — lower a ceiling temporarily and confirm the 429
    message, then confirm Deterministic Analytics still works.
14. **Web-restart durability** — restart the web service and confirm the
    daily counter did not reset. This does not test a free Key Value restart,
    which is documented to erase the store.

## What the live deployment has been shown to do

The latest production audit is
[`RELEASE-EVIDENCE-production-2026-10-06.md`](RELEASE-EVIDENCE-production-2026-10-06.md).
It identifies the exact deployed commit, the ten-job CI run, the 156-cell
hosted browser sweep, the 60-check API acceptance run and one bounded paid
Compare canary. The evidence below is retained as the earlier v0.1.0 record.

`docs/RELEASE-EVIDENCE-v0.1.0.md` records the v0.1.0 measurements: a
55-check credential-free acceptance run against production, and one
authorised comparison request whose deterministic and AI halves both
published a total computed independently beforehand, for $0.001160.

It also records what was *not* exercised, which matters more for anyone
operating this: the rollback below has never been rehearsed against the
running service, no production restart has been performed to watch a
ledger counter survive one, and only Chromium was tested.

---

## Rollback

Set `AAE_AI_ANALYTICS_ENABLED=false` and redeploy. AI becomes unavailable
with a stated reason; Deterministic Analytics is unaffected, because it
never reads the credential or the ledger. No data migration is involved: the
ledger holds counters only.
