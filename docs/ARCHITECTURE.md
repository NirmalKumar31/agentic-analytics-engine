# Architecture

How a question becomes a verified report, and why each boundary is where it
is.

---

## 0. Two planes

```
        PROBABILISTIC CONTROL PLANE
   ┌──────────────────────────────────────┐
   │  Question Analyst   Planner          │   decides what to investigate
   │  Worker             Critic           │   interprets what came back
   │  Visualiser         Reporter         │   selects and organises
   └──────────────────┬───────────────────┘
                      │
                      │  typed requests: metric names, dimensions,
                      │  filters, test types, chart encodings
                      ▼
        DETERMINISTIC ANALYTICS PLANE
   ┌──────────────────────────────────────┐
   │  MCP server -- 17 tools, 4 resources │   owns every number
   │  Semantic metric layer               │
   │  DuckDB, locked read-only            │
   │  SciPy statistics + Holm correction  │
   │  Change decomposition                │
   │  Result registry                     │
   │  Numeric verification                │
   │  Chart builder                       │
   │  Session capability isolation        │
   └──────────────────────────────────────┘
```

The model never crosses into the lower plane. It cannot write a metric
formula, execute a query, compute a statistic, emit a chart specification,
choose a session, or validate its own arithmetic.

### What survives if every model provider is deleted

Dataset ingestion. Profiling and semantic schema inference. The metric layer.
DuckDB execution with its guard. Period comparison, segmentation, trend
analysis. Statistical testing with applicability checks and multiple-comparison
correction. Change decomposition. The MCP server and client. Numeric
verification. Provenance. Chart construction. Session isolation. The
evaluation harness. The API and the UI.

What is lost: natural-language interpretation, dynamic planning, hypothesis
generation, tool selection, follow-up reasoning, narrative generation.

A caller could drive the MCP tools directly and get every number, every
statistic and every decomposition this system produces. If deleting the model
destroyed the analytical product, the architecture would have failed its own
test.

---

## 1. The shape of a run

```
START
  │
  ├─ dataset_context      catalogue + metric definitions, emits dataset_loaded
  ├─ analyze_question     question -> structured brief
  ├─ plan_analysis        brief -> independent AnalysisTask objects
  │
  ├─ dispatch             one Send per task  ──┐
  │                                            │  concurrent
  ├─ analysis_worker  ×N  bounded tool loop  ──┘
  │
  ├─ aggregate_results    merge outcomes deterministically
  ├─ critique_findings    claim shape -> arithmetic -> critic
  │       │
  │       └─ followup_round   at most once, guarded by a counter in state
  │
  ├─ build_visualizations from verified results only
  ├─ write_report         from verified findings only
  ├─ verify_publication   strip any reference that did not survive
  └─ finalize
END
```

Exactly one conditional edge loops (`critique_findings → followup_round →
dispatch`). `followup_round` increments `followup_rounds` in state and
`needs_followup` reads it, so the loop can be taken at most once. Termination
is a property of the graph, not of a model deciding it is finished.

---

## 2. Boundaries

### Agents ↔ analytics

Agents never import `agentic_analytics.analytics`. They hold an
`AnalyticsToolset`, which owns a real `mcp.Client`. Three things follow:

- The MCP trace shown in the UI is the calls that crossed the boundary, not a
  description of them.
- Budgets are enforced at one place, because there is one place.
- The session id is injected by the toolset rather than accepted from the
  agent, so an agent cannot address another session's data. A test passes a
  foreign `session_id` and asserts the client overrides it.

**Which transport carries this, precisely.** The agent always holds a real
`mcp.Client`; what differs is what that client is connected to.

- **The deployed website uses the SDK's in-process transport.** `run_analysis`
  is handed the `MCPServer` object and the client connects to it directly.
  That is what makes the deployment one container, and it is the path every
  analysis on the site actually takes. It is a real client and a real server
  speaking the protocol -- not a function call dressed up as one -- but it is
  not a network hop, and claiming the site makes HTTP MCP requests to itself
  would be false.
- **`/mcp` is a separate Streamable HTTP transport** for callers outside this
  process. It is exercised in CI (`tests/integration/test_mcp.py` runs a real
  server) and again against the built container, where a second instance is
  started with a Host allow-list and asked to `initialize`, list tools, and
  refuse a tool call that presents the wrong capability.
- **On the public deployment `/mcp` is deliberately withdrawn**, because
  `AAE_MCP_ALLOWED_HOSTS` is left empty. An anonymous demo gains nothing from
  an internet-facing MCP endpoint, and the site is unaffected: its agents were
  never using that transport.

There is no setting to switch the web application onto HTTP. One existed and
nothing read it, which is worse than not having it.

### Model ↔ DuckDB

Nothing a model writes reaches DuckDB unchecked. See §6.

### Generator ↔ agents

`data/ground_truth.py` holds the answer key. `data/generator.py` imports it to
inject the patterns; `evaluation/` imports it to score. Nothing else may.
`tests/unit/test_ground_truth_isolation.py` parses the import graph of every
agent-facing package and fails if that module appears, and separately asserts
no injected entity name has been pasted into a prompt.

---

## 3. The result contract

Every analytical tool execution produces a `ResultSnapshot`:

```text
result_id, tool_name, task_id, sql, columns, rows, row_count,
truncated, dataset_fingerprint, started_at, duration_ms,
parameters, warnings, statistical_result
```

A statistical result adds `test_name`, `statistic`, `p_value`,
`sample_sizes`, `effect_size` and its name, `confidence_interval`,
`confidence_level`, `assumptions`, `warnings`.

Snapshots are the only thing a finding may cite. They are produced by
deterministic code; no model writes one. `run_query` is the single execution
path, so "was this query checked?" has one answer.

Truncation is detected rather than assumed: the executor fetches `max_rows + 1`
and sets `truncated` if the extra row came back.

---

## 4. The semantic metric layer

`warehouse/metrics.yml` defines four **models** (named relations) and twenty
**metrics** (aggregate expressions bound to a model).

```yaml
gross_margin_pct:
  model: sales
  description: Gross profit as a percentage of revenue. A ratio of sums,
    never an average of per-row margins.
  sql: >-
    100.0 * (SUM(quantity * unit_price) - SUM(quantity * unit_cost))
    / NULLIF(SUM(quantity * unit_price), 0)
  format: percent
```

A model chooses *what* to compute; the SQL is assembled here from the
definition, a validated dimension list and parameter-bound filters. Two tasks
that both report revenue cannot disagree about what revenue means.

Two tests keep the file honest: every metric must execute, and every dimension
a metric declares must actually group it.

**Filters** are never text. An agent supplies `{column, op, value}` triples;
the column is checked against the model's declared dimensions, the operator
against a closed set, and the value is bound as a parameter. A filter cannot
carry SQL even if a model is talked into trying.

**Time scope** gets special handling. A question naming "Q3 2025" is not
asking about Q3 in isolation — it is asking what changed relative to the
period before. `agents/timescope.py` widens a named quarter to its whole year
at quarter grain and records the named period separately, so the worker reports
the change *into* that quarter rather than the largest movement anywhere in
the dataset. Without this the engine answers a different question than the one
asked, which for seasonal data is almost never the movement the user meant.

Uploaded files have no metric layer. They take a different route: profile the
table, then compose a grouped aggregate from the columns and cardinalities the
profile reported. The second call is a genuine bounded loop — the SQL cannot
be written until the profile comes back — and the composed statement still
passes the guard.

---

## 5. Statistics

`analytics/stats.py` implements two-proportion z, Welch's t, one-way ANOVA,
chi-square, and Pearson / Spearman correlation. A model chooses the test and
the variables; every number is computed by SciPy or by explicit arithmetic
here.

Two design choices worth naming:

- **Welch's t and ANOVA are computed from group `n`/mean/sd**, not from raw
  values, so a test costs one aggregate query regardless of table size.
- **`correlation_matrix` uses DuckDB's `CORR`**, so no row-level data leaves
  the database.

Every test attaches its assumptions and a correlation-is-not-causation
warning, and flags shaky ones — fewer than 10 events per group for a
proportion test, expected cell counts below 5 for chi-square.

Tests assert against independent references (SciPy called directly, or a
closed-form F statistic), not against the implementation's own output.

---

## 6. SQL safety, in two layers

### Layer 1 — SQLGuard (`warehouse/sqlguard.py`)

Parses with `sqlglot` and works on the AST. Rejects any non-`SELECT` root,
any forbidden node anywhere in the tree (`Insert`, `Copy`, `Attach`,
`Pragma`, `Command`, …), any forbidden function (`read_csv`, `read_parquet`,
`getenv`, `duckdb_settings`, …), any string literal containing a remote URL,
any cross-catalogue reference, and any statement after the first. CTE names
are recognised as relations defined in the same statement rather than as
unknown tables.

The guard returns normalised SQL and callers execute *that*, so what was
validated is what runs.

### Layer 2 — the engine

Each session's DuckDB connection is built in two phases:

1. **Load** — Parquet or CSV is read. This needs filesystem access.
2. **Lock** — `SET enable_external_access = false`, then
   `SET lock_configuration = true`.

After phase 2 DuckDB refuses filesystem reads, network reads, `COPY`,
`ATTACH` and extension loading, and refuses to have either setting turned back
on. It is irreversible for the life of the connection, which is why it is done
once at construction.

`tests/unit/test_session_and_execute.py` bypasses the guard on purpose and
asserts the engine still refuses — a control that only holds when the layer
above it works is not defence in depth.

---

## 7. Verification

Three gates, in this order, first failure decides:

**1. Claim shape** (`verification/claims.py`) — deterministic.
Causal language (`caused`, `drove`, `led to`, `due to`) without hedging on
observational data; the word "significant" with no test behind the cited
result; a claim citing nothing.

**2. Arithmetic** (`verification/numeric.py`) — deterministic.
Every number in the text must appear in a cited result, or be derivable from
two cited cells by subtraction, ratio or percentage change. A declared
`claimed_change` is recomputed and compared, including its sign.

Two details matter. ISO dates and quarter labels are stripped before
extraction, so `2025-07-01` does not read as three numbers. Statistical
boilerplate is stripped too — "significant at the 5% level" states a
threshold, not a measurement, and treating it as one rejects correct findings.

**3. Semantics** — the critic model.
Whether the wording fairly describes the result, whether the direction is
right, whether the claim is broader than the evidence.

The ordering is deliberate: a model asked to check arithmetic will sometimes
agree with wrong arithmetic. By the time the critic runs, the numbers are
already settled, and its prompt says so.

Only `supported` is published. `partially_supported` is retained for audit
metrics and kept out of the report. A critic that *fails* returns
`partially_supported`, so a verifier that cannot answer never waves a finding
through.

`verify_publication` is a final pass that strips any report reference or chart
whose findings did not survive.

---

## 8. Charts

A model never emits a specification. It chooses a mark, an x field and a y
field; `verification/charts.py` builds the Vega-Lite spec and binds the rows
inline from the snapshot. That closes three holes at once: a chart cannot
reference a field the result lacks, cannot load data from a URL, and cannot
carry an expression that executes in the browser.

Forbidden anywhere in the tree: `signals`, `expr`, `transform`, `params`,
`selection`, `url`, `loader`, `datasets`. Allowed marks: `bar`, `line`,
`area`, `point`, `scatter`.

The built spec is re-validated by the same function that validates an
arbitrary one, so a future change to the builder cannot quietly widen what
ships. The browser re-checks it again before `vega-embed` sees it, because the
browser is where a hostile spec would actually run.

---

## 9. Parallelism and determinism

Workers are dispatched with LangGraph `Send`, one per task, and run
concurrently — the MCP client is safe for concurrent calls, which a test
asserts by issuing six at once and checking each gets its own `result_id`.

`merge_outcomes` is the reducer on `task_outcomes`: it concatenates,
de-duplicates by `task_id`, and **sorts**. Without the sort, section order in
the report would depend on which worker happened to finish first.

What is deterministic: the tasks, the calls per task, the findings, the
report. What is not: the order entries appear in the global MCP trace, since
that is real completion order. The reproducibility test asserts the former and
groups the trace by task rather than asserting a global sequence — asserting a
fixed order would be asserting something the system does not provide.

---

## 10. Budgets

Every ceiling is enforced where the resource is consumed, and exceeding one
degrades the run rather than failing it.

| Budget | Default | Enforced in |
|---|---|---|
| analysis tasks | 6 | planner |
| tool calls per task | 6 | `ToolBudget` |
| total tool calls | 48 | `ToolBudget` |
| LLM calls | 40 | `LLMProvider._check_budget` |
| runtime | 300 s | `RunContext.out_of_time` |
| follow-up rounds | 1 | graph state counter |
| result rows | 500 | `run_query` |
| sample rows | 20 | `sample_rows`, capped server-side |
| chart rows | 200 | chart builder |
| query timeout | 20 s | `con.interrupt()` on a timer |
| upload size | 25 MB | enforced while streaming |
| upload rows | 2,000,000 | rejected, not truncated |

The **Default** column is the code's default, which is what a local run
gets. The public deployment overrides several of them downwards --
15 MB and 750,000 rows rather than 25 MB and 2,000,000 -- because it is
sized for a 1 CPU / 2 GB (`1c-2g`) instance; see `render.yaml`.

`max_sample_rows` has a hard ceiling of 20 in the schema. Raw row disclosure
is the only path by which unaggregated user data reaches a prompt, so it
cannot be raised by configuration.

Budgets are a nested settings model, overridden as
`AAE_BUDGETS__MAX_ANALYSIS_TASKS`. A test asserts every key in both deployment
blueprints maps to a setting that exists — an early version set ceilings that
were silently ignored, which is worse than setting none.

---

## 11. Events and the UI

One closed `EventType` enum drives the recorder, the SSE stream and the
frontend. The execution-flow diagram derives every node and edge state from
events: a branch exists because the planner emitted that task and lights up
because that task started. The dash animation on an edge is present only while
a task is actually in flight. Nothing is on a timer.

The `EventBus` replays history on subscribe, so a client connecting mid-run
sees a complete timeline, and each subscriber owns a bounded queue — a slow
client loses events rather than stalling the run.

---

## 12. Deployment

One container. FastAPI serves the API, streams events over SSE, hosts the
built frontend, and carries the MCP endpoint at `/mcp` in the same process.

The MCP route is *adopted* from the SDK's Starlette app rather than mounted:
a Starlette mount only matches `/mcp/...`, so a bare `/mcp` would depend on a
slash redirect that the single-page fallback shadows. A regression test posts
to `/mcp` and asserts it is neither 404 nor 405.

`render.yaml` deploys recorded mode and needs no secret of any kind.
`deploy/render-live.yaml` enables live analysis with tighter ceilings, still
with no secret unless a cloud provider is explicitly selected.


---

## 13. Session capability isolation

There is no authentication. A session is reached with a capability, and two
identifiers are kept deliberately separate:

* **`session_id`** — a public opaque handle. It appears in MCP resource URIs
  and in tool arguments. On its own it authorises nothing.
* **`session_key`** — a private bearer capability, compared in constant time.
  It reaches the browser as an HttpOnly cookie (so it stays out of history,
  access logs and `Referer` headers) and is injected by the MCP client into
  every tool call. A model never sees or chooses either.

Both come from `secrets`. Neither is derived from the other, and an
incrementing id would make the handle guessable — which matters because the
handle is in URIs.

**Tools** require the capability. **Resources**, which are keyed only by the
handle, serve the built-in demo dataset and refuse an uploaded session:
`dataset://catalog` lists demo sessions only, and reading an uploaded
session's schema by handle returns an error directing the caller to the tools.
The demo warehouse is identical for every visitor and holds no user data, so
exposing it by handle discloses nothing.

The same 404 is returned for an unknown handle and a wrong capability, so a
caller cannot probe for which handles exist.

This is capability-based isolation, not authentication. Anyone holding the
token is the session. `tests/security/test_session_isolation.py` runs two
browsers with separate cookie jars and asserts neither can reach the other's
file, schema, results, runs or session.

---

## 14. Uploaded datasets

An upload is one arbitrary table, so there is nothing to look a metric up in.
It takes a different route:

1. **Validate by content.** Parquet is checked through its footer — columns,
   row groups, schema nesting, declared uncompressed size — before any data is
   read. CSV has no magic bytes, so it is screened against the signatures of
   formats it definitely is not, its header is bounded, and DuckDB's parser is
   the real arbiter. The code says this rather than claiming CSV is
   magic-byte validated.
2. **Isolate.** Its own DuckDB database, its own scratch directory under
   `/tmp`, both erased when the session ends. The table name is fixed by the
   server; a user filename is never a path component and never reaches SQL.
3. **Infer a schema.** Column roles from type and cardinality, everything
   marked `inferred`. A real number is always a measure — a price is not a key
   and not a grouping — and cardinality rules only apply once there are enough
   rows for a value to have had the chance to repeat.
4. **Ask when it matters.** Two columns that could both be revenue produce a
   clarifying question rather than a guess.
5. **Analyse.** Profile the table, then compose a grouped aggregate from the
   columns the profile reported. The second call is a genuine bounded loop —
   the SQL cannot be written until the profile comes back — and the composed
   statement still passes the guard.

An inferred measure is not a governed metric, and the UI says so.

---

## 15. Change decomposition

The tool that answers "why".

**Additive metrics** (revenue, units, orders). The total change is the sum of
per-segment changes, so each segment's contribution is its own change.

**Ratio metrics** (gross margin percent, return rate). A weighted average has
no additive split, so a shift-share decomposition runs instead:

```text
overall change = rate effect + mix effect + interaction

rate effect  = sum_s  w0_s * (r1_s - r0_s)      movement within segments
mix effect   = sum_s  (w1_s - w0_s) * r0_s      volume moving between them
interaction  = sum_s  (w1_s - w0_s) * (r1_s - r0_s)
```

where `w` is a segment's share of the denominator and `r` its own rate. This
separates "every segment got worse" from "volume moved to the worse segments"
— the distinction the Q3 margin question turns on. On the demo warehouse it
resolves to −3.76pp rate and −3.67pp mix against an observed −7.63pp.

Both forms reconcile to the observed change or the result is marked
`reconciled: false`, and no finding is generated from an unreconciled
decomposition. A randomised test asserts reconciliation across 400 cases.

Metrics declare how they decompose in `metrics.yml`; a ratio metric must
define its numerator and denominator or the registry refuses to load.

---

## 16. Multiple comparisons

A task running several related significance tests will produce a false
positive roughly one time in twenty per test. Holm's step-down method controls
the family-wise error rate without assuming independence, which is the right
default: segment comparisons on one dataset are usually correlated.

Correction is applied per analytical task, because a task is the unit in which
a worker asks a family of related questions. The result carries `p_value`,
`p_value_adjusted`, `correction_method` and `family_size`, and the publication
gate reads the adjusted value — so an uncorrected p-value can never be the
basis for a published significance claim. A family of one is left alone rather
than marked corrected.

Correction does not span tasks. That limit is stated in
[LIMITATIONS.md](LIMITATIONS.md).

---

## 17. Concurrency, learned the hard way

Workers run in parallel against one DuckDB connection per session. A DuckDB
connection carries cursor state, so reading `description` or `fetchall`
outside the session lock returns whichever query finished last.

That is not hypothetical: relation inspection for statistical tests was
written without the lock and, under parallel workers, reported the columns of
an unrelated result. `tests/integration/test_concurrency.py` exists because of
it, and asserts that concurrent queries get their own columns, that result ids
never collide, that decomposition is stable under contention, and that
repeated parallel runs agree.

Every query path now holds the lock. The audit is a grep away: outside the
single-threaded load phase in `session.py`, `con.execute` appears in three
places -- two in `analytics/execute.py` and one in `analytics/stats.py` --
and all three are inside `with session.lock`.
