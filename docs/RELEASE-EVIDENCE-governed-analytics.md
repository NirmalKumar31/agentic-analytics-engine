# Release evidence: governed analytics rebuild

What was measured, against what, and what it does not cover. Every figure
below came from a run recorded in this document's own session; none is
carried over from an earlier release.

**Deployed and verified SHA:** `1f2446d111ae828a5c5529e6f66fe6bb5d57016a`
(merge of PR #23)
**Service:** <https://agentic-analytics-engine.onrender.com>
**Captured:** 2026-10-01

> `main` has since advanced to `78f619b` (merge of PR #24, test-only: it
> makes the golden layout assertions execute rather than skip). **That
> commit is not deployed.** Nothing in it changes product behaviour, but
> the deployed SHA and the head of `main` are different and are stated
> separately throughout.

---

## 1. Credential-free hosted acceptance

Two scripts, both against the deployed service, neither spending anything.

| script | result |
|---|---|
`scripts/live_acceptance.py` | **PASS: all 57 checks** |
`scripts/hosted_acceptance_upload.py` | **PASS: all 50 checks**, 2 not run |

The two probes that did not run were refused by the deployment's own
per-IP ceiling:

- `What is the total Weekly_Revenue where Promo_Flag is 1?`
- `What is the total Weekly_Revenue where Avg_Temp_C >= 50?`

They are recorded as **not run**, which is neither a pass nor a failure.
The rate limit working is the abuse control working; counting a refused
probe as a pass would be the dishonest reading, and counting it as a
failure would be the engine being blamed for its own ceiling.

Covered by these 107 checks: health and readiness; the application shell;
all three committed recordings; a built-in warehouse session and a
deterministic analysis over it; supported findings with citations that
resolve; read-only SQL; a Secure, HttpOnly capability cookie absent from
the run payload; CSV upload and profiling; the profile marking itself
inferred; deterministic upload analyses against DuckDB oracles; inclusive
filter bounds; a grouping key not adopted as the measure; safe refusal of
an unmappable question stated once rather than repeated; session deletion
and run unreachability; the MCP endpoint withdrawn with 503; and the
security-header matrix.

## 2. One authorised paid Compare Both run

`scripts/paid_compare_acceptance.py --confirm`, against the deployed
service. **One comparison. No retry.**

**PASS: all 41 checks. Total cost 184 microdollars ($0.000184).**

The assumed ceiling was 60,000 microdollars, so the run came in three
orders of magnitude under it.

This is the only evidence here that a *real* cloud model's output survives
the engine's validation, and it is the reason the run was worth
authorising. Every automated test uses a scripted stand-in that returns a
plan shaped to pass; it proves the policy, not the planner.

What the paid run established that no fake can:

- the model's typed plan passed `mapping_from_plan`: the operation,
  measure and grouping are the ones the question named, and it invented no
  filter, no period and no ordering;
- both panes executed the **same canonical contract** over the same
  dataset fingerprint;
- both sides' averages match an independent DuckDB oracle computed before
  anything was sent;
- coverage was complete on both sides and every row was represented;
- a cited cell resolves inside each run's own results, and the two sides
  share no result id and no finding id, so there is no cross-run leakage;
- the deterministic side consumed **no** AI allowance;
- nothing was published without a supporting verdict, and each side
  published exactly one direct answer rather than the same figure twice;
- no secret, private path or provider endpoint appeared anywhere in
  56,453 bytes of captured response body.

### What it does not establish

The paid run exercises the **AI planner through Compare Both**. It does
not exercise the `auto` routing *decision*, that a question the rules
resolve exactly makes zero provider calls, and an ambiguous one makes
exactly one. That decision is deterministic logic with no provider in it,
covered by 45 tests and nine mutations, and a paid run would not add to
it. Stated here so the distinction is on the record rather than inferred.

## 3. Local and container evidence

Not hosted. Run on a development machine against a locally built
container image and a locally served build.

| gate | result |
|---|---|
Python suite | 2,078 tests collected, exit 0 |
`ruff check` / `ruff format --check` / `mypy src` / `mypy scripts` | clean |
`validate-recordings` | 3/3 |
Frontend unit | 197 passed |
`typecheck` (`tsc -b`) and production build | clean |
Chromium browser suite | 44 passed, **0 skipped** |
CI (10 jobs) on the deployed SHA | all green |

### Cross-browser

Run locally only, never in CI:

| engine | result |
|---|---|
Chromium | 44 passed, 0 skipped |
WebKit | 48 passed, 1 skipped |
Firefox | 48 passed, 1 skipped |

The single skip on WebKit and Firefox is the browser-PDF test, which uses
`page.pdf()`, a Chromium-only Playwright API. It is gated rather than
failing.

**CI runs Chromium alone.** Firefox and WebKit are therefore *locally
verified at this SHA*, not continuously verified. A regression in either
would not be caught by a push. Running all three locally is what found the
three focus defects fixed in PR #23, so the gap is real and worth closing.

### Bundle size

Measured from a clean production build:

| asset | raw | gzip |
|---|---|---|
`index.css` | 31.95 kB | 7.56 kB |
`index.js` | 304.27 kB | 93.33 kB |
`vega.js` | 860.99 kB | 295.66 kB |

Vega is **already code-split and lazy**. A network trace of the landing
page fetches only `index.css` and `index.js`; the Vega chunk is requested
only when a chart renders. Vite's "chunk larger than 500 kB" warning is
therefore cosmetic here: the large chunk is not on the critical path.
Measured before altering, and no change is warranted.

---

## 4. Not measured

Stated so that silence is not read as success.

- **Accessibility scan.** No axe or equivalent automated audit has run.
  Focus visibility, touch targets at 390px, keyboard reachability and the
  WCAG 2.5.8 inline-target exemption are asserted by browser tests; full
  WCAG 2.2 AA conformance is **not** claimed.
- **Lighthouse or any performance score.** Never run. Bundle sizes above
  are file sizes, not a performance measurement.
- **Firefox and WebKit in CI.** See above.
- **Long-task, CPU or layout-shift profiling.** Not measured.
- **Screenshot baselines.** None exist; layout is asserted by bounding
  boxes and computed style, which survive copy changes where a baseline
  would not.
- **The two rate-limited filter probes** in §1.
- **Any paid run of the `auto` route**, for the reason given in §2.

## 5. Standing limitations

These are properties of the product, not gaps in this release's testing.

- The free key-value datastore backing the cost ledger can reset its
  counters if the datastore restarts. The provider's own project limit is
  the external billing backstop; the ledger is the in-application ceiling,
  not the last line of defence.
- The upload corpus is **tested** coverage, not universal correctness. No
  claim is made about arbitrary datasets.
- Some schema ambiguities cannot be resolved from values alone.
  `infer_schema` gives `age` (48 values, 18–65) and a numeric `Store` (45
  values, 1–45) identical classifications, because they are structurally
  identical. The engine treats both conservatively and says so rather than
  inventing a distinction; see `LIMITATIONS` §11a.
- A multi-word category value is truncated at the first space by the
  filter grammar, which makes the filter match nothing and the run decline
  rather than answer wrongly. Safe but confusing; see `LIMITATIONS` §11b.
- Very large complete breakdowns refuse rather than silently dropping
  rows. Raising that ceiling is a deployment decision about memory on the
  free tier, not a correctness fix.

---

## 6. Positioning

This is a governed analytics engine with agent-assisted planning and
deterministic execution. It is not an autonomous analyst. A model
translates a question into a typed contract when the rules cannot; it
never performs arithmetic, never writes executable SQL, and never decides
whether its own answer is supported.
