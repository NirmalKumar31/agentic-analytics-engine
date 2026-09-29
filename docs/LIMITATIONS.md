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
- for an uploaded file, asks the engine to map the question onto the inferred
  schema (`aggregate_for_question`) rather than mapping it itself

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
and `lock_configuration=true` after load. 230 adversarial tests cover both,
including tests that bypass the guard on purpose. Query cancellation uses
`con.interrupt()` and was verified to stop CPU work, not merely the waiting
coroutine.

**What is not claimed.**

- **Session capability is not authentication.** Anyone holding the token is
  the session. There are no accounts, no identity and no revocation beyond
  ending the session. The isolation between two visitors is real and tested;
  the boundary is a bearer secret, and that is all it is.
- **The session cookie's `Secure` flag is configuration, not detection.** It
  is set from `AAE_SESSION_COOKIE_SECURE`, because a TLS-terminating proxy
  forwards plain HTTP and the request scheme would say `http` on an HTTPS
  site. A deployment that forgets to set it serves the capability without
  `Secure`; nothing in the application can notice.
- **Rate limits are in-process counters, not durable quotas.** A restart
  resets them and a second replica would count separately. They raise the cost
  of casual abuse. The client key comes from `X-Forwarded-For`, which is
  client-supplied and therefore spoofable.
- **The limiter's memory is bounded; its guarantee is not.** Because the key
  is attacker-chosen, so is the number of distinct keys, so the store holds
  at most 4096 clients and evicts the least recently seen. The consequence
  is stated rather than hidden: a flood of spoofed keys can evict a real
  client's record, and that client's next request then starts a fresh
  window. Bounding the store stops spoofing costing *this process* memory.
  It does not make the header trustworthy and does not make the limit a
  guarantee.
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
- CSV or Parquet only. No Excel, JSON, SQLite, archives or URL ingestion.
- **Two different sets of limits, and the public one is the tighter.** The
  code's defaults are 25 MB and 2,000,000 rows; the public Render deployment
  sets 10 MB and 400,000 rows, because it is sized for a 512 MB instance
  admitting three sessions. 200 columns in both. Anything quoting one of
  these numbers should say which deployment it means.
- **An oversized file is refused, never truncated.** Parquet is rejected from
  its metadata before any data is read; CSV is read to one row past the
  limit and rejected if that row exists. Returning the first 400,000 rows of
  a larger file as though it were the whole thing would make every number in
  the analysis wrong without anything looking wrong.
- **CSV validation is not format validation.** CSV has no magic bytes. The
  file is screened against signatures of formats it is definitely not, its
  header is bounded, and DuckDB's parser is the real arbiter. Parquet is
  genuinely validated through its metadata before any data is read.
- The inferred semantic schema is heuristic: roles come from column types,
  cardinality and, as a tiebreak, the column's name. It is marked `inferred`
  everywhere and will misclassify. Two rules exist because the obvious
  version of each got it wrong:
  - a numeric column with few distinct values reads as a dimension, *unless
    the question names it as the thing to aggregate* -- a question naming a
    column is an instruction, not something to infer around;
  - a text column named like a key (`store_code`, `account_no`) is still a
    grouping if its values repeat, and a text column with a different value
    on almost every row is an identifier however it is named, because one
    row per group is a label rather than a category.
- **Question interpretation for an uploaded file is a set of rules, not
  understanding.** `aggregate_for_question` recognises five operations --
  count, total, average, ranking, trend -- matches column names as whole
  tokens (with simple pluralisation), and reads an explicit `by <column>`
  grouping. It resolves a column the question did not name only when the
  schema offers exactly one candidate of the right role.
- Everything outside that is **refused with a reason**, and the profile is
  offered instead. "Why did revenue fall?" names no operation and gets no
  answer. That is deliberate, but it means the deterministic demo answers a
  narrow band of questions about an arbitrary file, and the demo warehouse is
  where the interesting analysis lives.
- The rules are literal. A question that says "turnover" about a column
  called `revenue` is refused, because synonym matching would be guessing.
  The refusal says which column to name instead, which is the most a rule
  system can honestly offer.
- **A named period is applied or the question is refused.** "Total revenue in
  1998" filters to 1998 and reports nothing if no row falls there. On a table
  with no date column it is refused, rather than answered over every row --
  which is what it used to do.
- **A grouping that names no column is refused**, quoting the name back.
  "Total revenue by loyalty_tier" on a table without that column used to
  return an ungrouped total, presented confidently as the answer.
- **What an AI run discloses, stated precisely**, because the short version
  overclaims. Column names, inferred roles, aggregate values **and the
  labels of a column you group by** are sent: a total by department cannot
  be reported without naming the departments, and those labels are cells of
  the file.
- What is withheld is unaggregated data: `sample_rows` is refused,
  row-returning SQL over an upload is refused rather than filtered, a
  profile's per-column minimum and maximum are withheld, and a near-unique
  column is classified as an identifier so it can never become a group key.
  That last rule is what stops a column of names travelling as labels.
- What is *not* claimed: an aggregate over a group of one row equals that
  row's value, so a sum by a low-cardinality dimension with a thin group can
  reproduce a cell. That is inherent to aggregation, not a hole in the
  redaction, and nothing in the design prevents it.
- The restriction follows the **run**, not the process. Compare Both puts a
  local run and a cloud run on one session; the cloud half withholds and the
  local half does not, and `AAE_PROVIDER_MODE` does not decide it.

---

## 5a. What the upload corpus proves, and what it does not

There is an acceptance corpus of 230 cases: every one of 40 domains, all 24
structural families, all 10 question kinds, nine domains with the full
matrix, both CSV and Parquet. It runs in CI with no credentials.

The newest family is `interval_grained_rows`, where a row describes a
*span* rather than a point -- a drill-core depth interval, a
departure-to-arrival window. It is there because the bound columns read as
ordinary numbers and dates: nothing about `from_depth_m` marks it as a
coordinate rather than a quantity, so a question asking for "total depth"
has three columns to choose between and must refuse rather than pick.
Flight legs add a shape nothing else has, where the row is an *edge* and
two columns are drawn from one vocabulary, so "by airport" names neither
`origin_airport` nor `destination_airport` and must not be answered with
whichever appears first.

**What it establishes.** Across those cases, no published finding was
unsupported or irrelevant, nothing failed unexpectedly, no measure,
dimension, unit or time field was silently guessed, and no run cited
another run's results. Every case's outcome was one the manifest allows.

Every case also runs a second time against a provider that declares itself
remote and retains its prompts, and **no prompt contained a withheld
cell**. The boundary for that count is derived from each dataset's own
cardinality rather than from the schema classifier: a column whose values
repeat is a category, and its labels are legitimately part of an aggregate
grouped by it, while a near-unique or free-text column's values are not.
Reading the classifier's verdict instead made the measurement blind to the
one leak it exists for -- reintroducing the defect reclassified those
columns as groupings and the check excused them in the same instant. ISO
dates are excluded because the engine derives period bounds from the
question, and legitimate values are subtracted by value rather than by
column so that a foreign key sharing its value space with the identifier
it references is not reported as a leak.

**What it does not establish.** That an arbitrary file gets a good answer.
The corpus is 230 questions the authors chose, against datasets the authors
generated. It demonstrates the committed cases and the *shape* of the
failure behaviour -- refusal rather than guessing -- not coverage of every
file a visitor might upload. Three of the ten question kinds are satisfied
by a refusal, and a large share of the corpus is deliberately
unanswerable.

**Acceptance is about what must never happen**, not what must always
happen. Zero unsupported publications, zero irrelevant publications, zero
silent guesses, zero cross-run leaks, zero unexpected failures. A case may
answer, refuse, or complete with nothing published; the manifest records
which of those are honest for each question.

---

## 5b. Exact versus conservative cost

Two different numbers, and the distinction is load-bearing.

**Exact settled cost** is charged from the usage the provider reported:
ordinary input, cached input, cache-write input and output tokens, each at
its own rate. This is what a normal call produces.

**Conservatively retained cost** is charged when a call's outcome is
ambiguous -- the usage could not be read, or the request left the process
and no usable answer came back. The full worst-case reservation stands: the
counted input at the dearest input rate plus the whole output allowance the
call was permitted. Nothing is refunded, because a request that was sent
may have been billed, and a spend ceiling that guesses "free" when it
cannot tell is not a ceiling.

A run reports whether its total is complete. When it is not, the figure is
a **floor built from reservations, not a measurement**, and the lifetime
counter in Redis can therefore overstate real spend. It is never silently
reset: compare it against the provider's own usage page rather than
treating it as the truth.

**Cancellation retains both money and token allowance.** A cancelled call
is not refunded and its output allowance is not returned to the run. The
request may have reached the provider and been billed, and the tokens it
was permitted may already have been generated; a ceiling that hands both
back on cancellation can be crossed by cancelling. So a run that cancels
work reports a total at or above what was really spent, and has less
allowance left than an uncancelled run would -- conservative in the
direction that cannot overspend.

**Provider enforcement is not instantaneous.** OpenAI documents that a hard
spend limit can process a small amount of extra usage while the limit state
propagates, so recorded spend can slightly exceed the configured amount.
The application ceiling is therefore set below the provider's ($4 against
$5), and contradictory ceilings are refused at startup rather than
discovered mid-run.

---

## 5b-i. Minimum and maximum are still text

The profile query emits one `UNION ALL` across every column of a table,
so its `min_value` and `max_value` must share a single type and that type
is text. `mean_value` does not have to, and no longer does -- it was cast
to text, which made a correct claim about it unverifiable, and it is now
numeric where it is produced.

The two range columns remain a gap. **A finding citing the minimum or
maximum of a numeric column is reported as numerically unsupported**,
because the verifier reads a cell's number only when the column declares
itself numeric and those columns declare VARCHAR. Coercing them would
mean trusting numeric-looking text on a column that says it is not
numeric, which is the rule that keeps an order reference like `0012345`
from being read as a quantity.

---

## 5c. How the cost and mapping invariants are checked

The invariants that money and mapping rest on are checked by **deterministic
sweep, not by a property-generation engine**. For each one the test walks a
fixed grid -- token counts either side of both context tiers, every split of
input across the three billing categories, six schema shapes against nine
questions -- and asserts the property at every point. Several thousand
combinations, reproducible exactly, no new dependency in a pinned install.

What that buys and what it does not:

* the grid covers the corners that matter (zero, one, the tier boundary and
  the token either side of it, an empty schema, a schema with no measure),
  because those were chosen deliberately rather than sampled;
* it does **not** search for a counterexample outside the grid. A defect
  reachable only at, say, 3,912 cached tokens against a seven-column schema
  would not be found here.

Each property was checked against a deliberately broken build before being
trusted: rounding reversed; the reservation priced at the cached rate
instead of the dearest; the long-context threshold moved out of reach, and
separately off by one; the settlement receipt left in place; run admission
made non-idempotent; the session quota off by one; the confidence gate
removed from `build_sql`; the period filter dropped from the `WHERE`
clause; an unresolved grouping answered instead of refused; the resolver
made to invent a column. **Eleven mutations, eleven caught.** Two of them
were caught only after the tests were strengthened: one property
had read the context threshold off the object it was meant to pin, so it
moved with the mutation, and the sweep never produced an unconfident
mapping with a real operation, leaving the `build_sql` confidence guard
unreachable and its assertion vacuous. Both are now pinned and exercised
directly. The number worth reporting is not "the properties pass" but
"a broken build fails them," and that is what was measured.

The governed-boundary suite is counted the same way and stated precisely,
because the loose version of the sentence overclaims. **All 27 tests pass
against the current code, and 26 of the 27 reproduce a defect found by
audit of 959ebd9.** The 27th, `[cache exceeds total]`, passes against
959ebd9 as well: the coherence check already rejected a cached count
larger than the input containing it. It guards the same settlement path
and is worth keeping, but it is a broader invariant rather than an
original-defect reproduction, so it is not counted as one and the suite is
not described as 27 of 27. That figure was also mismeasured once -- an
editable install leaked current code into the checkout of the old
revision and produced 25 of 27, which is why the measurement now runs
against a copy of 959ebd9 on `PYTHONPATH`.

The upload corpus carries two further injections of its own, recorded in
§5a: the near-unique-text leak, which the disclosure check must catch, and
the two mapping guards the interval-grained family was added for -- a
measure taken from the first of several candidates, and a grouping matched
by substring so that "by airport" silently becomes `origin_airport`.

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

## 8. Resource envelope and scale

Sized for a demo. The public deployment is one Render `1c-2g` instance --
1 CPU, 2 GB -- and the limits are set against that: 384 MB and one thread per
session's DuckDB, 12 sessions admitted, 6 of them uploads, 2 analyses at once.

**Those are admission limits, not a measured concurrency guarantee.** Nothing
here establishes that 12 sessions running large aggregates simultaneously
would survive; what the numbers do is stop the instance accepting work whose
resource envelopes alone exceed it. `scripts/capacity_smoke.py` checks for
breakage under a small bounded load and deliberately reports no throughput
figure.

Note that neither `384 MB x 12 sessions` nor `384 MB x 2 analyses` is the
real figure. `AAE_DUCKDB_MEMORY_LIMIT` is a ceiling DuckDB will not exceed,
not an allocation it makes up front — but an idle session is not free
either, because it has already materialised its rows into a private
in-memory database and holds them until it ends. The honest number is not
derivable from the configuration, so it is measured instead.

**What was measured.** `scripts/resource_rehearsal.py --cycles 10` against a
local process configured to the deployment's shape — 384 MB, one thread, and
per cycle 4 demo sessions plus 4 uploads of 120,000 rows, 2 concurrent
analyses, then every session deleted. All 68 checks passed. RSS after each
cycle's cleanup:

| Cycle | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| RSS (MiB) | 378 | 446 | 546 | 602 | 610 | 572 | 591 | 594 | 593 | 386 |

Baseline before the first cycle was 280 MiB.

**What those readings support, and nothing more.** RSS rose above baseline
and stayed there for most of the run; it stopped rising after roughly the
fifth cycle rather than growing without bound; it fell back to 386 MiB by
the end; and no out-of-memory kill or restart occurred — the process
reported the same `instance_id` throughout, which is how a restart would
have been visible.

**The test does not establish why memory was retained.** An allocator
holding freed pages and a slow leak can both look like this, and telling
them apart needs a longer run and a heap profiler, neither of which this
is. No claim of "no memory leak" is made or supported here. The practical
consequence either way is that a long-lived instance sits above its idle
figure, so headroom should be judged from the high-water mark — around 610
MiB in this run, against a 2 GB instance — rather than from the baseline.

This was a process on a developer machine, not a container on the target
instance. It establishes no bound, and no throughput figure was recorded.

Sessions expire on a timer rather than on the next request, and a browser
opening a second dataset retires its first. Both were previously true only
when later traffic happened to arrive.

Each session materialises its dataset into a private in-memory DuckDB.
Results returned to an agent are capped at 500 rows, charts at 200. A run is
bounded to 20–48 MCP tool calls and 120–300 seconds depending on deployment.
No query cache, no incremental computation, no persistence between sessions.
The local default is still 1 GB and two threads per session, because a
developer's machine is not the constraint the deployment is.

---

## 9. Frontend

- No client-side routing; one page.
- No virtualised tables. A 500-row result renders capped at 60 displayed rows.
- Vega is 298 kB gzipped, lazily loaded on first chart render, and dominates
  the bundle.
- Tested with Vitest and Testing Library for components, and with Playwright
  for the assembled application: 12 browser tests covering the landing page,
  a recorded run, a demo analysis, provenance, upload, refusal, session
  deletion, cross-session isolation, cookie flags and the MCP endpoint
  policy. They run against a real server, not a mock.
- CI runs those tests on Chromium only. Firefox and WebKit are run against
  the deployed URL at release time; between releases, a browser-specific
  regression in either would not be caught.
- The suite uploads several files per browser, which a deployment's per-IP
  hourly ceiling will legitimately refuse. Those tests skip with the reason
  rather than failing, so a rate-limited run reports fewer executed tests.

---

## 10. Not yet deployed

At the time of writing there is **no live URL**. `render.yaml` is committed
and complete and every check in this repository passes against a container
built from the same Dockerfile, but nothing here is evidence that the service
runs on Render. In particular these are untested until it does:

- behaviour behind a TLS-terminating proxy, which is the reason
  `AAE_SESSION_COOKIE_SECURE` exists at all
- the 384 MB / 1 thread envelope against a real 2 GB instance under load
- cold-start latency on a free-tier-adjacent plan
- Firefox and WebKit against the deployed build (they pass locally)

This section is replaced with the deployment's actual details once the site
is up and the acceptance run has passed against it.

---

## 11. Real-model evaluation, and what it has and has not shown

The deterministic benchmark measures the engine, not a model. A separate
opt-in evaluation (`make`-less: `evaluate-real-model`) drives an actual model
over seven datasets with deliberately unrelated vocabularies. It has no
answer key and no pass mark; it records what a model *did*, including which
gate withheld what.

**The qwen3:4b run is a pre-fix, incomplete diagnostic — not a model
evaluation.** It published nothing and withheld 17 of 17 findings, but the
run was contaminated by five defects it exposed, and it deadlocked before
finishing. It should not be cited as evidence about that model. What it
established is that the engine had: a thinking model exhausting its output
budget before answering, a planner whose output was silently discarded on
metric-free datasets, a worker prompt that named no tables, a verifier that
rejected findings whose numbers were correct, and a provider that could hang
indefinitely. All five are fixed.

**What the evaluation can now distinguish**, and previously could not:

- *transport failure* from *JSON failure* from *schema failure*. Watching
  only the provider call recorded "returned a dict" as success, so a model
  breaking its contract looked identical to one honouring it, and a timeout
  was counted as a format error.
- *the model planned this* from *the engine rescued it*. A fallback plan is
  good for the product and hides weak planning, so redirects and fallbacks
  are counted separately and a rescued run is never reported as a planning
  success.
- *failure* from *safe refusal*. An unanswerable question that produces no
  finding is the desired behaviour, and is flagged as such.

Runs are checkpointed per question and resumable, with a status file so a
stalled sweep is diagnosable while it runs. There is a whole-question
timeout as well as a per-call one.

---

## 12. Deliberately out of scope

No authentication, billing, multi-tenant persistence or scheduled jobs. No
arbitrary Python or notebook execution by the model. No vector database, RAG
or web search. No warehouse OAuth connectors, BI integrations or write-back.
No forecasting or model training.

These are absent by design.
