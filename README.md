# Agentic Analytics Engine

An analytics engine where language models plan investigations and interpret
results, while deterministic tools own computation, statistics, security and
provenance.

Point it at a built-in commerce warehouse or upload your own CSV or Parquet
file. Ask a question in plain English. The engine decomposes it into
analytical tasks, runs them in parallel through an MCP tool layer over DuckDB,
verifies every claim against the rows that produced it, and publishes only
what survived.

Every number in the report is clickable. Clicking it shows the task, the SQL,
the result rows with the cited cells highlighted, the arithmetic the engine
recomputed, the MCP calls involved, and the dataset fingerprint.

It runs with no API credentials.

---

## The architecture that matters

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
   │  MCP server (16 tools, 4 resources)  │
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

No `.env` required. `make verify` runs everything CI runs.

```bash
make test         # 540 Python tests
make evaluate     # score the engine against the injected patterns
make record       # re-record the three demo runs
```

---

## Deterministic analytics, not generated SQL

The MCP server exposes analytical capabilities, not a SQL endpoint. In the
benchmark, **35 of 35 tool calls used a governed tool and none used
model-written SQL.**

**16 tools** — `list_tables`, `describe_table`, `profile_table`,
`profile_dataset`, `sample_rows`, `list_metrics`, `compute_metric`,
`compare_segments`, `compare_periods`, `analyze_timeseries`,
`decompose_change`, `rank_contributors`, `correlation_matrix`,
`statistical_test`, `get_result`, and `run_readonly_sql` as a guarded
fallback.

**4 resources** — `dataset://catalog`, `dataset://schema/{session_id}/{table}`,
`metrics://definitions`, `result://{session_id}/{result_id}`.

The tool that answers "why" is `decompose_change`. For an additive metric it
splits a change into per-segment contributions. For a rate it runs a
shift-share decomposition separating movement *within* segments from volume
moving *between* them, and both reconcile exactly to the observed change or
report that they did not. On the demo warehouse the Q3 margin drop resolves to
−3.76pp of rate effect and −3.67pp of mix effect, summing to the observed
−7.63pp — which is the pattern the generator injected.

Built on the official MCP Python SDK v2 (`mcp` 2.2.0, protocol `2026-07-28`),
tested in-process and over a real Streamable HTTP server.

---

## Three execution modes, labelled

A scripted-provider run executes the same graph, the same MCP calls, the same
SQL and the same verification as a model-driven one. It is still not a
language model, and the UI never implies otherwise.

| Badge | What it means |
|---|---|
| **Recorded** | Replaying a committed run |
| **Deterministic live** | Executing now; agent decisions from a scripted deterministic provider |
| **AI live** | Executing now; agent decisions from a language model |

`/api/config` reports `execution_mode` and `model_inference_remote`, and every
recording carries `provider_kind` and `run_kind`.

---

## Upload your own data

The public deployment accepts a CSV or Parquet file with no account.

Each upload gets a **capability-based anonymous session**: a cryptographically
random handle plus a separate bearer capability delivered as an HttpOnly
cookie. The handle appears in MCP resource URIs and authorises nothing on its
own. This is isolation, not authentication — anyone holding the token is the
session, and the docs say so rather than implying more.

- One file, 25 MB, 200 columns, CSV or Parquet only
- Its own DuckDB database and its own scratch directory, both erased when the
  session ends or expires after 45 minutes
- Parquet validated through its metadata — columns, row groups, nesting,
  declared uncompressed size — before any data is read
- CSV screened as bounded delimited text. **CSV has no magic bytes**, and the
  implementation does not claim otherwise
- Per-IP and global rate limits, and a cap on concurrent analyses

An uploaded file has no governed metric layer, so the engine infers one from
column types and cardinality and marks everything `inferred`. It will not
invent business meaning: two columns that could both be revenue produce a
clarifying question, not a guess.

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
published support rate            35/35 = 1.000

numeric assertions correct        35/35 = 1.000
SQL statements read-only          33/33 = 1.000
tool calls succeeded              35/35 = 1.000
provenance complete               35/35 = 1.000
chart fields valid              205/205 = 1.000

tool calls using a governed tool  35/35 = 1.000
tool calls using generated SQL     0
```

The two support rates answer different questions and are both reported: a run
that withholds nothing is not verifying anything, so candidate support is
*expected* below 1.0. Published support must be exactly 1.0.

Engine runtime is 0.094 s per question on a local machine and 0.150 s on a
GitHub-hosted runner, both with the scripted provider. That is a deterministic
engine figure, it varies with the hardware, and it **excludes model inference
entirely** — it is not a latency claim about an AI system.

Reproduce with `make evaluate`. Details in
[docs/EVALUATION.md](docs/EVALUATION.md).

---

## Security

The model never reaches DuckDB directly.

**SQLGuard** parses every statement with `sqlglot` and works on the AST.
177 adversarial tests cover writes, DDL, `COPY`, `ATTACH`, extension loading,
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
served with Host validation disabled.

**Charts** are built by the engine from an encoding the model chose, then
validated on the server and again in the browser.

Dataset values are data. A cell containing `IGNORE PREVIOUS INSTRUCTIONS` or
`<script>` round-trips as a string.

---

## Providers

| Mode | Credential | Used for |
|---|---|---|
| `fake` | no | tests, CI, recordings, the public demo |
| `local` | no | an Ollama-compatible server |
| `cloud` | yes | a hosted API, configured only through the environment |

The scripted provider is a rule-based stand-in, not a language model. It reads
every figure it writes out of a real `ResultSnapshot`, and on correlation
tasks it proposes a causal claim — the mistake real models make most often —
so the rejection path is exercised by a genuine error.

Reaching a paid endpoint requires both `AAE_PROVIDER_MODE=cloud` and a key in
the environment. No default path spends money.

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
capability is not an account. Rate limits are in-process, not durable quotas.

The full list is in [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

---

## Verified

540 Python tests, 35 frontend tests, 87% branch coverage. `ruff`,
`ruff format`, `mypy` (with `disallow_untyped_defs`), `pip-audit` and
`npm audit` clean. Frontend production bundle 383 kB gzipped, of which 298 kB
is the Vega chart engine in a lazily-loaded chunk.

MIT licensed.
