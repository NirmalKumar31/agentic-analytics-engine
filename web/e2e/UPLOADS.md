# What this suite costs the server, and why

Written for: whoever adds a browser test next.

CI runs three engines against one container. That container allows **24 live
upload sessions**, **200 uploads per IP per hour** and **200 analyses per IP
per hour**, and the three engines share all three ceilings because they run
from one address inside one hour.

All three were reached on PR #46's first push, and nothing in the suite could
say by how much: the figures had to be reconstructed afterwards from
rejection messages in a CI log.

Every number below comes from one strict three-engine run against a server
started by `scripts/serve-e2e.sh` with CI's exact ceilings. **Nothing here is
a prediction.** Source comments carry no totals at all, for the same reason:
a number in a comment is a number nothing re-measures.

## The two rules

> A new **upload** is justified only by a dataset *shape* no existing fixture
> can express. A viewport is not a shape. A theme is not a shape. A terminal
> state is not a shape. A filename is certainly not a shape.

> A new **admission** -- a `POST /api/analyses` or `POST /api/comparisons`
> the container answers -- is justified only by a *behaviour of the engine*
> no existing result demonstrates. Rendering the same result at a different
> width, in a different theme, in print, or in a different terminal state is
> iteration over one result, and `reportFor()` serves it from a capture.

## How the cost is counted

At the HTTP boundary, and nowhere else.

`watchTraffic` (in `e2e/helpers.ts`) is attached to every page the suite
opens. It gives each billed `POST` a sequence number on its way out and
writes exactly one response record on its way back, carrying the status and
the **source**: a response bearing the suite's own
`x-aae-e2e-analysis-source` header was answered by this suite, and one
without it reached the container. The application has no code that writes
that header, so the classification cannot be faked from inside a test.

This replaced an earlier scheme that asked a client-side marker whether the
current page had a fixture route installed. `terminalStates.spec.ts` removed
its route by pattern rather than through the `release()` it was handed, so
the route went away and the marker did not -- and every later real analysis
in that worker was recorded as a free replay. Two were:
`visualReview.spec.ts`'s "a real report holds at every approved width" and
`timeline.spec.ts`'s refused run. **The job was over its analysis budget and
the guard reported it passing**, because the suite was lying to its own
accounting. Nothing in the present scheme consults test intent, cleanup
state, or which helper a test called:

- a test that clicks "Run analysis" instead of calling `ask()` is counted;
- a request issued through an API context or `route.fetch` is invisible to
  the page recorder, so `scripts/check-e2e-sources.mjs` **refuses** one
  statically unless it goes through `ledgerHarnessRequest`;
- a spec that imports `test` from `@playwright/test` rather than
  `./fixtures` has no recorder on its pages, so that is refused too. Six
  specs did, which is where a third of the suite's real cost was hiding.

### What a replay has to be

Every replayed response names the payload behind it, and the guard fails if
that payload was never registered. A capture is registered only when it came
from a real server response, and only after it has been checked for every
field `RunPayload` declares non-optional plus the provenance and
terminal-classification fields the redesign asserts on -- so a payload
captured mid-run cannot be replayed for the rest of the job as a finished
result. Committed fixtures (the six terminal states) are registered as
`committed` rather than `capture`: they are evidence about *rendering*, and
the accounting keeps them apart from evidence about a run.

Captures are keyed by **dataset and question**, not by question alone.
`golden.spec.ts` asks the demo warehouse what `chart.spec.ts` asks a 200-row
upload; a question-keyed cache replayed one dataset's result onto the
other's session, and the test asserted against a report for a file it never
opened. The same defect existed in the Compare cache, where "Compare over an
uploaded dataset" was being answered with the warehouse's comparison.

## The shared sessions

Each is a worker-scoped fixture in `e2e/fixtures.ts`, uploaded once per
engine. `freshComposer(page)` returns one to the composer between tests
through the product's own "Start over" control, and resets the strategy, the
theme and any emulated media with it.

| fixture | shape | used by |
|---|---|---|
| `profiled` | the ordinary 200-row sample | accessibility, app, chart, evidence, golden, motion, print, terminalStates, timeline, visualReview |
| `wide` | 36 categories over 400 rows — a long table and a crowded axis | chart, report |
| `twoSeries` | two dimensions, so the chart carries a colour encoding | report |
| `twoClocks` | two date columns, so the composer must ask which is the clock | composer |
| `closeCall` | a genuinely ambiguous column | *available; roleConfirmation does not use it — see below* |
| `plain` | no ambiguity at all, as the control | *available* |
| `ambiguous` | a column that reads as measure and as dimension | *available* |
| `comparable` | a Compare-capable session, with `/api/config` routed before mount | compare |
| `demo` | the committed demo warehouse, which holds no upload session at all | chart, dualmode, foundation, golden, informationArchitecture, timeline |

### A test may not close the session it borrowed

A worker-scoped page is shared by every test in the file. `page.close()` is
the ordinary line to write against a test-scoped page, and against a shared
one it destroys the resource for every test after it — which then fail with
"Target page, context or browser has been closed", reported in the *victim*,
naming a locator that is plainly present in the screenshot. The culprit
passes.

So the fixture refuses `page.close()` and `context.close()` at the line that
attempts them, with a message that says what to do instead, and keeps the
real ones for its own teardown. `e2e/sharedSession.spec.ts` is the regression
test: remove the refusal and it fails.

The same applies to state. **A test gives the session back the way it found
it.** A route installed on a shared page outlives the test that installed it;
so does a run left in flight — and while a run is in flight there is no
composer in the document at all, so the next test's `fill()` waits for a
locator that will never resolve, until the *test* timeout. That is a
two-minute hang reported as a closed page. `accessibility.spec.ts`'s "a run
in flight" therefore unroutes in a `finally` and waits for its own run to
settle, and asserts the missing composer so the mechanism stays written down.

## The uploads, and the exceptions

Nineteen per engine, measured. Four are worker fixtures, attributed to
whichever spec first asked for one -- `profiled` to `accessibility`,
`twoClocks` to `composer`, the multi-series shape to `report`, and
`comparable` to `compare`. The `demo` fixture uploads nothing at all: the
warehouse is committed data the server already holds. Three declared
fixtures (`closeCall`, `plain`, `ambiguous`) are never instantiated, and so
cost nothing.

The remaining fifteen belong to specs that genuinely cannot share, each for
a stated reason:

**`roleConfirmation` — 8 uploads.** Confirming a schema role is a `PATCH`
that advances the session's revision, and the flow is one-way: a column that
has been settled cannot be un-settled through the UI. Each scenario needs a
dataset whose roles are still open, so each takes its own. Three are
`beforeAll` sessions shared by a serial describe; five are per-test, because
those tests mutate the revision from outside the page to model a second tab.
Every one of them closes in an `afterEach` or `afterAll`.

**`informationArchitecture` — 3 uploads.** Three serial describes over three
different shapes (`ambiguous`, `plain`, and `plain` again driven to a
terminal state). The third could share the second's shape but not its state,
which has already been driven somewhere specific. All three close in
`afterAll`.

**`app` — 2 uploads.** One test *ends* a session and asserts the dataset
becomes unreachable — it cannot share one it is going to destroy. The other
opens two browser contexts to prove one cannot reach the other's dataset,
which is the whole point of the test. Both close what they open.

**`chart` — 2 uploads.** One is the Compare-lanes dataset, which needs
`/api/config` answered with AI advertised *before the app mounts*, so it
cannot reuse a session created without that route. The other is a
high-cardinality shape the sample cannot express.

Specs that upload nothing: `compareSetup`, `dualmode`, `evidence`,
`foundation`, `golden`, `landing`, `motion`, `print`, `sharedSession`,
`terminalStates`, `timeline` and `visualReview`. Between them they render
every terminal state, the whole viewport and theme matrix, the print
appendix, five PDFs and forty review screenshots -- on sessions and
payloads other specs already paid for.

## The admissions, and why each one is real

Measured per engine. Every entry is a behaviour the container has to
perform; everything else in the suite renders a payload one of these
produced.

| what is admitted | count | why it cannot be replayed |
|---|---|---|
| a completed report, on an upload | 1 | the capture every rendering of a report replays |
| a refusal, on an upload | 1 | the engine's own classification; also the capture for the dark refusal scan |
| a run *in flight* | 1 | the only state that exists mid-interaction |
| a completed run that published nothing | 1 | `no_findings` is a distinct classification |
| a mappable and an unmappable question, on an isolated session | 2 | `app.spec.ts`'s contract: what the engine accepts and what it refuses |
| the demo warehouse, live, from the landing | 1 | the in-flight timeline of a run nobody has performed yet |
| the demo warehouse through the in-process MCP transport | 1 | a replay would prove nothing about the transport |
| the demo warehouse, once, as the shared capture | 1 | replayed afterwards by five specs |
| a trend | 1 | a distinct chart specification (line, not bar) |
| a crowded categorical axis | 1 | a distinct chart specification |
| a two-group and a forty-five-group chart | 2 | distinct cardinalities, which is what the width claim is about |
| a multi-series chart | 1 | a distinct chart specification (colour encoding) |
| a comparison over the warehouse | 1 | Compare execution semantics |
| a comparison over an uploaded dataset | 1 | a different dataset, which is what that describe is named for |
| a comparison over the Compare-lanes upload | 1 | ditto, and it asserts the lanes |
| a refused run, on an upload, for the stopped stage | 1 | the timeline's stopped marker comes from a real stop |
| three terminal states the engine produces itself | 3 | refusal, filtered-to-empty and answered are engine outcomes |
| one payload captured for the committed terminal states | 1 | the base every committed fixture overrides |
| two schema-role confirmations | 2 | a `PATCH` that advances the session revision, one-way |

## Handing sessions back

A closed browser context does **not** free a server-side upload session. The
server holds it until the capability deletes it or the TTL expires, and the
TTL outlives a CI run — so a suite that merely closes pages leaves one
session per upload behind, and three engines leave three times as many.

`endSession(page)` presses "End session", which is
`DELETE /api/datasets/{id}` with the capability cookie. No test-only
endpoint. Every fixture teardown, every `afterAll`, and every spec that
uploads inside a test body calls it.

## The ledger, and the two scopes

`e2e/helpers.ts` appends one line per event to
`web/playwright-ledger/<engine>.jsonl`: an `upload` attempt, the `open` the
server answered it with and the `close` that returned it (both by session
id), a `request` for every billed POST and the one `response` that matched
it, a `capture` or `fixture` registration for every payload the suite serves
back, and a `refused` for every 429. Every record carries a durable id that
includes the worker's pid, because a worker restart begins its own counter
and a repeated id makes duplicate detection either useless or wrong. That directory is outside
`test-results/` on purpose: Playwright clears its output directory at the
start of every run, and the three engines are three runs against one
container, so a ledger kept there could only ever report the last engine's
numbers — the opposite of what a shared ceiling needs.

Every record carries the **job id** (`AAE_E2E_JOB_ID`; in CI, the run id and
the attempt). The ledger deliberately outlives a Playwright invocation, and
the price of that is that a file left by an earlier job is indistinguishable
from this job's unless each line says who wrote it. The guard refuses a
ledger that mixes two jobs, or one that belongs to a job other than the one
it was asked about. The workflow clears the ledger **once**, before the first
engine, and never between them.

The guard runs twice over, with different questions:

```
node scripts/check-resource-budget.mjs --scope engine   # after each engine
node scripts/check-resource-budget.mjs --scope job      # after all three
```

`engine` scope is everything one invocation can know by itself — a 429 it was
served, a session it did not hand back, its own totals — and `scripts/e2e.mjs`
runs it after every engine so a leak is named before the next two engines
spend twenty minutes on top of it. `job` scope adds the totals against the
shared ceilings and the roll-call of engines that were expected to write;
neither can be judged until the last engine has finished.

It fails on: any 429; a session left open, closed twice, or closed without
having been opened; a billed request with no response or with two; a
duplicate or id-less record; a response from an unrecognised source; a replay
naming a payload nobody registered; an orchestrated response standing for
nothing; per-engine or job totals over budget; an engine with uploads and no
admissions at all; per-test sums that disagree with the engine's; an expected
engine missing; an event attributable to no engine or no job; an unreadable
line; or no ledger at all. `scripts/budgetGuard.check.mjs` drives every one
of those with a synthetic ledger, and `scripts/sourceGuard.check.mjs` drives
the static rules.

## Retries

`retries: 0`, deliberately, and `scripts/check-e2e-sources.mjs` fails the
build if that changes without the resource model changing with it.

The report guard refuses a flaky test outright, so a retry can never turn a
job green — it can only spend the budget a second time. Worst case today is
therefore exactly one execution per test, which is what the budget above
measures. If retries were ever enabled, each retried test would re-upload
its dataset and re-issue its admissions, and the guard would only say so
after the fact; that is why the static check exists rather than a comment
asking politely.

## Checking it

```
cd web
scripts/serve-e2e.sh 8125                   # from the repository root
npm run check:guards                        # the guards' own self-tests
export AAE_E2E_JOB_ID=local-$(date +%s)     # once, outside the loop
rm -rf playwright-ledger                    # once, outside the loop
AAE_E2E_BASE_URL=<url> npm run test:e2e     # Playwright and both guards
AAE_E2E_LEDGER_ENGINES=chromium,firefox,webkit \
  node scripts/check-resource-budget.mjs --scope job
```

`scripts/serve-e2e.sh` starts a server with CI's ceilings, including
`AAE_MAX_ACTIVE_UPLOAD_SESSIONS=24`. A relaxed server is fine for exploring
one spec; it is not evidence, and a run against one cannot exhaust anything.
