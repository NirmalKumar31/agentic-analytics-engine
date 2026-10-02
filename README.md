# Agentic Analytics Engine

One governed analytics engine, two orchestration strategies.

An analytics engine where the decision-making layer can be swapped without
swapping the trusted execution layer. Deterministic Analytics drives it with
a scripted provider; AI Analytics drives the same engine with a cloud model.
Compare Both runs one question through each and shows the results side by
side. In every case the computation, the statistics, the security and the
provenance are deterministic.

Point it at a built-in commerce warehouse or upload your own CSV or Parquet
file. Ask a question in plain English. The engine decomposes it into
analytical tasks, runs them in parallel through an MCP tool layer over DuckDB,
verifies every claim against the rows that produced it, and publishes only
what survived.

Every number in the report is clickable. Clicking it shows the task, the SQL,
the result rows with the cited cells highlighted, the arithmetic the engine
recomputed, the MCP calls involved, and the dataset fingerprint.

It runs with no API credentials. AI Analytics is off unless a deployment
explicitly enables it and supplies a model, a credential and a shared external
usage ledger; without all four it stays unavailable and Deterministic
Analytics is unaffected.

**Live demo:** [agentic-analytics-engine.onrender.com](https://agentic-analytics-engine.onrender.com/).
The service reports the availability of each mode at runtime; Deterministic
Analytics remains available when the AI credential or quota ledger is absent.

---

## How it is put together

```
        PROBABILISTIC CONTROL PLANE          decides what to investigate
   ┌──────────────────────────────────────┐
   │  Question Analyst   Planner          │
   │  Worker             Critic           │
   │  Visualiser         Reporter         │
   └──────────────────┬───────────────────┘
                      │  typed requests only
                      ▼
        DETERMINISTIC ANALYTICS PLANE        owns every number
   ┌──────────────────────────────────────┐
   │  MCP server (17 tools, 4 resources)  │
   │  Semantic metric layer               │
   │  DuckDB, locked read-only            │
   │  SciPy statistics                    │
   │  Change decomposition                │
   │  Result registry                     │
   │  Numeric verification                │
   │  Chart builder                       │
   │  Session isolation                   │
   └──────────────────────────────────────┘
```

The model chooses *what* to compute. It never computes. It does not write the
metric formulas, run the database, calculate a statistic, build a chart
specification, decide what a session may access, or validate its own numbers.

**What survives if you delete every model provider:** dataset ingestion,
profiling, semantic schema inference, the metric layer, DuckDB execution,
period comparison, segmentation, trend analysis, statistical testing, change
decomposition, the MCP server and client, the SQL guard, numeric
verification, provenance, chart construction, session isolation, the
evaluation harness, and the API. What is lost is natural-language
interpretation, dynamic planning, hypothesis generation and narrative writing.

That split is the point of the project. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## The problem it addresses

"Chat with your CSV" fails in a specific way: the model writes SQL, reads the
output, then writes prose, and nothing checks that the prose matches the
output. A number off by a factor of ten, a direction that is backwards, and a
causal claim built on a correlation all look exactly like a correct answer.

This separates three things those tools conflate:

| | Produced by | Checked by |
|---|---|---|
| **Calculated fact** | DuckDB | Arithmetic recomputed from the cited cells |
| **Statistical result** | SciPy | The test is run, never asserted |
| **Interpretation** | The model | Deterministic claim rules, then a critic |

A claim failing any check does not reach the report. In the recorded
`shipping-repeat` demo the worker proposes *"Being in the late group causes
the observed difference"*; the verifier withholds it and the report says so.

---

## Run it

```bash
make bootstrap    # virtualenv, Python and npm dependencies
make data         # generate the demo warehouse (deterministic, ~2 seconds)
make dev          # build the frontend and serve on http://127.0.0.1:8000
```

No `.env` required, and none is read for the figures below: every `make`
target that runs application code pins `AAE_PROVIDER_MODE=fake` explicitly, so
the posture is a property of the command rather than of what the checkout
happens to contain. `make verify` runs everything CI runs.

A direct CLI invocation does load `.env`, which is deliberate — that is how an
expert selects a provider. To develop against the cloud provider, ask for it by
name:

```bash
AAE_CONFIRM_PAID_LOCAL_RUN=1 make dev-cloud   # sends real, billed requests
```

`make dev-cloud` refuses to start without that confirmation. It makes no
attempt to discover whether a credential exists; the gate is on intent, not on
configuration.

```bash
make test         # 1,851 Python tests
make evaluate     # score the engine against the injected patterns
make record       # re-record the three demo runs
```

---

## Deterministic analytics, not generated SQL

The MCP server exposes analytical capabilities, not a SQL endpoint. In the
benchmark, **35 of 35 tool calls used a governed tool and none used
model-written SQL.**

**17 tools** — `list_tables`, `describe_table`, `profile_table`,
`profile_dataset`, `sample_rows`, `list_metrics`, `compute_metric`,
`compare_segments`, `compare_periods`, `analyze_timeseries`,
`decompose_change`, `rank_contributors`, `correlation_matrix`,
`statistical_test`, `aggregate_for_question`, `get_result`, and
`run_readonly_sql` as a guarded fallback.

**4 resources** — `dataset://catalog`, `dataset://schema/{session_id}/{table}`,
`metrics://definitions`, `result://{session_id}/{result_id}`.

The tool that answers "why" is `decompose_change`. For an additive metric it
splits a change into per-segment contributions. For a rate it runs a
shift-share decomposition separating movement *within* segments from volume
moving *between* them, and both reconcile exactly to the observed change or
report that they did not. On the demo warehouse the Q3 margin drop resolves to
−3.76pp of rate effect and −3.67pp of mix effect, summing to the observed
−7.63pp — which is the pattern the generator injected.

Built on the official MCP Python SDK v2 (`mcp` 2.2.0, protocol `2026-07-28`).
Agents always hold a real `mcp.Client`. On the deployed site that client uses
the SDK's **in-process transport** to the server object in the same container;
`/mcp` is a **separate Streamable HTTP transport** for external callers,
exercised in CI and against the built image. On the public deployment `/mcp`
is withdrawn on purpose — see [Security](#security).

---

## Two modes, one engine

```
                        USER QUESTION
                              |
             +----------------+----------------+
             |                                 |
             v                                 v
     DETERMINISTIC                         AI AGENT
     scripted planner                      cloud model
             |                                 |
             +----------------+----------------+
                              |
                          LangGraph
                              |
                             MCP
                              |
                    governed analytics
                              |
                           DuckDB
                              |
                        verification
                              |
                         provenance
                              |
                           report
```

The probabilistic orchestration layer can be replaced without replacing the
trusted execution layer.

| Mode | Who decides | What it costs |
|---|---|---|
| **Deterministic Analytics** | A scripted provider chooses the plan and the tools. The same question produces the same plan every time. | No external model call. Nothing leaves the server. |
| **AI Analytics** | A cloud model interprets the question, plans, selects tools and organises the report. | Bounded by per-run and public-demo ceilings, tracked in a shared external ledger. |
| **Compare Both** | Both, against the same session and dataset. | One AI run's worth. Only the AI side consumes quota. |

What the model controls in AI mode: question interpretation, task planning,
tool selection, which findings to propose, the evidence and relevance
judgements, and how the report is organised.

What stays deterministic in **both** modes: the SQL, the metric definitions,
the statistics, the SQL guard, numeric verification, the claim-shape rules,
the publication gate, the provenance, and the report's factual sentences,
which are the findings' own text rather than generated prose.

Compare Both never ranks the two sides. They differ in how the analysis was
planned, which is not evidence that either is more accurate.

The mode arrives with each request and the provider is constructed per run,
so one deployment serves both. `/api/config` reports which modes are
available and, when one is not, a reason a visitor can read.

---

## What is checked before a finding is published

Five gates, and they answer different questions:

1. **Claim shape** — no causal language from observational data, no
   significance claim without a test.
2. **Arithmetic** — every number in the sentence appears in a cited result.
3. **Answer coverage** — a direct aggregate must preserve the accepted
   operation, measure, grouping, row filters, period, output shape and, for a
   ranking, sort order. A grouped answer also carries counted coverage:
   groups returned against groups total, rows represented against rows
   matching. A breakdown is complete or it says which part of it is
   missing — it is never described as complete because nothing measured
   said otherwise. Each has its own rejection reason: "withheld as
   unsupported" tells a reader the engine found a data problem when it found
   a population problem.
4. **Evidence support** — the wording fairly describes what the cited result
   shows.
5. **Question relevance** — the finding materially answers the question, or a
   sub-question the task objective names.

Evidence and relevance are judged separately and recorded separately,
because a claim can be perfectly evidenced and still be off-topic. An
accurate, off-topic finding is withheld under `irrelevant_to_question`
rather than called unsupported. Both fail closed: a verifier that does not
answer has not approved anything.

A published finding is **numerically verified against cited query results**
and **independently checked for evidence support and question relevance**.
That is not a proof of semantic truth, and the phrase "verified" is not used
to imply one. `publication_gate_integrity` is an internal consistency metric
-- the fraction of published findings carrying a supporting verdict -- not
independent evidence that a finding is correct.

---

## If someone asks it something else

A public endpoint that dispatches a language model on whatever it is handed
is a general-purpose text generator with someone else's credit card behind
it. Three things stop that here, and only the first is a filter.

**Scope, before anything is spent.** A question that names nothing in the
dataset and uses no analytical vocabulary is refused with `422` before a
model is constructed, before a concurrency slot is taken, and before
anything billable happens. The check is deliberately tuned to admit rather
than reject: turning away a question the engine could have answered is a
broken product, while running one it cannot answer merely wastes a run.
It is a relevance test, not a safety classifier, and it does not pretend to
judge intent.

**Shape, which is the real defence.** The model is never asked for a factual
sentence. It decides which *verified* findings appear, how they are grouped
and in what order; the engine writes the report from each finding's own
checked text. `ReportPlan` — everything the reporter may decide — contains
two lists of ids and nothing else that can carry a claim. A model that is
never asked to assert something cannot assert something, which is a stronger
guarantee than scanning output for the assertions someone thought to look
for. A test asserts the schema itself, so adding a free-text field to it
fails the build.

**One bounded exception.** The report's suggested follow-up questions are
the only model-written sentences a visitor sees. They are capped at five,
truncated to 200 characters, stripped of anything containing a number or a
causal claim, and dropped entirely unless they name something in the
dataset.

The question itself is data throughout. It is never concatenated into an
instruction position, and a question containing "ignore previous
instructions" changes neither the plan nor the tools available — there is a
test that inspects the actual prompt payloads rather than reasoning about
the code path.

What this does **not** claim: it is not a content moderation system, and the
scope check is a keyword test that a determined person can phrase their way
past. What they reach if they do is an engine that can only call read-only
analytical tools over one dataset and can only publish sentences that
survived arithmetic verification — which is why the shape matters more than
the filter.

---

## Upload your own data

The public deployment accepts a CSV or Parquet file with no account.

Each upload gets a **capability-based anonymous session**: a cryptographically
random handle plus a separate bearer capability delivered as an HttpOnly
cookie. The handle appears in MCP resource URIs and authorises nothing on its
own. This is isolation, not authentication — anyone holding the token is the
session, and the docs say so rather than implying more.

- One file, 10 MB, 200 columns, CSV or Parquet only on the public deployment
- Its own DuckDB database and its own scratch directory, both erased when the
  session ends or expires after 30 minutes
- Parquet validated through its metadata — columns, row groups, nesting,
  declared uncompressed size — before any data is read
- CSV screened as bounded delimited text. **CSV has no magic bytes**, and the
  implementation does not claim otherwise
- Per-IP and global rate limits, and a cap on concurrent analyses

An uploaded file has no governed metric layer, so the engine infers one from
column types and cardinality and marks everything `inferred`. It will not
invent business meaning: two columns that could both be revenue produce a
clarifying question, not a guess.

### How a question about your file is answered

`aggregate_for_question` executes a **validated query contract** against that
inferred schema. Deterministic Analytics derives the contract from bounded
rules. AI Analytics can propose a typed contract, but every table name,
column, operation, filter, period and source excerpt is checked locally before
execution. A rule-detectable restriction is a lower bound: an AI plan that
omits “aged 30 to 40”, changes an inclusive bound, or names an unknown column
is refused. The engine composes the SQL itself and it still passes the guard.

**AI Analytics uses a cloud model to translate a natural-language question
into a typed analytical contract. SQL generation, computation, coverage
checks, verification, tables and charts are deterministic and governed.**
One planning call; the model never calculates a result, chooses a chart, or
restates a number the engine computed.

Supported: totals, averages, counts, minima and maxima; grouped
breakdowns of one or two cuts; explicit filters; rankings; explicit time
trends; and a safe refusal when the requested meaning cannot be mapped or
the complete result would exceed the configured limit.

Not supported, and refused rather than approximated: causal conclusions,
forecasts, arbitrary joins, significance claims without an implemented
test, invented measures or populations, and grouping by an identifier or
near-unique text. See [ADR 0003](docs/adr/0003-bounded-upload-analytics.md).

Where the rules resolve a question unambiguously, their contract is what
executes. A cloud planner that returns `profile` for `total sales by store`
does not cost the visitor the answer: the engine's own contract runs, the
report records that the planner did not decide it, and Compare Both reports
the same governed interpretation because that is what it is. Where the rules
are *not* confident, a plan that cannot be validated is refused with its
reason, because there is no contract to fall back to.

Well-formed is not the same as faithful, so the plan is bounded in four more
places. It may not group by a column the engine withholds as a grouping — an
identifier's values would become group labels, and from there reach a remote
prompt. It may not apply a time period the question did not state, or shift
one it did; date arithmetic has one correct answer and is rule-owned in both
modes. It may not reverse a ranking, because “highest” answered ascending is
the bottom of the table presented as the top while every other field agrees.
And where the rules resolved the question confidently, it may not change an
operation, measure or grouping the question named outright. A plan that
breaches any of these is refused rather than quietly corrected: the visitor
asked for AI planning, and a refusal tells them it disagreed.

The report leads with the answer, the population it covers, the rows it was
counted over and the grouped result, and puts the provenance after them. The
accepted calculation, measure, grouping, filters and period appear under
**Applied analysis**.

In Compare Both, the panes state when they executed the same canonical
contract. If the interpretations differ, the page says the results are not
like-for-like *and names which canonical fields diverged*, side by side — a
swapped measure and a dropped row restriction are very different things to
have happened.

When the question cannot be resolved into a safe contract, **it is refused with the
reason** and the table's profile is offered instead:

> this question could not be mapped to the table without guessing (the
> question does not name which numeric column to use, and the table has 2 to
> choose from)

That refusal is the feature. Aggregating whichever column happened to look
groupable would produce a number that reads like an answer without being one.

### What leaves the server

Nothing on a deterministic run: no inference call is made. An AI run sends
the schema, the inferred column roles and computed results.

Being precise about "computed results", because the obvious phrasing
overclaims. An aggregate grouped by a column **contains that column's
values**: a total by department cannot be reported without naming the
departments, so those labels reach the model and are disclosed as such.

What is withheld is unaggregated data. `sample_rows` is refused for an
uploaded dataset under remote inference, row-returning SQL over one is
refused rather than filtered, a profile's per-column minimum and maximum are
withheld because those are cells rather than summaries, and a column holding
a different value on almost every row is classified as an identifier so that
it can never become a group key — which is what stops a column of names
travelling as labels.

An aggregate over a group of one row can still equal a cell. That is inherent
to aggregation and is stated in [docs/LIMITATIONS.md](docs/LIMITATIONS.md)
rather than glossed over.

---

## Provenance

Every tool execution produces a `ResultSnapshot`: the SQL, columns, rows, row
count, truncation flag, duration, parameters, warnings, and the dataset
fingerprint. A finding cites `result_id`s and specific `(row, column)` cells.

Before the critic model is consulted, two deterministic gates run:

1. **Claim shape** — causal language on observational data, "significant" with
   no test behind it, or a claim citing nothing.
2. **Arithmetic** — every number in the text must appear in a cited result or
   be derivable from two cited cells by subtraction, ratio or percentage
   change. A stated change is recomputed and compared, including its sign.

A model is the wrong tool for checking arithmetic, so it is not asked to.

**And the report cannot undo that.** The reporter is an organiser, not an
author: it returns which verified findings belong in the summary and how to
group them, and the engine writes every factual sentence from those
findings' own text. Section headings are engine-chosen too, because a
heading short enough to look like a label is still long enough to assert
something. There is no free-text field in the schema the reporter fills, so
a sentence like *"New customers are the primary cause of poor Electronics
performance"* has nowhere to enter — which is a stronger guarantee than
scanning the prose afterwards for the claims someone thought to look for.

---

## The demo dataset

Generated locally from seed `20260924`. No download.

| Table | Rows |
|---|---|
| `customers` | 30,000 |
| `products` | 800 |
| `orders` | 104,915 |
| `order_items` | 239,794 |
| `returns` | 19,471 |
| `shipping_events` | 104,915 |
| `marketing_daily` | 2,924 |

Two years, 5.3 MB of Parquet, fingerprint
`sha256:8e9ad9348f7dc18660d78ed3cd4d4b32`. CI generates it twice and fails if
the fingerprints differ.

Six phenomena are injected: a Q3 margin compression from discounting and a
mix shift; a category with elevated returns concentrated in new customers; a
carrier running late in one region; late first delivery associated with lower
repeat purchase; an acquisition channel leading on revenue and trailing on
contribution; and Q4 seasonality.

The answer key lives in `data/ground_truth.py`. Two tests keep it away from
the agents: one walks the import graph, and one captures every prompt, context
object and schema any provider sees during a full benchmark and asserts the
answer key appears in none of them.

---

## Benchmark

**This is a deterministic end-to-end engine benchmark.** It runs with the
scripted provider, so it measures graph execution, MCP execution, SQL and
statistical correctness, provenance, deterministic verification and
publication behaviour. It does **not** measure language-model question
understanding, planning quality or tool-selection reliability.

```
cases passed                      8 / 8
injected patterns recovered       6 / 6

candidate findings                37
  supported                       35
  withheld                         2
candidate support rate            35/37 = 0.946

published findings                35
  unsupported published            0
publication-gate integrity        35/35 = 1.000

findings numerically verified     35/35 = 1.000
SQL statements SQLGuard accepts   33/33 = 1.000
tool calls succeeded              35/35 = 1.000
provenance complete               35/35 = 1.000
chart fields valid              205/205 = 1.000

tool calls using a governed tool  35/35 = 1.000
tool calls using generated SQL     0
```

The two rates answer different questions. Candidate support is *expected*
below 1.0 — a run that withholds nothing is not verifying anything.
Publication-gate integrity must be exactly 1.0, and is a consistency check on
the gate rather than an accuracy score: it asks whether the gate emitted
anything its own pipeline rejected. Independent evidence that the findings
are *right* comes from the injected patterns, which the agents cannot see.

The eight cases are a controlled synthetic regression suite over one
generated warehouse. They are not evidence of external validity across
arbitrary business datasets, and nothing here should be read as such.

Engine runtime is 0.094 s per question on a local machine and about 0.15 s on
a GitHub-hosted runner, both with the scripted provider. That is a
deterministic engine figure, it varies with the hardware, and it **excludes
model inference entirely** — it is not a latency claim about an AI system.

Reproduce with `make evaluate`. Details in
[docs/EVALUATION.md](docs/EVALUATION.md).

---

## Security

The model never reaches DuckDB directly.

**SQLGuard** parses every statement with `sqlglot` and works on the AST.
230 adversarial tests cover writes, DDL, `COPY`, `ATTACH`, extension loading,
remote URLs, multi-statement payloads, file-as-table syntax, and DuckDB
specifics like `SUMMARIZE` and `FROM x SELECT`. A further set covers read-only
denial of service: unbounded generators, cross-join explosion, AST size and
depth, join and CTE ceilings, and recursive CTEs, which are refused outright.
Deep nesting previously crashed sqlglot's recursive-descent parser before any
check could run.

**The engine** runs with `enable_external_access=false` and
`lock_configuration=true`, applied after loading and irreversible for the
connection's life. Tests bypass the guard on purpose to confirm DuckDB refuses
on its own. Query cancellation uses `con.interrupt()`, verified to stop CPU
work rather than just the waiting coroutine.

**The MCP endpoint fails closed.** A loopback binding gets a localhost
allow-list automatically. A network binding must declare its hostnames; one
that declares none has the remote transport withdrawn with a 503 rather than
served with Host validation disabled. The public deployment leaves the
allow-list empty, so the endpoint is withdrawn there by choice — the site's
agents reach the same MCP server in-process, so nothing is lost but the
exposure.

**Uploaded cells do not go to a remote model.** `sample_rows` is refused for
an uploaded dataset whenever inference is remote, and a profile's per-column
minimum and maximum are withheld from the one representation handed to an
agent. A test inspects the actual prompt payloads rather than the code path.

**Charts** are built by the engine from an encoding the model chose, then
validated on the server and again in the browser.

Dataset values are data. A cell containing `IGNORE PREVIOUS INSTRUCTIONS` or
`<script>` round-trips as a string.

---

## Providers

| Mode | Credential | Used for |
|---|---|---|
| `fake` | no | tests, CI, recordings and Deterministic Analytics |
| `local` | no | an Ollama-compatible server |
| `cloud` | yes | AI Analytics and the AI half of Compare Both |

The scripted provider is a rule-based stand-in, not a language model. It reads
every figure it writes out of a real `ResultSnapshot`, and on correlation
tasks it proposes a causal claim — the mistake real models make most often —
so the rejection path is exercised by a genuine error.

The CLI and evaluation harness use `AAE_PROVIDER_MODE`; the web application
does not. A web request reaches a paid endpoint only when it explicitly asks
for AI Analytics or a planning-strategy comparison *and* the deployment has
enabled AI, supplied an exactly priced model and credential, and connected the
shared quota ledger. No default path spends money.

Locally, `make` targets pin the scripted provider rather than relying on that
default: settings load `.env`, so a checkout that has one could otherwise
select a paid provider for an ordinary development command. `make dev-cloud`
is the one target that asks for the cloud provider, and it refuses without
`AAE_CONFIRM_PAID_LOCAL_RUN=1`.

---

## Layout

```
src/agentic_analytics/
  data/         deterministic generator + the injected answer key
  warehouse/    DuckDB sessions, capability model, metric layer, SQL guard, uploads
  analytics/    result contract, execution, metrics, statistics, decomposition, semantics
  mcp_layer/    MCP server (tools + resources) and the client agents use
  llm/          provider interface, scripted / Ollama / cloud adapters
  agents/       analyst, planner, worker, critic, visualiser, reporter
  verification/ arithmetic, claim shape, chart safety, multiple comparisons
  graph/        LangGraph workflow, state reducers, run orchestration
  api/          FastAPI, SSE, uploads, rate limits, mounted MCP endpoint
  recordings/   capture and publication acceptance
  evaluation/   benchmark cases and scoring
web/            React frontend
```

---

## Limitations

The benchmark numbers come from the scripted provider and measure the engine,
not model planning. Statistical scope is five test families with Holm
correction within a task and no causal identification. Correlations above
50,000 rows use a seeded sample. There is no authentication — session
capability is not an account. Deterministic rate limits are in-process and
reset on restart. The AI spend ceilings are atomic and shared across web
instances and web restarts, but a free Render Key Value instance loses its
counters if the datastore itself restarts. The public research and analytics
demos share that store with disjoint key prefixes, so their counters cannot
collide but their datastore failure domain is shared. These are application
controls, not a billing guarantee, so a provider-side hard spend cap is
required before AI is enabled.

The relevance gate judges whether a finding addresses the question. It does
not check unit semantics, whether the source data is correct, or whether a
business interpretation is sound, and question classes outside the committed
benchmark and evaluation artifacts are untested.

The full list is in [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

---

## Verified

1,851 Python tests, 92 frontend tests, and 21 Chromium browser tests. Each
figure comes from its own run; they are never summed across overlapping
suites. 89% branch coverage. The deployed deterministic path passes a
**57-check** credential-free acceptance run plus an independent Parquet
upload, analysis, provenance and cleanup check. Two of those checks are
HTTPS-only, so the same script reports 55 against a local container.

Both public modes are verified against the live service. Three authorised
Compare Both runs on 30 Sep 2026 asked one question with a time period. In
two, the cloud model's typed plan validated to a **byte-identical canonical
contract** to the schema-grounded rules, and both panes published the total
computed independently beforehand. In the third the model declared the
question ambiguous and the engine **refused with its stated reason** rather
than guessing or silently falling back to the rule contract. Two of three is
two of three: the variance is real and a reader of a single comparison cannot
see it. Total $0.003170, recorded in
[docs/LIMITATIONS.md](docs/LIMITATIONS.md) §11.

An earlier authorised Compare Both run published the correct total on the
deterministic and the AI side alike — a figure computed independently before
the run — for **$0.001160**, with every cited evidence cell resolving and no credential,
internal endpoint or uploaded row appearing in any public response.
`docs/RELEASE-EVIDENCE-v0.1.0.md` records the measurements and what they do
not cover: Chromium only, no rehearsed AI-off rollback, and one paid run
rather than a sample.

`ruff`, `ruff format`, `mypy` (with `disallow_untyped_defs`), `pip-audit`
and `npm audit` clean. Frontend production bundle about 380 kB gzipped, of
which about 296 kB is the Vega chart engine in a lazily-loaded chunk.

MIT licensed.
