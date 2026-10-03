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
- **The build is audited, but not hermetic or attested.** Python constraints
  and the npm lockfile bound application dependencies; CI runs pip-audit,
  npm audit and gitleaks. The workflow actions and Docker base images still
  use mutable release tags, however, and the downloaded gitleaks binary is
  not independently checksum-verified by this repository. There is no SLSA
  provenance or reproducible-build claim. A high-assurance deployment should
  pin actions and images by digest, verify downloaded tools, and automate the
  corresponding update process.

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
- **Question interpretation for an uploaded file remains bounded, not a
  general semantic layer.** Deterministic Analytics uses rules; AI Analytics
  may propose a typed contract. The latter is not trusted: all identifiers,
  operations, filters, periods and source excerpts are checked against the
  local schema and original question. This fixes silent loss of explicit row
  restrictions, but it does not make arbitrary synonyms or business concepts
  reliably understandable.
- Everything outside that is **refused with a reason**, and the profile is
  offered instead. "Why did revenue fall?" names no operation and gets no
  answer. That is deliberate, but it means the deterministic demo answers a
  narrow band of questions about an arbitrary file, and the demo warehouse is
  where the interesting analysis lives.
- The rules are literal. A question that says "turnover" about a column
  called `revenue` is refused, because synonym matching would be guessing.
  The refusal says which column to name instead, which is the most a rule
  system can honestly offer.
- **A named time axis or period is applied only when its date semantics are
  established.** "Total revenue in
  1998" filters to 1998 and reports nothing if no row falls there. On a table
  with no date column it is refused, rather than answered over every row --
  which is what it used to do. A lifecycle date such as `signup_date` is not
  silently treated as the date of revenue or as a revenue trend axis: the
  question must name that date column explicitly. A sole generic event clock
  such as `order_date` or `transaction_date` may define the time axis or
  period without extra wording.
- **Aggregate population and effective observations are distinct.** `SUM` and
  `AVG` ignore null measures. Results therefore record matching rows with
  `row_count` and contributing non-null values with `value_count`; reports
  disclose both and warn when they differ. This does not impute missing data
  or make a recorded-value total a total of unknown values.
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

There is an acceptance corpus of 266 cases: every one of 40 domains, all 24
structural families and 19 question kinds, both CSV and Parquet. It runs in
CI with no credentials. The added restriction families cover numeric bounds,
categorical equality, nullity, compositions and contradictions across at
least three unrelated domains each.

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
The corpus is 266 questions the authors chose, against datasets the authors
generated. It demonstrates the committed cases and the *shape* of the
failure behaviour -- refusal rather than guessing -- not coverage of every
file a visitor might upload. Some question kinds are satisfied by a refusal,
and a large share of the corpus is deliberately unanswerable.

**Acceptance is about what must never happen**, not what must always
happen. Zero unsupported publications, zero irrelevant publications, zero
silent guesses, zero cross-run leaks, zero unexpected failures. A case may
answer, refuse, or complete with nothing published; the manifest records
which of those are honest for each question.

**The two modes now agree on every case.** Both passes are compared on
outcome and publication count, and the divergence is zero. It was five, and
all five had one cause: the AI path recorded a period's column in
`time_field`, where the rule path records it in `period_field` and reserves
`time_field` for a trend's axis. That changed the canonical contract without
changing the SQL, so revalidation at the MCP boundary refused the engine's
own contract and the AI pass published nothing where the deterministic pass
answered.

This is worth stating as a measured number because Compare Both puts the two
side by side and gives a reader no way to tell a planner disagreement from a
data problem. It is compared on outcome rather than on wording: the reporter
may phrase a finding differently, and that is not a divergence.

What it does not establish is that a *real* model agrees. Both passes are
driven by the scripted provider — the AI pass differs in declaring itself
remote and retaining its prompts, not in who plans. A real model's plan is
bounded by the checks in §5a and by the AI-plan rules in ARCHITECTURE §14,
and the only evidence about its actual behaviour is the paid runs recorded
in §11.

---

## 5a-i. What real data found that generated data did not

Every defect in the grouped-completeness work was found by running a real
uploaded file through the real graph, and none of them by the suite. The
corpus was broad across subject areas and narrow across the shapes files
actually take, so it agreed with the assumptions that produced it.

The blind spots, each of which hid a distinct defect:

| Assumption | What it hid |
|---|---|
| 25 or fewer groups | a breakdown cut to the top 25 and called complete |
| lowercase headers | a grouping compared literally against a lowercased alias |
| every numeric column is a measure | store numbers summed, and averaged |
| answers short enough not to be truncated | a claim cut mid-number |
| only successful run statuses | a refused run badged COMPLETE and rendered blank |
| hand-written schema dictionaries in tests | the classifier's real output never checked |

The last one is the one worth naming. Unit checks supplied a schema written
by hand, and that hand classified the grouping key correctly where
`infer_schema` did not — so seven acceptance questions passed at the unit
level while three were wrong end to end. `tests/integration/` now drives a
committed fixture with the failure-relevant shape through the actual
upload, inference, planning, SQL, verification and report path, and no test
in it supplies a schema.

**What this does not establish.** That the remaining fixtures are realistic
in ways nobody has thought to check. The lesson is about the method, not
about having finished: a generated corpus tests the generator's
assumptions, and only real files, or fixtures derived from them, test the
engine's.

---

## 5a-ii. Two cuts, and the ceiling that refuses them

The contract carries at most two groupings, plus an optional time grain.
That covers "by store", "by region and channel" and "by store for each
month", and refuses anything wider rather than dropping a cut.

The refusal is reachable on ordinary data. Store x month on 45 stores and
33 months is 1,485 groups, against a ceiling of `min(GROUP_RESULT_MAX,
max_result_rows)` = 500. The engine counts the shape before executing it
and declines with the group count, the ceiling and four ways to narrow.

**This is a limitation of the deployment, not of the question.** Raising
`max_result_rows` would answer it, at a memory cost on a 512 MB instance,
and that is a deployment decision rather than something the engine should
take for itself. What it must not do -- and what it did -- is return the
first 25 groups and call them the breakdown.

A consequence worth stating plainly: a >200-group breakdown cannot arise
from an *inferred* dimension at all, because a column with more than 200
distinct values is not classified as groupable. The partial-coverage path
is therefore exercised by the 501-group boundary test rather than by
normal inference.

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
counter in Redis can therefore overstate real spend. The application never
silently resets it, but the free Render Key Value plan loses every counter if
the datastore itself restarts. The research and analytics demos share that
store with structurally separate prefixes (`are:live-runs:*` and
`aae:ai:*`), so values cannot collide but the restart/failure domain is
shared. Compare the ledger against the provider's usage page, and rely on the
provider-project hard limit as the non-resetting external backstop.

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

The typed-AI-plan bounds and the answer-first report were measured the same
way. Reverted one at a time: the `time_field`/`period_field` fold restored;
the identifier-grouping check removed; the stated-period check removed; the
ranking-direction check removed; the sort-order coverage component removed;
the contract diff made to report nothing; the rows-counted figure taken from
the result's own row count instead of summed across groups; the direct
answer taken as the first published finding instead of the one citing the
executed contract; the answer block's print rules dropped; filters compared
in order rather than as a set. **Eleven mutations, eleven caught** — five in
the engine, five in the frontend, and the cross-mode equivalence above,
which the fold alone breaks.

Two of these were regressions caught by a real suite rather than predicted.
Making the answer its own block broke the browser tests, which select
`article.finding`; hiding the findings panel when nothing was published then
broke `waitForReport` on every refusal test, because that helper had no other
signal that a report had rendered. Neither was visible to the jsdom suite,
which never waits for a report.

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

Sized for a demo. The public deployment declared by `render.yaml` is one
Render **free** instance with 512 MB. Each session's DuckDB is limited to
160 MB and one thread; the process admits three live sessions, at most two of
them uploads, and two analyses at once. Uploads are capped at 10 MB, 400,000
rows and 200 columns. These are the current deployment settings; the Python
defaults are intentionally broader and must not be quoted as public limits.

**The admission policy is based on a bounded rehearsal, not a throughput
claim.** With two analyses running against a full session table, three live
sessions peaked at 438 MB; four reached 497 MB, within 15 MB of the instance
limit; six and eight exceeded the instance and were killed. Hence a fourth
visitor evicts the least-recently-used session instead of being admitted.
The detailed table and operational consequences live in `DEPLOYMENT.md`.

`AAE_DUCKDB_MEMORY_LIMIT` is a ceiling, not an up-front allocation. An idle
session is still not free: it holds a private in-memory database until expiry.
The measurements above do not establish a universal memory bound, leak
freedom, or throughput. They establish why the public cap is three on this
specific 512 MB plan. A paid `1c-2g` instance is documented only as the next
capacity option; it is not what the public service currently runs.

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
  for the assembled application: **96 discovered browser scenarios** covering
  the landing page, recorded and live analysis, provenance, uploads,
  refusals, session deletion, cross-session isolation, cookie flags, MCP
  policy, planning modes, reports, charts, accessibility and schema-role
  confirmation. They run against a real server, not a mocked page.
- The report leads with the answer, the population it covers, the rows it was
  counted over and the grouped result, and puts the provenance after them.
  Population rows and non-null observations used by an aggregate are separate
  facts. `row_count` records rows matching the contract; `value_count` records
  non-null measure values that contributed to `SUM` or `AVG`. Grouped coverage
  carries both across the complete population and the returned groups.
- Print rules are maintained per block, and `printStyles.test.ts` reads the
  stylesheet to enforce it. jsdom does not apply print media, so nothing else
  in the suite can see those rules, and the screen palette is tuned for a
  dark background — a block added without them renders close to white on
  white in a saved PDF.
- CI runs Chromium, Firefox and WebKit separately. Chromium executes all 96;
  Firefox and WebKit each declare one skip for Playwright's Chromium-only PDF
  API. The report guard reconciles every discovered result and rejects zero
  execution, undeclared skips, missing engines and retry-rescued flakes.
- Browser tests preflight `/api/health` and refuse to run unless
  `provider_mode` is `fake`. Their CI upload ceiling is set for the suite's
  known aggregate demand; rate-limit refusals are failures, not passes or
  silently accepted skips.

---

## 10. Deployed, and what the deployment has and has not shown

Live at <https://agentic-analytics-engine.onrender.com>. Both public modes
work there: credential-free acceptance passes **57 checks** against the
deployed service, and a single authorised Compare Both run published the
correct total on both the deterministic and the AI side for **$0.001160**.
`docs/RELEASE-EVIDENCE-v0.1.0.md` carries the figures and the capture
checksums for the v0.1.0 release, where the same script reported 55.

The two figures are the same script, not a changed one. Two of its checks
are HTTPS-only — that HSTS is set, and that it does not claim Render's
shared parent domain — so a run against a local container reports 55 and a
run against the deployed service reports 57. Neither number should be quoted
without saying which it was.

Behaviour behind a TLS-terminating proxy is now exercised rather than
assumed, which is what `AAE_SESSION_COOKIE_SECURE` existed for.

**Not shown by the deployment**, and stated here rather than left to
inference:

- **AI-off rollback has not been rehearsed against production.** Setting
  `AAE_AI_ANALYTICS_ENABLED=false` and redeploying is covered by automated
  isolation tests only. The procedure is documented in
  `docs/DEPLOYMENT.md`; it has not been performed on this service.
- **No production restart was performed**, so a Redis counter has not been
  watched surviving one. Durability is a property of the store and is
  tested locally; it has not been observed here. Separately, the free Key
  Value plan can lose every counter if the datastore itself restarts —
  see §5b.
- **Firefox and WebKit are untested against this deployment.** Chromium
  only. No claim is made about the other two.
- **One paid run.** Everything known about the hosted AI path comes from a
  single Compare Both request; it is evidence that the path works, not a
  sample of how it behaves under variety or load.
- The resource envelope has not been measured against the running instance
  under load, and cold-start latency is unquantified.

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

### What three paid Compare Both runs showed about typed AI planning

The bounds on a typed cloud plan (ARCHITECTURE §14, ADR 0002) are enforced
in code and tested against plans written by hand. Neither the corpus nor the
unit tests say anything about how a real model behaves, because both drive
the scripted provider. Three authorised runs against the deployed service on
30 Sep 2026 are the only evidence there is, and they are three samples.

One question, over a 300-row upload: *"What was the total annual revenue in
2024?"* The expected total, computed with DuckDB before any run, is
`77781.76` over 150 rows.

| Run | AI outcome | Canonical hash vs rules | Cost |
|---|---|---|---|
| 1 | confident, published `77,781.76 across 150 rows` | identical | $0.001422 |
| 2 | declared the question ambiguous; engine refused | differs — refused, so `profile` | $0.000399 |
| 3 | confident, published `77,781.76 across 150 rows` | identical | $0.001349 |

Total $0.003170. The deterministic pane published `77,782` in all three.

**What this supports.** The typed plan validated to *byte-identical canonical
contract* as the schema-grounded rules in two of three runs — same operation,
measure, period, `period_field` and `time_field`, hash
`adc71a559eaa58a9be…`. When the model declined to commit, the engine
**refused with the model's stated reason** rather than guessing or silently
falling back to the rule contract, which is the behaviour ADR 0002 chose.

**What it does not support.** That the model agrees reliably. Two of three is
two of three. The variance is real and a reader of a single Compare Both
cannot see it.

**No bound was breached in any run** — but none was *attacked* either. No run
attempted to add a period, shift one, group by an identifier or reverse a
ranking, so these runs are not evidence that the bounds hold under a model
that tries. They are evidence that an agreeing plan is accepted and a
hesitant one refuses safely.

**The disagreement was substantive, not noise.** Run 2's stated ambiguity was
that "2024" might not refer to `signup_date`, the table's only date column.
The critic raised the same point in runs 1 and 3 when withholding an
*additional* proposed finding:

> The result sums annual_revenue for the 150 rows whose signup_date falls in
> 2024. It supports the value 77781.76 for that filtered group, but does not
> establish total revenue earned during 2024 across the dataset; the filter
> is on signup date, not a revenue-period date.

That is a fair reading of a fixture whose only date column is a signup date,
and it is the distinction the rule planner does not draw — it uses the one
date column available. Recorded here because it is a limitation of the
*question-to-schema mapping*, in both modes, rather than a model failure.

---

## 11a. An ordered quantity is indistinguishable from a numeric key

`infer_schema` cannot tell a numeric dimension that measures something from
one that identifies something. `age` (48 values, 18-65) and `Store` (45
values, 1-45) receive identical classifications, down to the same reason
string, because they are structurally identical: dense integer ranges whose
values each recur across many rows.

The presentation contract therefore phrases both the same way and uses the
numeric order only to position marks on a chart axis, which is true of a
store number as much as an age. What it will not do is read an order as a
direction: an ordered breakdown states explicitly that it is descriptive
and that no trend was tested.

A rule separating the two would be a guess about column naming presented as
inference. See [ADR 0004](adr/0004-deterministic-presentation-contract.md).

**The limitation is permanent; there is now somewhere for the missing fact
to come from.** The values alone will never separate these two readings. For
a supported ambiguous numeric field on an *uploaded* dataset, the person who
uploaded it can now say which it is: the schema inspector offers "quantity"
or "category", the engine plans the query with the confirmed reading, and
the planning audit names the column, both readings and that the choice was
confirmed for that dataset session. See
[ADR 0007](adr/0007-session-scoped-role-confirmation.md).

What this does and does not change:

- The inference is **unchanged**. The engine is no better at guessing, and
  the column stays marked a close call after confirmation, because
  confirming does not make it decidable -- it records that someone decided.
- The confirmation is **user-supplied meaning, not inferred truth**. The
  engine validates that the choice is one it can act on, acts on it, and
  records whose it was. It cannot validate that the reader is right about
  their own column.
- It is **session-scoped**: held in memory on the dataset session, never
  written to disk, never shared between sessions, never inherited by a
  later upload of the same file, and gone when the session ends. It is not
  a governed metric definition.
- Only **measure and dimension** can be confirmed. `time`, `identifier`
  and `ignored` remain unsupported, because the planner would not honour
  them, and a role the engine ignores is worse than no role.
- Confirming a column as a quantity does **not** make it additive. The
  additivity guess is cleared rather than assumed, so the interface offers
  an average of it and never a total.
- The **demo warehouse and recorded runs offer no control**, because the
  choice belongs to whoever knows what the column means and that is not a
  visitor to someone else's dataset.

---

## 11b. A category filter value stops at the first space

`where chronotype is Night Owl` binds as `chronotype = 'Night'`. The
equality grammar captures a single token, so a multi-word value is
truncated, the filter matches no rows, and the run is declined by the
empty-population guard rather than answered.

The failure is safe -- no wrong number is published -- but the question is
one a reader would reasonably expect to work, and the message does not say
that the value was cut. Widening the capture is not a one-line change: the
clause runs to the end of the sentence, so `where chronotype is
Intermediate by region` would swallow the grouping, which is the same
over-capture that once turned a filtered breakdown into a two-cut one.

Quote the value or use a single-word category until this has its own
change with its own corpus evidence.

---

## 12. Deliberately out of scope

No authentication, billing, multi-tenant persistence or scheduled jobs. No
arbitrary Python or notebook execution by the model. No vector database, RAG
or web search. No warehouse OAuth connectors, BI integrations or write-back.
No forecasting or model training.

These are absent by design.

## 13. Presentation and audit boundaries

The presentation contract prevents the browser from inventing labels,
coverage, chart semantics or numerical summaries. It does not establish a
unit that the uploaded data did not declare, or distinguish an ordered
quantity from a numeric entity key without user-confirmed metadata. Such a
field remains ambiguous rather than being given a misleading visual meaning.
Where the uploader supplies that metadata (§11a), the contract uses it and
the audit attributes it; the field is still reported as a close call.

The planning audit is an intentionally bounded operational record. It exposes
the accepted governed contract, route, coverage and timings, but never model
prompts, hidden reasoning, raw provider responses, credentials or uploaded
rows. It is evidence of how a result was constrained, not a claim that a
model's reasoning has been inspected.
