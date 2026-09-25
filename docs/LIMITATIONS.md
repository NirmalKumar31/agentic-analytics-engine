# Limitations

What this does not do, what has not been verified, and where the edges are.

---

## 1. The benchmark measures the engine, not model planning

The default provider is a rule-based stand-in, not a language model. It maps
question keywords to metrics and produces the same structured outputs a model
would, reading every figure it writes out of a real `ResultSnapshot`.

That is what lets tests, CI, the recorded demos and the public deployment run
with zero credentials and identical results. It also means the numbers in
[EVALUATION.md](EVALUATION.md) measure graph execution, MCP execution, SQL and
statistical correctness, provenance, verification and publication — and not
question understanding, planning quality or tool-selection reliability.

Live mode drives the same graph, the same MCP tools and the same verification,
and every safety and provenance guarantee holds identically. Plan quality
under a real model is simply not measured, because measuring it would require
paid calls.

**What the scripted provider is scripted to do**, stated plainly so the
benchmark cannot be mistaken for autonomous planning:

- maps question keywords to metrics and dimensions from the catalogue
- derives a comparison window from a named period (Q3 2025 → Q2 vs Q3)
- emits a fixed task shape: a trend per target metric, one or two segment
  cuts, a driver decomposition when the question asks *why*, and a statistical
  test when the question asks whether one thing relates to another
- reads findings out of result rows, never inventing a number
- proposes a causal claim on correlation tasks, because that is the mistake
  real models make most often

The plans are not question-specific lookups — the same rules run for every
question — but they are rules, not reasoning.

---

## 2. Statistical scope

- Six tests in five families: two-proportion z, Welch's t, one-way ANOVA,
  chi-square, and Pearson / Spearman correlation. No regression, no
  time-series decomposition, no causal inference.
- **Multiple comparisons** are corrected with Holm within an analytical task,
  and the publication gate reads the adjusted p-value. Correction does *not*
  span tasks: a run performing related tests in two separate tasks has two
  families, not one. That is a real limit on the guarantee.
- Applicability is validated before a test runs — a correlation on text or a
  t-test on a category label is refused rather than returning a meaningless
  number — but nothing refuses a test whose *distributional* assumptions are
  violated. The warnings travel with the result; a reader has to read them.
- Welch's t and ANOVA are computed from group n/mean/sd. Exact for those
  statistics, but no distributional diagnostics are available.
- Correlations above 50,000 rows use a seeded DuckDB reservoir sample. The
  snapshot records `rows_available`, `rows_used`, `sampling_applied`,
  `sampling_method` and `sampling_seed`, and the same request reproduces the
  same coefficient — but it is still an estimate.

---

## 3. Verification has a ceiling

The arithmetic check is complete for what it covers: every number must appear
in a cited result or be derivable from two cited cells by subtraction, ratio
or percentage change. Derivations outside that set — a weighted average across
three cells, a compound growth rate — would be rejected even if correct. That
is the safe direction to fail, and it is a limit.

The semantic check is a model and inherits a model's judgement. It runs last,
cannot overrule the arithmetic, and a critic that fails returns
`partially_supported` rather than approving. But "is this wording a fair
description of this result" has no deterministic answer.

Claim-shape rules are keyword-based. A causal claim phrased unusually could
pass; an innocent sentence using "drove" could be withheld.

---

## 4. Security boundaries

**What holds.** Two independent layers stand between a model and DuckDB: an
AST-based SQL guard and an engine locked with `enable_external_access=false`
and `lock_configuration=true` after load. 150 adversarial tests cover both,
including tests that bypass the guard on purpose. Query cancellation uses
`con.interrupt()` and was verified to stop CPU work, not merely the waiting
coroutine.

**What is not claimed.**

- **Session capability is not authentication.** Anyone holding the token is
  the session. There are no accounts, no identity and no revocation beyond
  ending the session. The isolation between two visitors is real and tested;
  the boundary is a bearer secret, and that is all it is.
- **Rate limits are in-process counters, not durable quotas.** A restart
  resets them and a second replica would count separately. They raise the cost
  of casual abuse. The client key comes from `X-Forwarded-For`, which is
  client-supplied and therefore spoofable.
- **No sandboxing of the DuckDB process.** The lockdown is a DuckDB
  configuration, not an OS boundary. A DuckDB vulnerability would not be
  contained by it.
- **Uploaded data lives in process memory** for the session's life. The
  temporary file is deleted once the rows are loaded and the scratch directory
  is removed with the session, but a memory dump of a running process would
  contain the data.
- **Resource exhaustion is bounded, not eliminated.** Generator arguments, AST
  size and depth, joins, CTEs and set operations are all capped, recursive
  CTEs are refused, and the query timeout is real. A query that is expensive
  without being structurally unusual — a legitimate aggregate over the whole
  warehouse — is bounded only by the timeout and the memory limit.
- **The MCP endpoint fails closed** for a network binding with no host
  allow-list: the transport is withdrawn rather than served unvalidated. When
  an allow-list *is* configured, Host and Origin validation is the SDK's, and
  the endpoint still accepts any caller who supplies a valid session handle
  and capability.

---

## 5. Uploaded datasets

- One file per session. No joins across uploads, no multi-file ETL, no
  user-defined metrics.
- 25 MB, 200 columns, CSV or Parquet only. No Excel, JSON, SQLite, archives or
  URL ingestion.
- **CSV validation is not format validation.** CSV has no magic bytes. The
  file is screened against signatures of formats it is definitely not, its
  header is bounded, and DuckDB's parser is the real arbiter. Parquet is
  genuinely validated through its metadata before any data is read.
- The inferred semantic schema is heuristic: roles come from column types and
  cardinality. It is marked `inferred` everywhere and will misclassify —
  a numeric code with few distinct values reads as a dimension, a text column
  with one value per row is dropped as ungroupable.
- Analysis of an uploaded file is profile-then-aggregate. It produces a real,
  verified answer for the common shape (a categorical column and a numeric
  one) and little for anything else. The demo warehouse is where the
  interesting analysis lives.

---

## 6. Change decomposition

Additive decomposition is exact. Shift-share decomposition for rate metrics
reconciles exactly and separates rate movement from mix movement, but:

- it is descriptive, not causal — it attributes arithmetic, not cause
- it requires the metric to declare a numerator and denominator, so metrics
  without one (`roas`, `average_order_value`) cannot be decomposed
- a segment present in only one period contributes its whole weight to the mix
  effect, which is correct arithmetic and can read oddly
- if the components do not reconcile the result is marked `reconciled: false`
  and no finding is generated from it

---

## 7. Determinism, precisely

Deterministic: the generated warehouse (byte-identical for a seed, checked in
CI), the analytical tasks, the tool calls per task, the findings, the report,
and any sampled correlation.

Not deterministic: the order entries appear in the global MCP trace, because
workers run concurrently and the trace records real completion order. The
reproducibility test groups the trace by task rather than asserting a global
sequence.

---

## 8. Scale

Sized for a demo. Each session materialises its dataset into a private
in-memory DuckDB with a 1 GB limit and 2 threads. Results returned to an agent
are capped at 500 rows, charts at 200. A run is bounded to 30–48 MCP tool
calls and 120–300 seconds depending on deployment. No query cache, no
incremental computation, no persistence between sessions.

---

## 9. Frontend

- No client-side routing; one page.
- No virtualised tables. A 500-row result renders capped at 60 displayed rows.
- Vega is 288 kB gzipped, lazily loaded on first chart render, and dominates
  the bundle.
- Tested with Vitest and Testing Library. Playwright is not used — the
  environment could not download its browser — so there is no automated
  end-to-end browser test. Flows were verified manually through headless
  Chrome driven over the DevTools protocol, including a real file upload.

---

## 10. Deliberately out of scope

No authentication, billing, multi-tenant persistence or scheduled jobs. No
arbitrary Python or notebook execution by the model. No vector database, RAG
or web search. No warehouse OAuth connectors, BI integrations or write-back.
No forecasting or model training.

These are absent by design.
