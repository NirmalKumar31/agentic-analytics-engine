# Handoff: the frontend redesign programme (PRs F–J)

Working notes for the next agent. Not product documentation — delete when
PR J lands.

Date: 2026-10-01 · Repo: `NirmalKumar31/agentic-analytics-engine`

---

## 0. Read this first

The backend is done and verified. **This phase is frontend architecture
and UX.** Do not rewrite DuckDB execution, MCP tools, query-contract
compilation, verification, routing, quotas or governed AI planning unless
a frontend integration test proves a backend defect.

The owner's one-line framing, which the interface must reflect:

> A governed analytics engine with agent-assisted planning and
> deterministic execution.

Not a chat wrapper, not an autonomous analyst, not a dashboard template.

---

## 1. Exact state

| | |
|---|---|
`origin/main` | `78f619b` |
**Deployed** | `1f2446d` (older than main; the difference is test-only) |
Worktree | clean |

### Three PRs open, all awaiting the owner's merge

| PR | Branch | Head | State |
|---|---|---|---|
**#27** | `feat/cross-browser-and-a11y-gate` | `e406542` | **PR F.** 8/10 green; Docker + Browser E2E running at handoff |
**#26** | `fix/compare-naming` | `506eff3` | Renames "Compare Both" → "Compare planning strategies" |
**#25** | `docs/release-evidence-governed-analytics` | `6d97cc0` | Release evidence, docs only |

**Do not merge any of them yourself.** The owner merges. Report the exact
head SHA and its CI verdict, then wait.

### Merged and deployed already

| PR | What |
|---|---|
#20 | Deterministic presentation contract (`AnalysisPresentation`) |
#21 | Automatic governed planning (`auto` route) |
#22 | Report workspace + visual foundation |
#23 | Planning audit, chart width, layout, focus fixes |
#24 | Made the golden layout assertions actually execute |

---

## 2. What PR F (#27) contains, and why it exists

It is the gate the rest of the programme needs. Merge it before
restructuring anything.

**New:** `web/e2e/accessibility.spec.ts` — 11 tests. Six axe scans
(landing, profiled upload, completed report, refused report, planning
audit open, 360px report) plus keyboard focus visibility, no-trap,
accessible names, disclosure operability, and drawer focus restoration.

**CI now runs three engines** as separate jobs so a failure names the
engine. `web/scripts/check-playwright-skips.mjs` takes a declared
per-browser allowance via `AAE_E2E_ALLOWED_SKIPS` and fails when skips are
*fewer* than declared as well as more — fewer means the gating changed and
the declaration is stale. WebKit and Firefox each declare **1**: the
browser-PDF test, which uses `page.pdf()`, a Chromium-only API.

**Five defects it found**, none of which anything was testing:

| defect | measured | cause |
|---|---|---|
Warning/error notices unreadable | **1.06:1** | hardcoded `#ffdf9a` / `#ffb0a5` — dark-palette pastels never revisited |
`--action` / `--warning` illegal as text | 3.47 / 3.31 | one token used for both rings and words |
`--ink-muted` below threshold | 3.79, then 4.16 | checked against one surface, failed a darker one |
Skipped lane detail unreadable | **2.75:1** | `opacity: 0.72` composites the text |
Drawer never restored focus | — | Escape left focus on `<body>` |

Plus `scrollable-region-focusable` on `.flow` and `nav` — a regression
introduced in #23 by adding `overflow-x: auto` without `tabindex`.

**New tokens:** `--action-text: #ae3524`, `--warning-text: #855c17`. The
vivid `--action` / `--warning` stay for rings, borders and chart marks,
where WCAG 1.4.11 asks 3:1 and both clear it. Use the `-text` variants
anywhere the colour carries words. The measurement table is a comment
beside the tokens in `tokens.css` — extend it rather than guessing.

**New doc:** `docs/PERFORMANCE-BUDGET.md`. Measured baseline: 336 kB
across two files initially, Vega requested only once a chart renders,
153 ms to a visible report on a local fixture.

### Local evidence at `e406542`

Python 2,078 tests 0 failures · frontend 197 · Chromium 55 passed 0
skipped · Firefox 54 + 1 declared skip · WebKit 54 + 1 declared skip ·
accessibility 11/11 on all three · ruff, ruff format, mypy src, mypy
scripts, `git diff --check` clean · 4 mutations injected, 4 caught.

**No WCAG conformance claim.** What is true: zero serious or critical axe
violations across six states on three engines, with no rule excluded.
Keep that distinction; do not upgrade it to "AA compliant".

---

## 3. What remains — PR G, H, I, J

### The honest summary

**The UX/UI redesign has not started.** It is PR I. Measured right now:

| | |
|---|---|
`web/src/App.tsx` | **914 lines**, 21 pieces of local state |
Named components that exist | **0 of 13** |
`web/src/styles.css` | **2,234 lines**, one file |
`web/src/styles/tokens.css` | 352 lines |
CSS refs via the compatibility bridge | **196 of 320** |

The information architecture — dataset identity strip, schema inspector,
workflow index, restructured onboarding — is unbuilt. The palette reaches
the UI through a bridge that was meant to shrink as components were
rewritten; the components were never rewritten.

---

### PR G — component architecture extraction

Branch from `origin/main` **after #27 merges**.

**Purpose: structural only.** Do not combine with a visual redesign. If
appearance changes, the diff becomes unreviewable and a regression hides.

Extract, adjusting a boundary only when repository evidence justifies it:

`AppShell` · `ProductHeader` · `DatasetIdentity` · `WorkflowIndex` ·
`DatasetOnboarding` · `SchemaInspector` · `QuestionComposer` ·
`PlanningMethodDisclosure` · `RunProgress` · `ReportWorkspace` ·
`TechnicalInspector` · `TerminalState` · `SessionControls`

**Architecture rules**

`App.tsx` keeps orchestration: session state, selected mode, current
dataset and run, API initiation, top-level transitions. Target **under
~400 lines**. No extracted component should become another 800-line
dumping ground.

Components take typed props and emit intent callbacks. They do not fetch
independently unless explicitly designed as a data boundary.

Do not duplicate run-state logic. `web/src/lib/runState.ts` is the
authoritative terminal-state interpretation and
`web/src/lib/phase.ts` the authoritative phase derivation — **the phase is
derived from existing state, never stored**. A 22nd piece of state saying
which phase we are in is one that can disagree with the run payload.

Keep comparison logic in `web/src/lib/comparison.ts`. Keep
`AnalysisPresentation` rendering (`PresentationReportView.tsx`) separate
from the compatibility/fallback renderer (`ReportView.tsx`).

No new state-management dependency unless a measured problem requires it.
No React Router unless multiple URLs are genuinely needed.

Do not move backend semantic policy into TypeScript. Keep
`web/src/lib/types.ts` synchronised with the Pydantic models.

**Selectors:** prefer accessible roles and names over `data-testid`. Keep
a testid only where it names a stable user-visible contract
(`report-panel`, `direct-answer`, `shared-result`). Note that renaming
visible copy *should* break tests that assert on it — that is the right
coupling, as PR #26 demonstrated when 11 selectors broke.

**Non-vacuity:** break refusal rendering, report rendering, the planning
audit disclosure, mode selection and session end — each must fail an
identified test. Restore each and record truthfully.

---

### PR H — CSS architecture and token migration

Create, with filenames adjusted if ownership is clearer:

```
web/src/styles/
  tokens.css  reset.css  shell.css  onboarding.css  workflow.css
  report.css  data.css   audit.css  motion.css      print.css
  responsive.css
```

Import order from `main.tsx` matters: `tokens.css` first. **Do not let
load order become the hidden source of correctness** — if a rule only
works because of where its file sits, the ownership is wrong.

**Migrate the 196 old-name references** to the new vocabulary. The bridge
block at the bottom of `tokens.css` may remain only for
archived/compatibility views, with a comment saying why. Delete it when
empty.

**Report before/after:** old-token references, total CSS lines, duplicate
declarations removed, unused selectors removed. Prove a selector is unused
before deleting it.

**Preserve, with tests already covering each:** print/PDF rules;
reduced-motion behaviour; focus-visible treatment (including `summary` in
the selector); 44px touch targets; the deliberate **WCAG 2.5.8 inline
exception** for `.disclosure > summary` (a link inside a sentence — do not
"fix" it to 44px); phone overflow fixes (`flex-wrap` on `.topbar` and the
`max-width: 640px` block are load-bearing; the `min-width: 0` block is
labelled defensive and is *not* currently exercised by a test); chart
sizing; subgrid lane alignment.

No `!important` as a migration shortcut. Keep the small z-index scale in
`tokens.css`; do not escalate ad hoc.

Validate at **360, 390, 768, 1024, 1440, 1920** with bounding-box
assertions, not screenshots alone.

---

### PR I — information architecture and visual redesign

This is the work the owner has been asking for. The full brief is in the
owner's prompt; the essentials:

**Character:** minimal, editorial, instrument-like, calm. No purple/blue
"AI" gradient, no glowing chatbot aesthetic, no glassmorphism everywhere,
no dashboard-card wall, no constant decorative movement, no marketing
headline inside the product, no fake terminal, no unexplained hashes or
internal IDs in the main report.

**Palette direction:** warm mineral/off-white canvas; dark ink or
charcoal text; one restrained signal colour (vermilion, rust, oxblood or
electric chartreuse); a cool technical secondary used sparingly; a
data-series palette checked for contrast and colour-blind
distinguishability. **Implement tokens and render every principal state in
light and dark before deciding** — do not pick colours by description.

**Any new colour must be measured against all four surfaces**
(`--surface-canvas`, `--surface-paper`, `--surface-raised`,
`--surface-inset`) at 4.5:1 for text and 3:1 for UI components. The table
in `tokens.css` shows the method. Three of the five PR F defects were a
colour that cleared one surface and failed another.

**Hierarchy the interface must express:**

```
Dataset → Question → Governed interpretation → Deterministic execution
       → Verification → Answer / refusal → Optional technical audit
```

**Sections:** product header · dataset identity strip · onboarding ·
schema inspector (collapsed after successful inference, ambiguous fields
visually distinct) · question composer (dominant, schema-derived
examples) · workflow index (Dataset → Ask → Analyse → Verify → Report,
real stages only) · run progress · report workspace (answer first) ·
result tables · charts · Compare · technical inspector (collapsed) ·
terminal states (refused / failed / withheld / quota / cancelled / zero
findings all visually distinct — **never present an empty report as
"Complete"**).

**Motion:** ambient only while idle or onboarding; a transition when
profiling completes; stage-line progression during a run; one chart
reveal; disclosure expansion; hover/focus feedback that does not move
layout. Never: infinite pulsing, glowing halos, sweeps across reading
content, parallax behind tables, animation surviving
`prefers-reduced-motion`, or animation as the only state signal. **When a
report exists, background activity recedes substantially** — already
implemented via `body[data-phase]`; keep it.

**Known product gap worth closing here:** Compare mode shows the
deterministic and AI lanes but never says **which route `auto` would have
taken**. `auto` is a routing policy over those two planners, not a third
lane — so there is correctly no third column — but the useful fact ("this
question resolves from rules alone, at no model cost") is computed and
unsurfaced. The owner noticed its absence. Surface it.

**Schema-role override:** only if it can be done safely. Expose controls
for ambiguous/low-confidence columns; session-scoped; show
"user-confirmed" distinctly from "inferred"; **invalidate the query
contract** after a change and never reuse stale results; include the
override in Planning Audit and provenance; **validate on the backend**;
test end-to-end through real `infer_schema()`. If that cannot be done
safely in PR I, write an ADR and leave it for a combined backend+frontend
PR. **Do not fake it with frontend-only labels.** And do not resolve
age-vs-`Store` with column-name special cases — the owner has ruled on
this twice.

**Acceptance scenarios (20):** landing · demo question with no chart ·
demo ranking with a chart · uploaded two-group · 33-period trend ·
41-group · 45-group · 48-group · near the result ceiling · refusal for a
nonexistent measure · numeric filter · categorical filter · period filter
· quota stopped · AI-planning refusal · Compare agreement · Compare
divergence · verification withheld · Planning Audit expanded · print/PDF.

**Every golden test must fail when its target is missing.** Never:

```ts
if (elementExists) { expect(...) }   // a disabled assertion
```

This exact pattern made six breakpoint tests pass vacuously — see §5.

---

### PR J — release audit and deployment handoff

Run the full matrix: Python with branch coverage · ruff check/format ·
mypy src and scripts · frontend unit · production build · Chromium,
Firefox and WebKit E2E · axe scenarios · all golden breakpoints · Docker
acceptance · recordings · benchmark · corpus in both modes · security
suites · gitleaks tree and history · pip-audit and npm audit ·
wheel/sdist and clean install · `git diff --check`.

**Mutations:** remove chart live resize; reintroduce a fixed 240px width;
hide a refused pane; omit summary focus styling; allow an undeclared
Playwright skip; render raw finding prose instead of the presentation
contract; mislabel partial scope as complete; remove report-first
ordering; bypass override validation if overrides shipped; make one
terminal state fall through to generic completion. Each must cause an
identified failure. **Do not count ineffective mutations** — see §5.

**Documentation:** README screenshots only after the UI is approved;
ARCHITECTURE with component and style boundaries; LIMITATIONS honestly;
preserve the release-evidence distinctions already in
`docs/RELEASE-EVIDENCE-governed-analytics.md` (unit/integration ·
container · hosted credential-free · paid provider · **not measured**).

Do not claim universal dataset correctness, WCAG AA, Firefox/WebKit CI
before those jobs truly run, or deployment before the owner deploys.

**Deployment sequence:** PR exact-head CI green → explicit merge approval
→ merge → merge-SHA CI green → give the owner the exact SHA → owner
deploys → credential-free hosted acceptance → **no paid call** unless
backend AI execution changed and the owner separately authorises it →
confirm deployed SHA equals intended → only then recommend a tag.

---

## 4. Invariants — do not break any of these

1. Presentation text and chart/table metadata come from the typed backend
   `AnalysisPresentation`.
2. The browser must not infer which number is the answer from prose.
3. Every published number remains traceable to a result cell.
4. Refused, failed, quota-stopped and verification-withheld are visibly
   different states.
5. A refused AI pane is never blank.
6. Compare never claims equality because two pieces of prose look alike —
   it compares contracts, coverage, values **and withheld counts**.
7. Complete and partial result scopes stay explicit.
8. Breakdowns are never silently truncated.
9. Deterministic analysis works with no provider.
10. Exact questions in `auto` make **zero** provider calls. The tests
    count *provider constructions*, because constructing the governed
    provider **is** the ledger admission. Do not "simplify"
    `RunContext.open_planner` from a factory into a provider.
11. AI planning may clarify genuine ambiguity, never bypass the contract.
12. Planning Audit exposes only the accepted contract, coverage, route,
    timings, fallback and issue codes — never prompts, hidden reasoning,
    provider bodies or raw uploaded rows.
13. Session isolation, deletion, cookies, security headers and leak
    protections stay intact.
14. Print/PDF output stays complete and readable.
15. The Playwright skip guard keeps failing CI on undeclared skips.
16. Never hand-write a schema in an acceptance test when the real path
    uses `infer_schema()`.

### Owner rules

No deploy, no Render change, no credential inspection, no Redis change,
no tag, no paid provider call, no merge without explicit approval. Never
push to `main`. The owner deploys.

### An owner decision already made — do not relitigate

`infer_schema` cannot distinguish an ordered quantity from a numeric
entity key: `age` (48 values, 18–65) and `Store` (45 values, 1–45) get
identical classifications. **Do not infer different semantics from
numeric density or column-name lists.** Resolve it only through a
user-confirmable override as described in PR I. Recorded in
`docs/adr/0004` and `LIMITATIONS` §11a.

---

## 5. Traps that have already cost real time

**A conditional assertion is a disabled assertion.** Six golden
breakpoint tests asked a question that produces **no chart**, so
`if (fraction !== null)` skipped the central claim at every width. Found
by tracing network requests and noticing the Vega chunk was never
fetched. Fixed in #24.

**`tsc --noEmit` is not what CI runs.** CI runs `npm run typecheck`
(`tsc -b`), which includes the test project.

**A failed build leaves `dist/` stale**, so a browser mutation test runs
against the previous bundle and the mutation appears caught. Always
confirm the build succeeded.

**A skipped test reads as a pass.** Two incidents: a run reporting
"9 skipped, 29 passed" after the upload-session pool was exhausted
(`max_active_upload_sessions = 24`), and a mutation run reporting
"3 passed" that was really 6 skipped. Use the demo warehouse instead of
uploading where possible; share one session across tests; the guard now
catches it.

**axe composites `opacity`.** Scanning mid-fade-in measures colours
elements are passing *through*. The spec emulates reduced motion and waits
for `getAnimations()` to settle.

**`:focus-visible` cannot be triggered from script.** Browsers gate it on
keyboard interaction, so `element.focus()` plus
`matches(':focus-visible')` matches nothing. Drive it with real `Tab`
presses.

**WebKit does not focus a button on click.** A click-opened drawer
captures `activeElement` as `<body>`, so focus restoration looks broken
when it is not. Test keyboard activation.

**Four of my "surviving" mutations were ineffective**, not weak tests: a
no-op slice on a one-element list; breaking one selector in a list the
test reached through another; nested quote escaping in a shell loop that
silently produced no edit; and the skipped-test incident above. **Verify a
mutation actually changed behaviour before concluding a test is vacuous.**

**`grep | head` masks exit codes.** It reported two mutations as caught
that were not.

---

## 6. How to verify

```bash
# Python
.venv/bin/python -m pytest tests/ -q                       # ~8 min, 2,078 tests
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy && .venv/bin/mypy scripts
.venv/bin/python -m agentic_analytics.cli validate-recordings

# Frontend — run what CI runs
cd web && npm run typecheck && npm run test && npm run build

# Browser. Strip the credential env and confirm provider_mode: fake first.
env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY AAE_PROVIDER_MODE=fake \
  AAE_LIVE_ANALYTICS_ENABLED=true AAE_UPLOADS_ENABLED=true \
  AAE_UPLOADS_PER_IP_PER_HOUR=3000 AAE_ANALYSES_PER_IP_PER_HOUR=6000 \
  AAE_ANALYSES_PER_SESSION=600 \
  .venv/bin/python -m uvicorn agentic_analytics.api.app:app \
  --host 127.0.0.1 --port 8000 --env-file /dev/null &
curl -s localhost:8000/api/health    # must say provider_mode: fake

cd web
AAE_E2E_BASE_URL=http://127.0.0.1:8000 AAE_E2E_BROWSERS=chromium npm run test:e2e
AAE_E2E_BASE_URL=http://127.0.0.1:8000 AAE_E2E_BROWSERS=firefox \
  AAE_E2E_ALLOWED_SKIPS=1 npm run test:e2e
AAE_E2E_BASE_URL=http://127.0.0.1:8000 AAE_E2E_BROWSERS=webkit \
  AAE_E2E_ALLOWED_SKIPS=1 npm run test:e2e
```

The local server once picked up an ambient `OPENAI_API_KEY` and came up
`provider_mode: cloud`. Always strip the environment and check
`/api/health` before running anything that could issue a request.

Raise the rate-limit env vars for long local runs; the defaults are the
production ceilings and will make tests skip.

---

## 7. Suggested order

1. Report #27's CI verdict on `e406542`; get the owner's merge.
2. Same for #26 and #25.
3. **PR G** from the new `main` — structural only, no visual change.
4. **PR H** — CSS modules and token migration.
5. **PR I** — the redesign. The owner cares most about this one.
6. **PR J** — release audit, then the owner deploys.

Stop at every PR boundary. Give the owner one concise action — "Merge
PR G", "Deploy SHA …" — not a buried checkpoint.
