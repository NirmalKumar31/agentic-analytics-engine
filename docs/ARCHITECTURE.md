# Architecture

How a question becomes a verified report, and why each boundary is where it
is.

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

In production the client connects over Streamable HTTP to `/mcp` on the same
process; in tests it connects in-process with `Client(server)`. Both are
covered.

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
