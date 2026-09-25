# Agentic Analytics Engine

A bounded multi-agent analytics workflow over DuckDB. A business question is
decomposed into analytical tasks, the tasks run deterministic queries and
statistics through an MCP tool layer, a verifier checks every proposed claim
against the rows it cites, and the report publishes only what survived.

Every number in the report is clickable. Clicking it shows the analytical
task, the SQL that ran, the result rows with the cited cells highlighted, the
arithmetic the engine recomputed, the MCP calls involved, and the dataset
fingerprint.

The whole thing runs with no API credentials.

---

## The problem this addresses

"Chat with your CSV" tools fail in a specific way: the model writes SQL, reads
the output, and then writes prose. Nothing checks that the prose matches the
output. A number that is off by a factor of ten, a direction that is
backwards, or a causal claim built on a correlation all look exactly like a
correct answer.

This project separates the three things that get conflated:

| | Produced by | Checked by |
|---|---|---|
| **Calculated fact** | DuckDB | Arithmetic recomputed from the cited cells |
| **Statistical result** | SciPy | The test is run, never asserted |
| **Interpretation** | The model | A critic, and deterministic claim-shape rules |

A claim that fails any check does not reach the report. In the recorded
`shipping-repeat` demo the worker proposes *"Being in the late group causes
the observed difference"*; the verifier withholds it, and the report says so.

---

## What runs

```
DATASET ──▶ ASK ──▶ PLAN ──▶ parallel analysis ──▶ VERIFY ──▶ REPORT
                                     │
                              MCP tool calls
                        (DuckDB queries, SciPy tests)
```

- **Backend** — Python 3.12, FastAPI, LangGraph 1.2, DuckDB 1.5, PyArrow, SciPy
- **Tools** — a real MCP server on the official Python SDK v2 (`mcp` 2.2), over
  Streamable HTTP in production and an in-process connection in tests
- **Frontend** — React 19, TypeScript, Vite, Vega-Lite
- **Provider** — pluggable; the default is a deterministic scripted stand-in

---

## Run it

```bash
make bootstrap    # virtualenv, Python and npm dependencies
make data         # generate the demo warehouse (deterministic, ~4 seconds)
make dev          # build the frontend and serve on http://127.0.0.1:8000
```

No `.env` is required. `make verify` runs everything CI runs.

```bash
make test         # 406 Python tests
make evaluate     # score the agents against the injected ground truth
make record       # re-record the three demo runs
```

---

## Why MCP, and what "real MCP" means here

The agents cannot import the analytics package. Every capability they have
arrives through an MCP client, so the tool trace the UI displays is the set of
calls that actually crossed that boundary rather than a narration of them.

That boundary is also where the budgets are enforced, where the session id is
injected (an agent cannot address another session's data), and where the trace
is recorded.

**12 tools** — `list_tables`, `describe_table`, `profile_table`, `sample_rows`,
`run_readonly_sql`, `list_metrics`, `compute_metric`, `compare_segments`,
`analyze_timeseries`, `correlation_matrix`, `statistical_test`, `get_result`

**4 resources** — `dataset://catalog`, `dataset://schema/{session_id}/{table}`,
`metrics://definitions`, `result://{session_id}/{result_id}`

Both transports are covered by tests: `Client(server)` in-process, and a real
`uvicorn` server over Streamable HTTP. Protocol version `2026-07-28`.

---

## The agents

| Agent | Does | Cannot |
|---|---|---|
| **Question Analyst** | Turns a question into target metrics, dimensions, time scope, ambiguities | Run SQL |
| **Analysis Planner** | Emits independent, executable `AnalysisTask` objects | Write prose the engine never reads |
| **Analysis Worker** | Runs one task in a bounded tool loop, states findings with cited cells | Exceed its per-task call budget |
| **Statistical Analyst** | Chooses a test and its variables | Compute a statistic, p-value or effect size |
| **Critic** | Judges whether wording fairly describes the result | Overrule the arithmetic check |
| **Visualisation Agent** | Picks a mark and two fields | Emit a chart specification |
| **Report Agent** | Writes from verified findings only | Introduce a number no finding supports |

Workers run concurrently via LangGraph `Send`. The graph has exactly one loop
edge, guarded by a counter in state, so it terminates by construction.

---

## Provenance

Every tool execution produces a `ResultSnapshot`: the SQL, the columns, the
rows, the row count, whether it was truncated, the duration, the parameters,
the warnings, and the dataset fingerprint. A finding cites `result_id`s and
specific `(row, column)` cells.

Before the critic model is consulted, two deterministic gates run:

1. **Claim shape** — causal language on observational data, the word
   "significant" with no test behind it, or a claim citing nothing.
2. **Arithmetic** — every number in the text must appear in a cited result or
   be derivable from two cited cells by subtraction, ratio or percentage
   change. A stated change is recomputed and compared.

A model is the wrong tool for checking arithmetic, so it is not asked to.

---

## The demo dataset

Generated locally from a fixed seed (`20260924`). No download, no Kaggle.

| Table | Rows |
|---|---|
| `customers` | 30,000 |
| `products` | 800 |
| `orders` | 104,915 |
| `order_items` | 239,794 |
| `returns` | 19,471 |
| `shipping_events` | 104,915 |
| `marketing_daily` | 2,924 |

Two years (2024-01-01 to 2025-12-31), 5.3 MB of Parquet, fingerprint
`sha256:8e9ad9348f7dc18660d78ed3cd4d4b32`. CI generates it twice and fails if
the fingerprints differ.

Six phenomena are deliberately injected — a Q3 margin compression driven by
discounting and a product-mix shift, a category with elevated returns
concentrated among new customers, a carrier degrading in one region, late
delivery suppressing repeat purchase, an acquisition channel that leads on
revenue and trails on contribution, and Q4 seasonality.

The answer key lives in `data/ground_truth.py`. A test walks the import graph
of every agent-facing package and fails if that module is reachable from any
of them.

---

## Evaluation

Eight benchmark questions, scored against the injected patterns after the run.

```
cases                            8 / 8 passed
injected patterns recovered      6 / 6
numeric accuracy                 1.00
SQL validity                     1.00
tool-call validity               1.00
provenance completeness          1.00
chart field validity             1.00
published support rate           0.95
unsupported findings published   0
```

35 findings published, 2 withheld, 35 MCP tool calls, 193 provider calls,
0.09 s mean runtime per question. Reproduce with `make evaluate`.

See [docs/EVALUATION.md](docs/EVALUATION.md) for what each metric measures and
how a case is scored.

---

## Security

The model never reaches DuckDB directly. Two independent layers stand between:

**SQLGuard** parses every statement with `sqlglot` and works on the AST.
Regex screening is defeated by comments, casing and string literals, and
cannot distinguish `read_csv` in a `FROM` clause from the same characters
inside a quoted string. 125 adversarial tests cover writes, DDL, `COPY`,
`ATTACH`, extension loading, remote URLs, multi-statement payloads,
file-as-table syntax, and DuckDB-specific forms such as `SUMMARIZE` and
`FROM x SELECT`.

**The engine itself** runs with `enable_external_access=false` and
`lock_configuration=true`, applied after the data is loaded and irreversible
for the life of the connection. Filesystem reads, network reads, `COPY`,
`ATTACH` and extension loading are refused by DuckDB even for a statement that
somehow got past the parser — which the tests verify by bypassing the guard on
purpose.

Uploads are validated by content, not by name: the size ceiling is enforced
while streaming, the format is decided from magic bytes, the stored filename
is server-generated, and the table name is fixed, so a user filename never
reaches SQL. Chart specifications are built by the engine from an encoding the
model chose, then validated on both the server and the browser.

Dataset values are data. A cell containing `IGNORE PREVIOUS INSTRUCTIONS` or
`<script>` round-trips as a string; React renders it as text and the SQL guard
treats it as a literal.

---

## Live and recorded modes

`AAE_LIVE_ANALYTICS_ENABLED=false` (the default, and what `render.yaml`
deploys) serves the three recorded runs and refuses to start a new analysis or
accept an upload. Recordings are captured from real runs and are written only
if they pass every publication rule — no unsupported finding, no chart field
that is not a column of its result, all SQL read-only, a dataset fingerprint
present, and no secret, host path or model reasoning anywhere in the file.
CI re-checks the committed recordings on every push.

Setting it to `true` accepts arbitrary questions. That still needs no
credential: the default provider is deterministic and free.

---

## Providers

| Mode | Needs a credential | Used for |
|---|---|---|
| `fake` | no | tests, CI, recordings, the public demo |
| `local` | no | an Ollama-compatible server |
| `cloud` | yes | a hosted API, configured only through the environment |

The scripted provider is not a language model. It is a rule-based stand-in
that produces the same structured outputs, reading every figure it writes out
of a real `ResultSnapshot`. On correlation tasks it proposes a causal claim,
because that is the mistake real models make most often — so the rejection
path is exercised by a genuine error rather than a staged one.

Reaching a paid endpoint requires both `AAE_PROVIDER_MODE=cloud` and a key in
the environment. No default path spends money.

---

## Layout

```
src/agentic_analytics/
  data/         deterministic generator + the injected answer key
  warehouse/    DuckDB sessions, metric layer, SQL guard, upload validation
  analytics/    result contract, execution path, metrics, SciPy statistics
  mcp_layer/    MCP server (tools + resources) and the client agents use
  llm/          provider interface, scripted / Ollama / cloud adapters
  agents/       analyst, planner, worker, critic, visualiser, reporter
  verification/ arithmetic, claim shape, chart safety
  graph/        LangGraph workflow, state reducers, run orchestration
  api/          FastAPI, SSE, uploads, mounted MCP endpoint
  recordings/   capture and publication acceptance
  evaluation/   benchmark cases and scoring
web/            React frontend
```

More detail in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Limitations

The scripted provider is a heuristic planner, not a language model — it maps
question keywords to metrics. Live mode with a real model exercises the same
graph, the same tools and the same verification, but its plans are not
represented in the measured numbers above.

Uploaded files get no semantic metric layer: one arbitrary table is profiled
and aggregated directly. Statistical tests requiring row-level values are
computed on the first 50,000 rows.

The Docker image has not been built on the development machine — no container
runtime is available there. The build and a container health check run in CI.

The full list is in [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

---

## Verified

406 Python tests, 35 frontend tests, 88% branch coverage. `ruff`,
`ruff format`, `mypy` (with `disallow_untyped_defs`), `pip-audit` and
`npm audit` all clean. Frontend production bundle 369.6 kB gzipped, of which
295.7 kB is the Vega chart engine in a lazily-loaded chunk.

MIT licensed.
