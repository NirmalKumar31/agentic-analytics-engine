# Handoff: the five-PR governed-analytics rebuild

Written for the next agent picking this up mid-sequence. Working notes,
not product documentation — delete when PR E lands.

Date: 2026-10-01 · Repo: `NirmalKumar31/agentic-analytics-engine`

---

## 0. Thirty-second orientation

The engine computes correctly and communicates badly. Five PRs fix the
communication without touching the arithmetic.

| PR | Branch | State |
|---|---|---|
A — presentation contract | merged | `5f2044c`, 10/10 CI |
B — automatic governed planning | merged | `8a152dc`, 10/10 CI |
**C — visual foundation** | `design/precision-atlas-foundation` @ `93e43bd` | **pushed, no PR, incomplete, one blocking defect (§5)** |
D — report/table/chart | — | not started |
E — audit, polish, release | — | not started |

`origin/main` = `8a152dc`. No open PRs. Worktree clean.

**Totals so far:** 81 files, +14,102/−1,669. 150 new Python tests, 23 new
frontend unit tests, 9 new browser tests, 28 mutations injected and 28
caught.

**Read first:** `docs/adr/0004`, `docs/adr/0005`. They carry the reasoning;
this file carries the state.

---

## 1. PR A — deterministic presentation contract (merged)

### Problem

The frontend built reports by arranging `finding.text`. That made the
browser decide five things it had no way to know, and the engine knew all
five:

| the browser guessed | what shipped |
|---|---|
which number is the answer | `Total Weekly Sales by Store: 1: 222,402,808.85; 2: …; and further groups in the cited result.` |
what a value means | `blue_light_filter_active` printed as `0` and `1` |
whether an axis is ordered | 48 ages plotted as unordered labels |
whether a breakdown is complete | a top-25 of 45 stores, covering 3,575 of 6,435 rows, read as complete |
what a unit is | nothing separated a percentage from a ratio |

### Files

```
src/agentic_analytics/presentation/
  schemas.py        331  typed models, closed enums, strict Pydantic
  summarize.py      624  shape detection + prose templates + numbers_resolve
  build.py          394  build_presentation(), display_fields_for(), caveats
  fields.py         208  boolean/percentage/currency/ordering gates
  charts.py          83  restates analytics.charts decisions, names the axes
  compatibility.py  184  derives a limited presentation from an old recording
  __init__.py        50  public surface
```

Touched: `graph/runner.py` (+47, the `presentation` field and
`_presentation_for`), `verification/canonical.py` (+8, `_format` promoted
to public `format_number`).

### The API

```python
build_presentation(
    mapping,                 # accepted QueryContract (object or dict view)
    snapshot,                # verified ResultSnapshot, or None
    findings=[],             # list[PublishedFinding]
    question_coverage=None,
    chart_decision={},       # the dict from analytics.charts.chart_for
    schema=None,             # InferredSchema OR its .as_dict() — both work
    planner_fallback=False,
    outcome="completed",
    stopped_reason="",
) -> AnalysisPresentation
```

`AnalysisPresentation` fields: `schema_version`, `shape`, `headline`,
`secondary_summary`, `highlights[]`, `scope`, `display_fields[]`, `table`,
`chart`, `caveats[]`, `provenance_refs[]`, `compatibility_derived`.

`PresentationShape`: `scalar`, `boolean_comparison`,
`categorical_breakdown`, `ordered_numeric_series`, `time_series`,
`ranking`, `multi_dimensional`, `statistical_test`, `refusal`, `failure`.

`DisplayField`: `source_name`, `display_label`, `semantic_kind`, `unit`,
`precision`, `boolean_labels`, `ordered`, `identifier`, `sensitive`.

Exposed on the wire as `run.presentation` (optional; `null` on failure to
build). `PRESENTATION_SCHEMA_VERSION` is `"1.0"`.

### Four invariants — do not weaken any of them

1. **No figure it cannot point at.** Every `PresentationHighlight` requires
   ≥1 `EvidenceCell` (`Field(min_length=1)`). Prose is *checked* by
   `numbers_resolve(text, snapshot, scope, derived=[])`, which returns the
   numbers that resolve to nothing. Three sources count: a numeric cell of
   the cited result (or its `row_count`), a recorded coverage count, or a
   declared difference recomputed from two cells.
   *Why:* the failure mode was never invented numbers. It was **real cells
   in a false sentence** — calling the tenth row of a top-ten list "the
   lowest".
2. **Never claims more than the analysis supports.** `"highest"` only when
   `group_coverage.complete`. Two-group deltas are "descriptive". Ordered
   dimensions state no trend was tested.
3. **Display metadata needs converging evidence.** Boolean requires *every
   value present* ∈ {0,1} **or** a declared boolean type — not two distinct
   values, which would relabel a two-store table "Off"/"On". Percentage
   requires a name token **and** a 0–100 observed range (so `rate` holding
   `0.03` is not `3%`). Currency is **never** inferred from a name.
4. **Additive.** `report`, `findings`, `rejected`, `charts`, `results`,
   `query_contract` unchanged. A build failure logs
   `presentation_build_failed` and returns `None` rather than failing a run
   whose numbers are already verified.

The schema also rejects at the boundary: >1 semicolon in a headline, or
the substring "and further group"/"and further period".

### Output, on the committed fixtures through the real pipeline

| shape | published |
|---|---|
boolean | Average total sleep hours is **6.33** when Blue light filter active is **On**, and **6.21** when it is **Off**. / *A descriptive difference of 0.12. No significance test was requested.* |
3 categories | …highest for **Morning Lark**, at 7.38, and lowest for **Night Owl**, at 5.01. |
48 ages | …highest for **Age 54**, at **22.45%**, and lowest for **Age 64**, at 21.18%. |
45 stores | …highest for **Store 20**, at **301,397,792.46**, and lowest for **Store 33**, at 37,160,221.96. |
33 periods | …peaked at 275,501,492.85 in 2010-10 and was lowest at 202,149,490.05 in 2010-09. |
ranking | **Store 20** has the highest total weekly revenue, at 301,397,792.46. |
scalar | Total weekly revenue is 7,712,747,351.84. / *Measured across 6,435 matching records.* |

### Fixtures

`tests/fixtures/sleep_study.py` — 8,500 rows. Columns
`participant_id, age, chronotype, blue_light_filter_active,
total_sleep_hours, deep_sleep_pct`.

The targets are **not independent**: flag totals imply a grand total of
53,266.12, chronotype totals imply 53,264.45, because each reported figure
is itself a rounded mean. So the fixture targets the *rounded* values and
**solves** for per-chronotype bases and flag offsets by fixed-point
iteration, with zero-sum jitter for spread. Verified against DuckDB: flag
0 → 6.21/n=4,524, flag 1 → 6.33/n=3,976; Intermediate 6.44/3,880, Morning
Lark 7.38/2,165, Night Owl 5.01/2,455; 48 age groups, max age 54 = 22.45,
min age 64 = 21.18.

`tests/fixtures/retail_monthly.py` — 6,435 rows, 45 stores, 143 weeks, 33
monthly periods. Store 20 = 301,397,792.46, Store 33 = 37,160,221.96,
**exact to the cent** (the last week absorbs the rounding residue). A
fixture whose oracle only nearly matches cannot distinguish a real
regression from its own noise.

Distinct from `retail_weekly.py` (26 weeks), which the shape-refusal tests
use. Keep both.

### Tests

`tests/unit/test_presentation_contract.py` — 57.
`tests/integration/test_presentation_acceptance.py` — 12.

The integration tests assert on **`result.presentation`**, the run's own,
not a rebuilt one. That matters: see §6.

### Mutations: 12/12

raw 0/1 · any two-valued column boolean · false complete coverage ·
percentage from name alone · unsupported significance · semicolon
enumeration · global minimum from top-N · highlights without evidence ·
prose figure not held by result · serialised profile discarded · ordered
numeric treated as nominal · chart shipping its own rows.

### Not yet consumed

**The UI still renders from `finding.text`.** Wiring it is PR D. That
staging was deliberate: ship the contract, test it, then build the
renderer against a stable schema.

---

## 2. PR B — automatic governed planning (merged)

### Problem

The resolver reported every unresolvable question as one
`confident=False` boolean. Only two policies were therefore possible:
*never ask a model* (refuses questions a model could read correctly) or
*always ask one* (spends a billable request on every question whose column
does not exist).

The resolver already knew the difference. `_pick` has always said either
"the table has no column that looks like a measure" or "the question does
not name which measure to use, and the table has 3 to choose from".

### Files

```
src/agentic_analytics/analytics/resolution.py   259  NEW — states, issues, assess()
src/agentic_analytics/agents/analyst.py        +115  resolve_upload_query_automatically
src/agentic_analytics/analytics/upload_plan.py  +97  issue codes at 11 refusal sites
src/agentic_analytics/api/app.py                +63  the lazy planner factory
src/agentic_analytics/api/modes.py              +29  RunMode.AUTO
src/agentic_analytics/graph/build.py            +44  routing branch + event
src/agentic_analytics/graph/runner.py           +21  open_planner threading
src/agentic_analytics/analytics/row_filters.py  +19  the dropped-restriction fix
```

### The routing truth table

`ResolutionState`: `exact`, `ambiguous`, `unresolved`, `unsupported`,
`unsafe`.

`ResolutionIssue` → state, and whether a planner is consulted:

| issue | state | planner |
|---|---|---|
`competing_measure_candidates` | ambiguous | **yes** |
`competing_dimension_candidates` | ambiguous | **yes** |
`ambiguous_column_role` | ambiguous | **yes** |
`missing_filter_binding` | ambiguous | **yes** |
`unresolved_measure` | unresolved | no |
`unresolved_dimension` | unresolved | no |
`missing_period_field` | unresolved | no |
`unsupported_operation` | unsupported | no |
`incomplete_question_coverage` | unsupported | no |
`role_collision` | unsafe | no |
`privacy_restricted_grouping` | unsafe | no |
`result_shape_too_large` | unsafe | no |

`state_for(issues)` takes the **strongest** state present. Ambiguity is
the weakest claim: a question that is also unresolved refuses whatever the
wording turns out to mean, so no request is spent.

`ai_eligible` is true only when state is `ambiguous` **and** every issue is
in `AI_ELIGIBLE_ISSUES`.

### The cost argument — the thing most likely to be broken by a refactor

`open_governed_cloud_provider()` takes the **durable ledger slot at
construction**. So *construction*, not the request, is when quota is spent.

Therefore `RunContext.open_planner` is a **factory**
(`Callable[[], Awaitable[LLMProvider]]`), called only once ambiguity is
established. Measured:

```
exact question            → 0 planners constructed
column doesn't exist      → 0
unimplemented operation   → 0
genuinely ambiguous       → 1
```

The tests count **constructions**, not calls, for exactly this reason.

**Do not "simplify" this into passing a provider.** It would charge quota
against questions that never consult a model, and read the credential on a
path that does not need it. It would also break the property that makes
`auto` safe as a default: *a deployment with a missing or broken credential
still answers everything the rules can resolve.*

`provider_kind(RunMode.AUTO)` returns `"governed"`, not `"cloud"` — the
run record is created before the question is resolved, and naming a
billable path for a run that may make no request is a false cost record.

### Entry points

```python
# analytics/resolution.py — synchronous, no I/O, no ledger, no provider.
assess(question, schema) -> ResolutionAssessment
#   .state .issues .candidates .reasons .safe_reformulations
#   .ai_eligible .executable .deterministic_contract .requirements
#   .as_dict()  → for events; carries no prompt, no raw cells

# agents/analyst.py
await resolve_upload_query_automatically(
    question, schema, open_planner=factory_or_None
) -> AutoResolution(mapping, assessment, route, planning_calls)
```

Routes: `rules_exact`, `ai_resolved`, `ai_unavailable`, `refused`.
`ai_unavailable` is a **refusal with a reason**, not a failed run.

The `contract_resolved` event now carries `planner`, `model_calls`,
`route`, `resolution_state`, `issues`, `ai_eligible`, `contract_hash`,
`duration_ms`, `fallback`. A test pins that key set, so nothing leaks.

`QuestionMapping.issues` is diagnostics, **excluded from
`canonical_dict()`** — two planners reaching the same contract must hash
identically however much they struggled.

### Unchanged

Model authority. A plan still cannot alter a named operation or measure,
add or shift a period, reverse a ranking, group by an identifier, or
supply SQL — `mapping_from_plan` enforces all of it and `auto` routes
**through** it. `deterministic`, `ai`, `compare` behave exactly as before;
passing no factory produces the old path.

### Tests

`tests/unit/test_automatic_routing.py` — 36.
`tests/integration/test_automatic_routing_acceptance.py` — 9.

One test reads `assess`'s **AST** (not its source text) to assert it is
synchronous and references no provider/ledger/client. A substring scan
matched the docstring's own explanation.

### Mutations: 9/9

confirm an exact contract anyway · ask about a missing column · treat every
issue as AI-eligible · ambiguity outranking a missing column ·
AI-unavailable reported as executable · unbindable restriction dropped
again · missing column reported as merely ambiguous · refusals carrying no
issue · refused route returning a usable contract.

### Not yet consumed

`capabilities.modes` now has **three** entries (`auto`, `deterministic`,
`ai`). `ModeSelector` builds a `Map` and looks up by key, so the new mode
is silently ignored. Offering it is PR C item 5.

---

## 3. PR C — visual foundation (in progress)

Branch `design/precision-atlas-foundation`, commits `3444f47` (the work)
and `93e43bd` (this file).

### Done

**`web/src/styles/tokens.css`** (322 lines) — the design system.

- Surfaces: `--surface-canvas/paper/raised/inset`. Four levels; a fifth
  would be a card inside a card inside a card.
- Ink: `--ink-primary/secondary/muted/inverse`.
- Rules: `--rule-hairline`, `--rule-strong`. They divide; they do not
  enclose.
- Meaning, not colour: `--signal` (the engine speaking), `--action` (the
  one thing to press), `--supported`, `--warning`, `--failure`, each with a
  `-weak` tint and an `-rgb` triple.
- Type: `--font-sans`/`--font-mono` resolved from system stacks (no runtime
  font request), a modular `--text-*` scale, `--numeric: tabular-nums`.
- Space: strict 4px `--space-1..8`.
- Motion budgets: `--motion-feedback` 100ms, `--motion-control` 180ms,
  `--motion-panel` 280ms, `--motion-sequence` 480ms, `--motion-ambient`
  48s. `--ease-out`, `--ease-in-out`. No spring, no bounce.
- `--z-base/raised/sticky/drawer/overlay`.
- `--measure-prose: 68ch`, `--measure-content`, `--measure-wide`.
- Dark mode in both `@media (prefers-color-scheme: dark)` guarded by
  `:root:not([data-theme="light"])` **and** `:root[data-theme="dark"]`.
- `@media (prefers-reduced-motion: reduce)` collapses every duration to
  1ms and `--motion-ambient` to 0s.

**The bridge.** At the bottom of `tokens.css`, the old 32 variable names
are redefined in terms of the new tokens. This is why the palette changes
everywhere without touching **289** `var()` call sites. Rewriting all 289
in the same change that replaces the palette would make the diff
unreviewable and the regressions invisible — a missed one simply inherits
and looks plausible.

Two mappings are judgements, both about `--accent`, which the old palette
used for two different meanings:
- `--accent` → `--signal` (most call sites are flow strokes, badges,
  selected borders, mode indicators)
- `--glow` → a **solid 2px ring** in `--action` (it was a translucent
  blurred halo, invisible on light surfaces and meaningless without hue
  perception)
- `--statistical` and `--interpretation` both → `--signal`; two extra hues
  implied a distinction the reader was never told about.

**Shrink the bridge as components are rewritten. Delete it when empty.**

**Decoration removed**, measured:

| | before | now |
|---|---|---|
`@keyframes` | 17 | 11 |
`animation:` | 27 | 20 |
`box-shadow` | 17 | 9 |
gradients | 8 | 3 |
`var(--glow)` | 6 | **0** |
hover lifts | present | **0** |

Gone: universal panel lift, sweep across busy panels, ripple under every
button, glow on the active step, self-drawing brand mark, skeleton
shimmer, pulsing flow nodes, live beacon, and the staggered panel rise
that delayed the answer behind four panels of easing.

What remains is bound to state: `reading` (a 2px rule on a panel actually
running), `waiting` (skeleton), `arrive` (a chart, once), `grid-drift`
(idle only), `fade`/`slide` (drawer), `flow-dash` (an edge actually
running).

**The ambient grid** is now `body::before` keyed on phase: drifts in
`choose_dataset`/`ready_to_ask`; `animation: none; opacity: 0.12` in
`presenting`/`completed`/`refused`/`failed`; stopped by
`body[data-hidden="true"]`; and `animation: none !important` under 600px.

**`web/src/lib/phase.ts`** (153 lines) — 12 phases: `booting`,
`choose_dataset`, `profiling`, `ready_to_ask`, `routing`, `executing`,
`verifying`, `presenting`, `completed`, `refused`, `failed`, `expired`.

```ts
phaseOf({ config, configError, session, replay, runId, run, busy, error, events }) -> Phase
showsReport(phase) -> boolean
isIdle(phase) -> boolean
```

Derived from the 15 pieces of state `App` already holds, **not** stored as
a 16th. A phase reading `completed` beside a payload reading `refused`
would render the wrong thing confidently.

`App.tsx` publishes it to `document.body.dataset.phase` in an effect, and
mirrors `document.hidden` to `data-hidden`. **The stylesheet depends on
both.** `RunPayload.outcome?: string` was added to `lib/types.ts`.

> `App.tsx` shows 658 changed lines in the diff. The logical change is
> ~60 lines; the rest is Prettier reformatting the file. Review with
> `git diff -w` if that is noise.

**Tests.** `web/src/test/phase.test.ts` (23),
`web/e2e/foundation.spec.ts` (9 — phase reaches the body, phase advances,
refusal is a distinct phase, visibility mirrored, grid holds still under a
report, nothing moves under the pointer, no coloured halo, focus is a
solid ≥2px ring, no external font/script request).

**Mutations: 7/7.** C1 stages read from the finished payload · C2 phase
regresses on a trailing event · C3 refusal read as completed · C4 hover
lift + halo restored · C5 grid never recedes · C6 phase not published to
the body · C7 focus ring removed.

### Remaining work, concretely

1. **Split `web/src/styles.css`** (2,044 lines) into `reset.css`,
   `shell.css`, `components.css`, `report.css`, `motion.css`, `print.css`
   under `web/src/styles/`, imported from `main.tsx` in that order after
   `tokens.css`. The ~20 existing `/* ---- section */` comments are the
   split points: shell, steps, layout, controls, dataset, flow, activity,
   findings, table, drawer, rail, misc, execution mode + roles, mode
   selection, comparison, print, instrument, state. Mechanical; verify
   with `npm run build` plus the full browser suite. The spec permits
   skipping this if it cannot be done without accidental behaviour change.
2. **Extract components from `App.tsx`** (729 lines): `AppShell`,
   `ProductHeader`, `DatasetIdentity`, `WorkflowIndex`,
   `DatasetOnboarding`, `SchemaInspector`, `QuestionComposer`,
   `PlanningMethodDisclosure`, `RunProgress`, `ReportWorkspace`,
   `TechnicalInspector`, `TerminalState`, `SessionControls`. No React
   Router unless multiple URLs are genuinely needed.
3. **Remove the permanent right rail** after completion
   (`web/src/components/RightRail.tsx`, 186 lines). Technical metrics must
   not compete with the answer. Collapse it into the technical inspector
   drawer.
4. **Onboarding** — one accurate sentence ("Ask governed analytical
   questions of a tabular dataset. Every published number links to the
   result that produced it."), built-in example, CSV upload, Parquet
   upload, concise privacy disclosure. No marketing hero, no statistics
   before a dataset exists.
5. **Offer `auto` in the UI.** Default label "Governed Analysis".
   Deterministic / AI-assisted / Compare move behind an advanced
   "Planning audit" disclosure. Do not present three implementation modes
   as equal primary product choices. `capabilities.modes[].mode === 'auto'`
   is already served, with `available = live_analytics_enabled`.
6. **Accessibility** — WCAG 2.2 AA contrast, keyboard-complete, visible
   focus (done), landmarks, heading order, colour never the sole
   indicator, focus trap and restore for the drawer, Escape to close,
   200% zoom, 44px touch targets, no horizontal overflow at 360 / 390 /
   768 / 1024 / 1440 / 1920.
7. **ADR 0006** for the visual foundation, plus a `docs/ARCHITECTURE.md`
   section.

---

## 4. PR D and PR E — not started

### PR D — report, table and chart (`design/report-and-chart-workspace`)

This is where the owner's original complaints actually get fixed on
screen. Branch from PR C's merge SHA.

**Report order on screen:** question → direct answer → population,
filters and coverage → primary visualisation or KPI → chart/table switcher
→ complete table → verified supporting findings → caveats → Show Work →
collapsed technical details. Execution internals must never precede the
answer.

**Presentation renderer.** Read `run.presentation`. The frontend must not
parse `finding.text` to discover shape, infer units, manufacture
highlights, map booleans without backend metadata, or compute summaries
separately. Keep a compatibility renderer for payloads where
`presentation` is absent.

**Chart selection matrix** (deterministic, from semantic type + shape):

| result | chart |
|---|---|
scalar | KPI, no one-bar chart |
boolean comparison | full-width two-value, labelled from `boolean_labels` |
nominal categories | horizontal bars |
ordered numeric dimension | dot/line on a **numeric** axis |
time | chronological line |
explicit ranking | ordered horizontal bars |
two categorical cuts | grouped bars below a readability ceiling |
time + category | multi-series line below a series ceiling |
excessive cardinality | table first, with `no_chart_reason` |

**The known chart defect to fix here:** charts have no `width`/`autosize`,
so Vega-Lite uses a fixed step per category and **cardinality drives the
rendered width**. Two categories render a narrow strip in a wide card; 48
render wider. Fix with `ResizeObserver` on the host, compute the plot
width, and call `view.resize()`/`view.width()` on container change. Assert
**bounding boxes** at 2, 3, 33, 45 and 48 groups — the presence of an
`<svg>` proves nothing.

Also in PR D: `Chart.tsx` hardcodes `theme: 'dark'` and a dark axis
palette. It must read CSS variables and update when the app theme changes.

**Axis honesty:** bars start at zero; bounded line/dot domains need clear
ticks and must not exaggerate a tiny difference; ordered numeric
dimensions get a numeric axis; dates chronological, never lexicographic;
percentages formatted consistently with stored values; units in axis
titles and tooltips.

**Compare Both:** `shareOneResult` is already consumed (PR #19) — one
shared result when contracts, coverage, values **and withheld counts**
match. Keep it.

**Provenance:** redesign as an evidence inspector — clicked statement,
originating strategy, accepted contract, calculation, filters, query,
cited cells, verdict, caveats; SQL and MCP trace behind disclosure. Never
fall back to the other run's side.

**Print/PDF order:** title and metadata → question and dataset → answer →
scope → chart → table → findings → caveats → compact provenance →
optional technical appendix. Exclude navigation, upload controls, the
ambient field, progress UI, hover controls, animation and empty panels.
Repeat table headers. Page numbers. Do not force landscape without proof
that Chromium honours it.

### PR E — planning audit, polish, release evidence

Move explicit modes into the advanced workspace showing route, why,
extracted requirements, accepted contract, coverage, AI use/fallback,
model calls and cost, contract differences, result equality.

One compact timeline: **Interpret → Contract → Query → Verify → Present**,
every stage from a real event. No invented stages, no giant empty
diagram, no MCP terminology as the ordinary user's primary status, no
completed stage left pulsing.

Distinct terminal states: safe refusal, execution failure, quota stop,
verification withholding, zero supported findings, session expired, AI
unavailable, service unavailable. Each: one reason, the affected
component, what was *not* guessed, valid reformulations, technical detail
behind disclosure.

Then: performance measurements (bundle sizes, largest chunks, long tasks,
layout shift; code-split Vega if it helps), documentation sweep
(README, ARCHITECTURE, LIMITATIONS, deployment, ADR index, screenshots,
privacy/mode disclosures), the full release gate matrix, and a mutation
report naming every injected defect and the test that caught it.

---

## 5. The blocking defect — fix before opening a PR for C

**`web/e2e/foundation.spec.ts` exhausts the upload-session cap, and a
skipped Playwright test looks like a pass.**

`Settings.max_active_upload_sessions = 24` (`src/agentic_analytics/config.py:233`).
The 9 new tests perform 7 uploads, each opening a session. Run with the 29
existing specs, the suite crosses 24 active sessions and
`web/e2e/helpers.ts::uploadFile()` calls `test.skip()` on "at capacity".

Observed twice:
- a full 38-test run reported `9 skipped, 29 passed`;
- during mutation testing a run reported `3 passed` that was really **6
  skipped** — which briefly made mutation C5 look caught when nothing had
  run. It had to be re-done in isolation.

**Do not fix this by raising the limit in CI.** Correct fixes, in order of
preference:
1. Upload once in a `beforeAll` and share the session across the
   foundation tests.
2. End the session explicitly at the end of each test.
3. Only if neither works: stagger, and document why.

Then confirm a full run reports **0 skipped** before trusting it.

**Add a CI guard.** A step that fails when the Playwright summary contains
any skipped test would have caught both incidents. This is cheap and the
whole suite's credibility depends on it.

---

## 6. Standing constraints from the owner

**Never:**
- let a model supply final arithmetic, write executable SQL that reaches
  DuckDB, or weaken explicit question requirements;
- infer causation or significance without the implemented analysis;
- hide refusals, failures, fallback, incomplete coverage or truncation;
- commit the owner's downloaded datasets or any absolute user path;
- deploy, change Render, use credentials, tag, or make a paid model call;
- merge without CI green on the exact head SHA;
- describe the product as an autonomous AI analyst — the positioning is
  "a governed analytics engine with agent-assisted planning and
  deterministic execution";
- call deterministic mode "fake" or "simulated" in public copy;
- claim a gate passed unless it ran, or weaken an assertion to make a
  release green;
- rewrite historical evaluation artifacts to look better.

The owner deploys. Merged ≠ serving; always say which SHA is which.

### An owner decision already made — do not relitigate

`infer_schema` **cannot** distinguish an ordered quantity from a numeric
entity key. `age` (48 values, 18–65) and `Store` (45 values, 1–45) receive
identical classifications — same role, same reason string — because they
are structurally identical dense integer ranges.

The owner's instruction: **do not infer different semantics from numeric
density or hard-coded column-name lists.** Keep PR A's uniform treatment.
Resolve it in the schema-inspector phase with a **user-confirmable
semantic override** stored as metadata: `ordered numeric`,
`categorical code`, `identifier`, `boolean`, `date/time`. Presentation and
chart selection then read the **confirmed** metadata. Where evidence is
insufficient, mark the column **ambiguous** rather than guessing.

Recorded in `docs/adr/0004` and `LIMITATIONS §11a`. Belongs in PR C item 6
and PR D.

---

## 7. Pre-existing defects found: one fixed, two recorded

**Fixed (PR B).** `average total_sleep_hours where mood is good` resolved
as `exact` and was answered for **every** participant. An equality clause
whose column did not resolve was silently `continue`d in
`row_filters.py`, so the restriction vanished — while the adjacent
*ambiguous*-column branch refused correctly. The module's own docstring
states the policy it was violating: *"a false detection costs a refusal
with a reason, a missed one costs a wrong answer presented as the right
one."* Now refuses, classified `ambiguous`.

**Recorded, not fixed — LIMITATIONS §11b.** `chronotype is Night Owl`
binds as `chronotype = 'Night'`: the equality value group is
`[\w][\w.-]{0,62}` and stops at the first space. The filter matches no
rows and the empty-population guard declines the run — safe, but
confusing, and the message does not say the value was cut. Widening the
capture would swallow a trailing `by region`, the same over-capture that
once turned a filtered breakdown into a two-cut one. Needs its own change
with its own corpus evidence.

**Recorded, not fixed — LIMITATIONS §11a.** The age/Store
indistinguishability above.

---

## 8. How to verify anything

```bash
# Python
.venv/bin/python -m pytest tests/ -q               # ~8 min
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy
.venv/bin/python -m agentic_analytics.cli validate-recordings

# Frontend — run what CI runs
cd web && npm run typecheck && npm run test && npm run build

# Browser. Strip the credential env; confirm provider_mode: fake first.
env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY AAE_PROVIDER_MODE=fake \
  AAE_LIVE_ANALYTICS_ENABLED=true AAE_UPLOADS_ENABLED=true \
  AAE_UPLOADS_PER_IP_PER_HOUR=2000 AAE_ANALYSES_PER_IP_PER_HOUR=4000 \
  .venv/bin/python -m uvicorn agentic_analytics.api.app:app \
  --host 127.0.0.1 --port 8000 --env-file /dev/null &
curl -s localhost:8000/api/health   # must report provider_mode: fake
cd web && AAE_E2E_BASE_URL=http://127.0.0.1:8000 npx playwright test --project=chromium
```

The local server picked up an ambient `OPENAI_API_KEY` once and came up
`provider_mode: cloud`. Always strip the environment and check
`/api/health` before running anything that could issue a request.

### Three traps, all of which cost time

1. **`npx tsc --noEmit` is not what CI runs.** CI runs `npm run typecheck`
   = `tsc -b`, which includes the test project. A bad testing-library
   option passed `--noEmit` and failed `-b`.
2. **A failed build leaves `dist/` stale.** Browser mutation testing must
   rebuild *and confirm the build succeeded*, or the test runs against the
   previous bundle and the mutation looks caught when it never shipped.
   Two mutations were falsely "caught" this way.
3. **A skipped test reads as a pass.** See §5.

---

## 9. Mutation testing is the standard of evidence

Every behavioural claim was verified by reintroducing the defect and
confirming a *named* test fails. Totals: A 12/12, B 9/9, C 7/7 — 28/28.

Record which test caught each mutation. A mutation that "survives" is
usually an **ineffective mutation** rather than a weak test. Four of mine
were:

- `[:1]` on a one-element list — a no-op;
- breaking one selector in a list the test reached through another;
- nested quote escaping in a shell loop, which silently produced no edit
  and reported 5 false survivors;
- the skipped-test incident in §5.

Verify the mutation actually changed behaviour before concluding a test is
vacuous. Then repair genuinely vacuous tests rather than counting them.

---

## 10. Suggested order from here

1. **§5** — fix the session-cap defect and add the skipped-test CI guard.
2. Run the full local matrix; confirm 0 skipped.
3. Finish PR C items 1–7 (§3). Items 5 and 3 are the most visible to the
   owner; item 1 is optional if risky.
4. Open PR C, CI green on the exact head SHA, owner merges, CI on the
   merge SHA.
5. PR D — the report and chart work, including the Vega responsive-width
   defect and the theme hardcoding.
6. PR E — audit surface, terminal states, performance, docs, release
   evidence.
