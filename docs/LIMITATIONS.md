# Limitations

What this does not do, what has not been verified, and where the edges are.

---

## 1. The measured numbers come from a scripted provider

The default provider is not a language model. It is a rule-based stand-in that
maps question keywords to metrics and produces the same structured outputs a
model would, reading every figure it writes out of a real `ResultSnapshot`.

This is what lets tests, CI, the recorded demos and the public deployment run
with zero credentials and identical results. It also means the evaluation in
[EVALUATION.md](EVALUATION.md) measures the *engine* — the metric layer, the
statistics, the verification gates, the provenance chain — and not plan
quality under a real model.

Live mode (`local` or `cloud`) drives the same graph, the same MCP tools and
the same verification. The safety and provenance guarantees are identical. The
planning is not measured, because measuring it would need paid calls.

The scripted provider deliberately proposes a causal claim on correlation
tasks, because that is the mistake real models make most often. That is
modelling a known failure, not simulating a rejection: the claim is generated
by the same code path as every other finding, and a real deterministic rule
catches it.

---

## 2. The Docker image has not been built locally

No container runtime is available on the development machine — Docker Desktop
is present as a broken stub with no binaries, and there is no podman, colima
or nerdctl.

What *was* verified instead: the wheel builds, installs cleanly into a fresh
virtualenv against `constraints.txt`, and then performs every step the
Dockerfile performs — generating the demo warehouse to a byte-identical
fingerprint, validating the recordings, and serving a healthy app with only
runtime dependencies present.

The image build itself, and the container health check, run in CI and have not
executed anywhere else. Treat the Dockerfile as reviewed and structurally
de-risked rather than as proven.

---

## 3. Uploaded files get no semantic metric layer

An upload is one arbitrary table. There is nothing to look a metric up in, so
`compute_metric`, `compare_segments` and `analyze_timeseries` are unavailable
and the run takes a different route: profile the table, then compose a grouped
aggregate from the columns and cardinalities the profile reported.

That produces a real, verified answer for the common shape (a categorical
column and a numeric one) and little for anything else. Joins across uploaded
files, user-defined metrics and multi-file ETL are explicitly out of scope for
v1.

The demo warehouse is where the interesting analysis lives.

---

## 4. Statistical scope

- Six tests in five families: two-proportion z, Welch's t, one-way ANOVA,
  chi-square, and Pearson / Spearman correlation. No regression, no time-series
  decomposition, no causal inference, no multiple-comparison correction.
- Running several segment comparisons in one report raises the family-wise
  error rate. Nothing adjusts for that, and nothing claims to.
- Tests needing row-level values (the correlations) are computed on the first
  50,000 rows, and the snapshot says so when it sampled.
- Welch's t and ANOVA are computed from group `n`/mean/sd rather than raw
  values. This is exact for those statistics but means no distributional
  diagnostics are available.
- Every test attaches its assumptions and flags shaky ones — small event
  counts, low expected cell counts — but nothing refuses to run a test whose
  assumptions are violated. The warning travels with the result; a reader has
  to read it.

---

## 5. Verification has a ceiling

The arithmetic check is complete for what it covers: every number must appear
in a cited result or be derivable from two cited cells by subtraction, ratio
or percentage change. Derivations outside that set — a weighted average across
three cells, a compound growth rate — would not match and would be rejected
even if correct. That is the safe direction to fail, but it is a limit.

The semantic check is a model, so it inherits a model's judgement. It runs
last, after the arithmetic is settled, and cannot overrule it; a critic that
fails returns `partially_supported` rather than approving. But "is this
wording a fair description of this result" has no deterministic answer, and
the critic will sometimes be wrong in both directions.

Deterministic claim-shape rules are keyword-based. `is_causal` looks for
causal verbs without hedging. A causal claim phrased unusually could pass, and
an innocent sentence using "drove" could be withheld.

---

## 6. Security boundaries

**What holds.** Two independent layers stand between a model and DuckDB: an
AST-based SQL guard, and an engine locked with `enable_external_access=false`
and `lock_configuration=true` after load. 125 adversarial tests cover both,
including tests that bypass the guard on purpose to confirm the engine refuses
on its own.

**What is not claimed.**

- **DNS-rebinding protection on `/mcp` is off unless configured.** The SDK
  enables it automatically only for a loopback bind; a container binds to
  every interface. Set `AAE_MCP_ALLOWED_HOSTS` to the deployment hostname to
  turn it on. Left empty it stays off — which is the documented default,
  because the endpoint is read-only, holds no credential, and every tool
  requires a session id the caller must already possess.
- **No authentication, no rate limiting beyond per-run budgets and a cap of
  four concurrent analyses.** A public live deployment is open by
  construction. The ceilings bound the cost of any single run, not the number
  of runs.
- **No sandboxing of the DuckDB process.** The lockdown is a DuckDB
  configuration, not an OS boundary. It is strong — the settings are
  irreversible for the connection's life — but a DuckDB vulnerability would
  not be contained by it.
- **Uploaded data lives in process memory** for the session's life. It is
  never written to disk after ingestion (the temporary file is deleted once
  the rows are loaded) and never persisted, but a memory dump of a running
  process would contain it.

---

## 7. Determinism, precisely

Deterministic: the generated warehouse (byte-identical for a seed, checked in
CI by generating twice), the analytical tasks, the tool calls per task, the
findings, and the report.

Not deterministic: the order entries appear in the global MCP trace, because
workers run concurrently and the trace records real completion order. The
reproducibility test groups the trace by task rather than asserting a global
sequence — asserting a fixed order would be asserting something the system
does not provide.

---

## 8. Scale

Sized for a demo, not a warehouse.

- Each session materialises its dataset into a private in-memory DuckDB.
  32 concurrent sessions × ~250k rows is the tested shape; the memory limit is
  1 GB per connection.
- Uploads are capped at 25 MB and 2,000,000 rows.
- Results returned to an agent are capped at 500 rows, charts at 200.
- A run is bounded to 48 MCP tool calls and 300 seconds.

There is no query result cache, no incremental computation, and no persistence
between sessions.

---

## 9. Frontend

- No client-side routing; the app is one page and the server serves the shell
  for any unknown path.
- No virtualised tables. A 500-row result in the provenance drawer renders in
  full, capped to 60 rows displayed.
- Vega is 295.7 kB gzipped, lazily loaded on first chart render. It dominates
  the bundle.
- Tested with Vitest and Testing Library. Playwright is not used — the
  environment could not download its browser — so there is no automated
  end-to-end browser test. The flows were verified manually through headless
  Chrome driven over the DevTools protocol.

---

## 10. Deliberately out of scope for v1

No authentication, billing, multi-tenant persistence, or scheduled jobs. No
arbitrary Python or notebook execution by the model. No vector database, RAG,
or web search. No warehouse OAuth connectors, BI-tool integrations, or
write-back. No forecasting or model training.

These are absent by design, not by omission.
