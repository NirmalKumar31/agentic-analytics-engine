# Visual acceptance — `feat/visual-redesign`

Written for: the reviewer of this PR, and for whoever next changes one of
these surfaces.

This is the reconciliation of the redesign brief's eighteen test
requirements against the suite that now exists, plus the deviations from the
approved mockups that the visual review found and did not fix. It is
deliberately specific about *which test* proves each claim, because a
requirement that is "covered by the suite somewhere" is a requirement nobody
can check.

Every number here came from a run. Nothing is estimated.

---

## 1. Where each requirement is proved

| # | Requirement | Proved by |
|---|---|---|
| 1 | Answer and context above the first viewport break at 360 and 390 | `visualReview.spec.ts` — `report-{phone-small,phone}-{light,dark}` measure the answer's box against the viewport height |
| 2 | Chart bounding box ≥ 90% of content width at each of the six widths | `golden.spec.ts` — `fractionOfContainer` at all six. The threshold was `> 0.8` and is tightened here to the brief's 0.9: measured, it is 90% at phone-small and 98% at the other five, in all three engines |
| 3 | `scrollWidth - clientWidth ≤ 1` in every state | `visualReview.spec.ts` (40 cells), `landing.spec.ts`, `golden.spec.ts`, `terminalStates.spec.ts`, `informationArchitecture.spec.ts` |
| 4 | Table contained: scrolls within its own wrapper, never the page | `report.spec.ts`, and requirement 3 above is what would fail if it escaped |
| 5 | Exactly one finding expanded on arrival at mobile widths | `findingFold.test.tsx` (both sides of the threshold, and that a Compare pane never folds); `report.spec.ts` drives a recorded run with six findings at 390px. It folds only where there is something to fold — see D4 |
| 6 | No resident technical material on the default report canvas | `visualReview.spec.ts` (every cell), `terminalStates.spec.ts`, `evidence.spec.ts`, `informationArchitecture.test.tsx` — all via `onCanvas`, which excludes the hidden print appendix |
| 7 | No duplicated panel heading text in one view | `evidence.spec.ts` "no heading appears twice"; `printAppendix.test.tsx` "names each region once, not three times" |
| 8 | Evidence drawer traps focus; Escape closes and returns focus | `accessibility.spec.ts` "the provenance drawer restores focus and closes on Escape" and "tabbing does not trap"; `components.test.tsx` for the focus-restore unit behaviour |
| 9 | Compare matrix readable at 390px | `visualReview.spec.ts` — `compare-phone-{light,dark}`; `compare.spec.ts` |
| 10 | `prefers-reduced-motion` honoured by every effect in the storyboard | `motion.spec.ts` — 12 tests, measured with `document.getAnimations()` rather than against a selector list |
| 11 | axe on landing, upload, active run, report, compare, refusal, no-findings, evidence-open, mobile | `accessibility.spec.ts` — nine scans, plus a dark-mode scan the brief does not ask for |
| 12 | Chromium, Firefox and WebKit under the reconciling guard | `scripts/check-playwright-skips.mjs`, run per engine with an explicit allowed-skip count |
| 13 | Bundle delta measured; Vega stays lazy-loaded | `scripts/check-bundle.mjs` (built output, CI step after the build) and `lazyVega.test.ts` (source) |
| 14 | One terminal state per run, six separate tests | `terminalStates.test.tsx`, `terminalStates.spec.ts` |
| 15 | No failure vocabulary on a `completed` state | `terminalStates.test.tsx` "a completed run never wears failure language" — all four words, with a control asserting a run that *did* fail still names itself. This existed only as a single `/failed/i` check in one browser test |
| 16 | Compare renders one evidence trigger; tabs issue no request | `compare.spec.ts` "is one control, with a tab per strategy" / "switching tabs changes the panel and issues no request" |
| 17 | On divergence, the structured diff's caption equals its table | `contractDiff.test.tsx` — 17 cases, two of which read the caption's numbers back out of the DOM and compare them with the rendered rows |
| 18 | The design-package consistency audit runs green | `docs/design/generators/audit_mockups.py` — 346 checks |

## 2. The comparative measures, re-measured

The brief set targets for five rendered properties. These are the values the
browser reports on a real report at 1440px, not counts of CSS rules:

| Measure | Before | Target | Measured now |
|---|---|---|---|
| panels on report | 7 | 0 | **0** |
| ALL-CAPS headings | 20 | 0 | **0** on every surface measured |
| rounded containers | 99 | ≤ 12 | **3** landing · **11** dataset · **8** report · **9** Compare · **11** drawer open |
| duplicated panel headings | 1 | 0 | **0** |
| page horizontal overflow | 0 | 0 | **0** at all six widths, both themes |
| chart width share | ~88% | ≥ 90% | **90%** at phone-small, **98%** at the other five |

"Rounded container" is counted as a visible element with a non-zero border
radius *and* a border or background, excluding anything whose radius makes it
a circle or a pill — a status dot is deliberately round and is not a
container. `visualReview.spec.ts` holds the ceiling at 12 on every cell.

## 3. Bundle

Measured by building the branch point, `0d82e87`, in a worktree from the same
lockfile and running `scripts/check-bundle.mjs` against each `dist`:

| | `0d82e87` | `feat/visual-redesign` | delta |
|---|---|---|---|
| `index.css` | 32.89 kB | 41.87 kB | +8.98 kB |
| `index.js` | 316.54 kB | 307.13 kB | −9.41 kB |
| `vega.js` | 840.81 kB | 840.81 kB | same chunk hash |
| **initial payload, gzipped** | **102.20 kB** | **103.72 kB** | **+1.52 kB (+1.5%)** |

The stylesheet grew because seventeen modules of new surfaces replaced seven
panels. The application code shrank, because ten components were deleted and
their replacements are smaller. Vega's content hash is unchanged across the
two builds and it is still behind a dynamic `import()`.

## 4. Deviations from the approved mockups — and what was done

These were found by comparing rendered screens against
`docs/design/mockups/`. All six were acted on. Two are closed in part, and
this says exactly which part.

**D1 — Chart x-axis labels rotated 90° on uploaded datasets. Fixed.**
The mockup shows horizontal labels. The engine's bar specification sets no
`labelAngle`, so Vega applied its own default and turned every label
vertical — four short words at 1920px read sideways — while the
demo-warehouse specifications set `-30` and looked as intended. The product
was rotating labels two different ways depending on which path drew the
chart.

The angle is now decided from the labels: flat for eight or fewer
categories of twelve characters or less, `-30` otherwise. The two failures
are opposite — flat labels on 33 categories collide, and Vega resolves a
collision by *dropping* labels, so a chart that silently loses most of its
axis is worse than one read at an angle. It is applied through
`config.axisX`, which a specification's own `axis.labelAngle` overrides, so
the specifications that already made this decision keep it.
`axisLabels.test.ts` covers the rule; `chart.spec.ts` covers that it reaches
the rendered SVG.

**D2 — The provenance stamp is back on the report footer. Fixed.**
`contract a7c31a · sha 5913f6e · 21 ms`, beside the one control, and it
prints. The earlier reading was that requirement 6 forbids anything
technical on the canvas; that requirement names the panels it is about —
the activity log, the stage list, the planning audit, the DAG — and this is
none of them. It is the identity of what produced the numbers, in the same
register as the dataset strip that names the file, and it is three facts
rather than four so that it answers "can I refer to this run later" without
beginning to explain the run.

It renders nothing rather than a row of placeholders: a stamp reading
`contract — · sha — · — ms` looks like a record and holds none.

**D3 — Chart header: the title is fixed, the rest is declined.**
The title was 13px/500 in `--ink-secondary` — a grey whisper over the
element a reader is most likely to screenshot. It is now a `<figcaption>`
at semibold in `--ink-primary`. `<figcaption>` rather than the `<h4>` it
was, because the document's headings run h1, h2: the h4 was a skipped level
*and* put "total revenue by region" into the heading outline, competing with
"What the numbers show" and "Result" for a reader navigating by heading.

Two parts of the mockup's header are deliberately not implemented:

  - the unit line (`revenue · thousands`) is not derivable. The payload
    carries the measure and a value format, not a scale; "thousands" would
    have to be inferred from the magnitudes, and a unit the product invents
    is worse than no unit on a page whose whole claim is that its numbers
    are traceable.
  - the inline `table · evidence · download` links are not added. "evidence"
    would be a second evidence trigger on the report canvas, which step E
    explicitly forbids — the report states one. The other two would be jump
    links on a single short column, which is the chrome this redesign
    removes.

**D4 — One finding expanded on arrival at phone widths. Fixed, conditionally.**
Implemented where there is something to fold: below 640px, with more than
three findings, the first is shown and the rest fold into a `<details>`
labelled "n more findings". The engine's uploaded path publishes two
one-line rows — "Lowest: North 11,770" — and folding one of those would
hide a line to save a line, so it does not. A recorded run publishes six
full-sentence findings, which is the case the mockup was drawn from, and
those fold.

It is a `<details>` so that it is reachable by keyboard and so that the
print cascade expands it: the paper copy is never the folded one.
`findingFold.test.tsx` pins both sides of the threshold and that a Compare
pane never folds; `report.spec.ts` drives a recorded run at 390px.

**D5 — Chart marks are outlined in print. The fills are still the embedding
theme's.**
A chart embedded while the screen was dark printed `#54becc` on white —
about 2:1, which does not meet WCAG 1.4.11, because the bar's boundary
against the page is not discernible. Neither obvious repair works:
restating the fill in CSS would collapse a multi-series chart to one
colour, since no selector can tell a single-series mark from one of five;
and re-embedding on `beforeprint` is asynchronous and `page.pdf()` never
fires it, so it could not be tested.

What does work in every theme is giving each mark a boundary. Every bar,
point, area and line prints with a hairline in ink, which satisfies 1.4.11
whatever the fill turns out to be, keeps the series distinguishable from
each other, and needs nothing to run at print time. It also improves the
light-theme print, where pale series had the same problem less severely.

**What remains:** the *fills* still carry whichever theme was on screen when
the chart was embedded. The printed chart is legible and compliant; it is
not the light ramp. Making it so means the chart resolving its palette for
the medium it is drawn in, which is a change to how `Chart.tsx` embeds
rather than to how it prints.

**D6 — Compare's diff now carries a counted caption. Fixed.**
"n of m compared contract fields differ; the rest are identical", where `n`
is the array the rows are mapped from and `m` is `COMPARED_FIELD_COUNT`,
the length of the list those rows were selected out of. Neither number is
written beside the table, so neither can drift from it — which is the
failure the requirement was guarding against. `contractDiff.test.tsx` reads
both numbers back out of the rendered caption and compares them with the
rendered rows.

## 5. Defects the review found and fixed

Each of these passed every existing assertion and was visible only in a
rendered artefact.

| Found in | Defect |
|---|---|
| Dark-mode screenshot | Vega's `view.stroke` default `#ddd` drew a near-white rectangle around every plot on a `#0e1113` canvas — the brightest element on the report, in a colour from neither palette. Invisible in light, which is why it survived. Now tokenised, with `chart.spec.ts` asserting that **every** colour in a rendered chart resolves to a palette token |
| PDF page 2 | Every continuation page of a result table lost three of its four column names: the headers are `<button>` because the columns sort, and Chromium does not paint a form control inside a repeated header group |
| PDF appendix | The activity trace printed as an empty bordered box — `animation: stream … both` from `opacity: 0`, restarted by the print layout |
| PDF appendix | The planning audit printed as a heading over nothing: Chromium lays a closed `<details>` out and skips painting it |
| PDF, dark theme | The whole page printed on `#0e1113`: the dark palette is `:root[data-theme="dark"]`, one attribute more specific than the print reset's bare `:root` |
| Phone screenshot | "Nothing was published. The full stop reason is in the evidence." — `stop_reason` is a field name; on screen it reads as a sentence about punctuation |
| Writing D2's test | `PlanningAudit` called `humanize(contract.operation)` unguarded, and read `dimensions` and `filters` without a fallback. A contract missing any of them threw, React unmounted the subtree, and to a reader the evidence drawer closed itself — the same failure that once made the Compare drawer vanish on an unfinished strategy |
| The failure-vocabulary test's own control | A failed run said "Nothing partial has been kept." unconditionally, above the chart, the two highlights and the four-row table its payload still carried. The page contradicted itself in the one state where a reader most needs to trust it. The claim is now made only when it is true |

## 6. How to re-run this

```
# unit, including the stylesheet and print-model suites
npm run test

# the built output
npm run build && npm run check:bundle

# one engine at a time, with the reconciling guard
AAE_E2E_BROWSERS=chromium AAE_E2E_BASE_URL=<url> npx playwright test
AAE_E2E_BROWSERS=chromium AAE_E2E_ALLOWED_SKIPS=0 node scripts/check-playwright-skips.mjs
# firefox and webkit declare 5 skips: page.pdf() is Chromium-only
```

Review artefacts are written to `web/test-results/`: `review/` holds 40
full-page screenshots (six widths × two themes for a real report, plus every
terminal state and Compare at phone and desktop in both themes), and `pdf/`
holds the five rendered PDFs. Both directories are gitignored and regenerate
from the suite.
