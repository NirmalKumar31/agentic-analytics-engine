# Handoff: the five-PR governed-analytics rebuild

Written for the next agent picking this up mid-sequence. Not product
documentation — delete it when PR E lands.

Date: 2026-10-01
Repo: `NirmalKumar31/agentic-analytics-engine`

---

## 1. Where things are, exactly

| | |
|---|---|
`origin/main` | `8a152dc` — all 10 CI jobs green |
Current branch | `design/precision-atlas-foundation` at `3444f47`, pushed |
Open PRs | **none** |
Worktree | clean |

PR A and PR B are **merged and green on their merge SHAs**. PR C is
committed and pushed but **no PR is open for it**, because it is
incomplete and has one known defect (§4).

### Merged

| PR | SHA | What |
|---|---|---|
#20 | merge `5f2044c` | Deterministic presentation contract |
#21 | merge `8a152dc` | Automatic governed planning (`auto` route) |

### Not started

PR D (report/table/chart experience) and PR E (planning audit, polish,
release evidence).

---

## 2. What each merged PR actually did

### PR A — `src/agentic_analytics/presentation/`

The frontend used to build reports by arranging `finding.text`, which made
the browser decide five things it could not know: which number is the
answer, what a `0` means, whether an axis is ordered, whether a breakdown
is complete, and what a unit is.

`build_presentation()` returns a typed `AnalysisPresentation` — a pure
function of the accepted contract, verified snapshot, coverage records,
published findings and chart decision. No model call.

Four properties, each worth preserving:

1. **No figure it cannot point at.** Every highlight cites ≥1
   `EvidenceCell`; the schema rejects one that does not. Prose is
   *checked*: `numbers_resolve()` requires each number in a headline to be
   a cell of the cited result, a recorded coverage count, or a declared
   difference recomputed from two cells.
2. **Never claims more than the analysis supports.** "Highest" only when
   coverage records every group present. Two-group differences are
   "descriptive". Ordered dimensions state that no trend was tested.
3. **Display metadata needs converging evidence.** Boolean only when every
   value present is 0/1 or the type is declared boolean — *not* merely two
   distinct values, which would relabel a two-store table "Off"/"On".
   Percentage needs a name token *and* a compatible range. Currency is
   never inferred from a name.
4. **Additive.** `report`, `findings`, `rejected`, `charts`, `results`,
   `query_contract` unchanged. A presentation that fails to build degrades
   to the older path rather than failing a verified run.

**Still unconsumed by the UI.** The frontend renders from `finding.text`
until PR D. That was deliberate staging.

ADR: `docs/adr/0004-deterministic-presentation-contract.md`

### PR B — `src/agentic_analytics/analytics/resolution.py`

`auto` is a routing *policy* over the existing planners, not a third
decision-maker. 12 typed `ResolutionIssue` codes across 5
`ResolutionState`s. Exactly one state earns a planning request:

| state | planner consulted |
|---|---|
`exact` | no |
`ambiguous` (dataset can answer, wording didn't say how) | **yes, once** |
`unresolved` / `unsupported` / `unsafe` | no |

Ambiguity is the **weakest** claim: a question that is also unresolved
takes the stronger reading.

**The cost argument, and why the tests look the way they do.**
`open_governed_cloud_provider()` takes the durable ledger slot *at
construction*. So construction — not the request — is when quota is spent.
`RunContext.open_planner` is therefore a **factory**, called only once
ambiguity is established, and the tests count *constructions*:

```
exact question            → 0 planners built
column doesn't exist      → 0
unimplemented operation   → 0
genuinely ambiguous       → 1
```

Do not "simplify" this to passing a provider. It would charge quota
against questions that never consult a model and read the credential on a
path that does not need it.

`provider_kind` returns `governed` for auto, not `cloud` — the record is
created before the question is resolved.

ADR: `docs/adr/0005-automatic-governed-planning.md`

---

## 3. PR C — what is done and what is not

Branch `design/precision-atlas-foundation`, commit `3444f47`.

### Done

- **`web/src/styles/tokens.css`** — the new design system. Precision-atlas
  palette (~90% neutral), 4px space scale, named motion budgets, z-index
  ladder, reading measures, dark mode, reduced-motion overrides.
- **A bridge layer** at the bottom of `tokens.css` redefining the old 32
  variable names in terms of the new tokens. This is why the palette
  changes everywhere without touching 289 call sites. **Shrink it as
  components are rewritten; delete it when empty.**
- **Decoration removed**, measured:

  | | before | now |
  |---|---|---|
  `@keyframes` | 17 | 11 |
  `animation:` | 27 | 20 |
  `box-shadow` | 17 | 9 |
  gradients | 8 | 3 |
  `var(--glow)` | 6 | **0** |
  hover lifts | yes | **0** |

- **`web/src/lib/phase.ts`** — 12 phases, derived from existing state.
  Published to `document.body.dataset.phase`, which the ambient rules key
  on. Also mirrors document visibility to `data-hidden`.
- **Tests**: `web/src/test/phase.test.ts` (23), `web/e2e/foundation.spec.ts`
  (9). 7 mutations injected, 7 caught.

### Not done — the rest of PR C

1. **Split `styles.css`** (still 2,044 lines, one file) into
   `reset.css`, `shell.css`, `components.css`, `report.css`, `motion.css`,
   `print.css`. The section comments (`/* ---- shell */` etc.) are the
   natural split points; there are ~20 of them. Do this mechanically and
   verify with a build plus the browser suite — the spec permits skipping
   it if it cannot be done without accidental behaviour change.
2. **Component extraction** from `App.tsx` (729 lines): `AppShell`,
   `ProductHeader`, `DatasetIdentity`, `WorkflowIndex`,
   `DatasetOnboarding`, `SchemaInspector`, `QuestionComposer`,
   `PlanningMethodDisclosure`, `RunProgress`, `ReportWorkspace`,
   `TechnicalInspector`, `TerminalState`, `SessionControls`.
3. **Remove the permanent right rail** after completion
   (`components/RightRail.tsx`, 186 lines) — technical metrics must not
   compete with the answer.
4. **Onboarding copy** — the one accurate sentence, built-in example,
   CSV/Parquet upload, privacy disclosure, no marketing hero.
5. **Offer `auto` in the UI.** PR B ships the capability
   (`capabilities.modes` now has three entries); `ModeSelector` reads by
   key so it is currently ignored. Default should be "Governed Analysis";
   Deterministic / AI / Compare move behind an advanced disclosure.
6. **Accessibility pass** — 200% zoom, 44px touch targets, landmarks,
   heading order, focus trap for the drawer, breakpoints 360/390/768/
   1024/1440/1920.
7. **ADR** for the visual foundation, and `docs/ARCHITECTURE.md` update.

---

## 4. The one known defect — fix this first

**`web/e2e/foundation.spec.ts` exhausts the upload-session cap, and a
skipped Playwright test looks like a pass.**

`Settings.max_active_upload_sessions` is **24** (`config.py:233`). My 9
new tests perform 7 uploads, each opening a session. Run together with the
29 existing specs, the suite crosses 24 active sessions and
`helpers.uploadFile()` calls `test.skip()` on "at capacity".

Observed: a full 38-test run reported `9 skipped, 29 passed`. Earlier,
during mutation testing, a run reported "3 passed" that was actually 6
skipped — which briefly made a mutation look caught when nothing had run.

**Do not fix this by raising the limit in CI.** The right fix is to stop
opening a session per test: upload once in a `beforeAll` and share it
across the foundation tests, or end the session explicitly at the end of
each. Then confirm a full run reports **0 skipped** before trusting any
result from it.

This is also worth a guard: a CI step that fails if the Playwright run
reports any skipped test would have caught both incidents.

---

## 5. Standing constraints (from the owner, still in force)

- **Never** let a model supply final arithmetic, write executable SQL, or
  weaken explicit question requirements.
- No deploy, no Render change, no credential use, no tag, no paid model
  call. Merging is permitted only after CI is green on the exact head SHA,
  and the owner deploys.
- Do not commit the owner's downloaded datasets or any absolute user path.
  Fixtures are derived and synthetic: `tests/fixtures/sleep_study.py`,
  `tests/fixtures/retail_monthly.py`.
- Do not describe the product as an autonomous AI analyst. The positioning
  is "a governed analytics engine with agent-assisted planning and
  deterministic execution."
- Never describe deterministic mode as "fake" or "simulated" in public copy.
- Do not claim a gate passed unless it ran. Do not weaken an assertion to
  make a release green.

### An owner decision already made — do not relitigate

`infer_schema` cannot distinguish an ordered quantity from a numeric
entity key: `age` (48 values, 18–65) and `Store` (45 values, 1–45) get
*identical* classifications, same role, same reason string.

The owner's instruction: **do not infer different semantics from numeric
density or hard-coded column-name lists.** Keep PR A's uniform treatment.
Resolve it in the schema-inspector phase with a **user-confirmable
semantic override** stored as metadata (`ordered numeric`,
`categorical code`, `identifier`, `boolean`, `date/time`); presentation and
chart selection then read the *confirmed* metadata. Where evidence is
insufficient, mark the column **ambiguous** rather than guessing.

That belongs in PR C item 6 / PR D.

---

## 6. Pre-existing defects found; one fixed, two recorded

**Fixed in PR B.** `average total_sleep_hours where mood is good` resolved
as `exact` and was answered for *every* participant. An equality clause
whose column did not resolve was silently skipped in
`row_filters.py`, so the restriction vanished. Now refuses, classified
`ambiguous` (a planner may bind the column).

**Recorded, not fixed — LIMITATIONS §11b.** `chronotype is Night Owl`
binds as `chronotype = 'Night'`: the value grammar stops at the first
space, the filter matches no rows, and the empty-population guard declines
the run. Safe but confusing. Widening the capture would swallow a trailing
`by region` — the same over-capture that once turned a filtered breakdown
into a two-cut one. Needs its own change with its own corpus evidence.

**Recorded, not fixed — LIMITATIONS §11a.** The age/Store indistinguish­
ability above.

---

## 7. How to verify anything here

```bash
# Python
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy
.venv/bin/python -m agentic_analytics.cli validate-recordings

# Frontend
cd web && npm run typecheck && npm run test && npm run build

# Browser — needs a server. Never with a real credential in the env.
env -u OPENAI_API_KEY -u ANTHROPIC_API_KEY AAE_PROVIDER_MODE=fake \
  AAE_LIVE_ANALYTICS_ENABLED=true AAE_UPLOADS_ENABLED=true \
  .venv/bin/python -m uvicorn agentic_analytics.api.app:app \
  --host 127.0.0.1 --port 8000 --env-file /dev/null
# confirm /api/health reports provider_mode: fake, then
cd web && AAE_E2E_BASE_URL=http://127.0.0.1:8000 \
  npx playwright test --project=chromium
```

Two traps worth knowing, both of which caught me:

- **`npx tsc --noEmit` is not what CI runs.** CI runs `npm run typecheck`
  (`tsc -b`), which includes the test project and catches errors the other
  misses.
- **A failed build leaves `dist/` stale.** Browser mutation testing must
  rebuild and confirm the build succeeded, or the test runs against the
  previous bundle and the mutation appears caught when it never shipped.

---

## 8. Mutation testing is the standard of evidence here

Every behavioural claim in this sequence was verified by reintroducing the
defect and confirming a named test fails. Totals so far: PR A 12/12, PR B
9/9, PR C 7/7.

Record which test caught each mutation. A mutation that "survives" is
usually an *ineffective mutation* rather than a weak test — three of mine
were (a no-op slice on a one-element list; breaking one selector in a list
that the test reached through another; and the skipped-test incident in
§4). Verify the mutation actually changed behaviour before concluding the
test is vacuous.
