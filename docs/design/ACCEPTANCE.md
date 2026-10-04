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
| 5 | Exactly one finding expanded on arrival at mobile widths | **Not implemented — see Deviation D4.** What is asserted instead: no finding is truncated and none is behind a disclosure |
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
| 17 | On divergence, the structured diff's caption equals its table | The diff itself: `contractDiff.test.tsx` (15 cases), `compare.spec.ts`. **The counted caption does not exist — see Deviation D6** |
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
| `index.css` | 32.89 kB | 42.09 kB | +9.20 kB |
| `index.js` | 316.54 kB | 312.01 kB | −4.53 kB |
| `vega.js` | 840.81 kB | 840.81 kB | same chunk hash |
| **initial payload, gzipped** | **102.20 kB** | **102.75 kB** | **+0.55 kB (+0.5%)** |

The stylesheet grew because seventeen modules of new surfaces replaced seven
panels. The application code shrank, because ten components were deleted and
their replacements are smaller. Vega's content hash is unchanged across the
two builds and it is still behind a dynamic `import()`.

## 4. Deviations from the approved mockups

These were found by comparing rendered screens against
`docs/design/mockups/`. None is fixed; each is recorded so the decision is
the owner's rather than mine.

**D1 — Chart x-axis labels rotate 90° on uploaded datasets.**
The mockup shows horizontal labels. The engine's bar specification sets no
`labelAngle`, so Vega applies its own default and rotates every label to
vertical, even for four short words at 1920px. The demo-warehouse
specifications set `-30` and look as the mockup intends, so the product is
inconsistent between its two paths. Not fixed here: the correction is a
client-side `config.axisX.labelAngle`, which is a product decision affecting
every chart and has a real cost at high cardinality — with labels horizontal,
Vega drops the ones that collide rather than rotating them, and a
33-category chart would silently lose most of its axis.

**D2 — No provenance stamp on the report footer.**
The mockup shows `contract a3f9c1 · sha 5913f6e · 412 ms` beside "Show work".
The implementation moved all of it into the evidence drawer. This is a direct
conflict between the mockup and the brief's own requirement 6, which forbids
resident technical material on the default canvas; the requirement won. If
the stamp is wanted back, requirement 6 needs an explicit exception rather
than a quiet one.

**D3 — Chart header treatment.**
The mockup gives the chart a bold title, a unit line (`revenue · thousands`)
and inline `table · evidence · download` links. The implementation renders a
small muted caption and moves those affordances to the table footer and the
actions row. The unit line is not currently derivable from the payload.

**D4 — Findings are not collapsed on mobile.**
Requirement 5 assumes the mockup's four multi-sentence findings, of which
three are collapsed on a phone. The engine publishes one-line label/value
rows — "Lowest: North 11,770" — so collapsing one would hide a line to save a
line. The requirement is unexercised rather than violated, and the claim
underneath it is asserted instead: on a phone no finding is truncated and
none is behind a disclosure.

**D6 — Compare states its verdict in words, not in counts.**
The brief asks that the divergence caption's agree/differ counts equal the
rows in the table it labels. The implementation has no counted caption: the
verdict block says `contracts identical · coverage identical · output
identical`, carried on a machine-readable `data-verdict`, and the diff table
below names each differing field. There is no number to reconcile, so the
requirement is unmet by construction rather than untested. If counts are
wanted, they should be derived from the diff rows rather than written beside
them, which is what the requirement was guarding against.

**D5 — A chart drawn while the screen was dark keeps the dark series ramp
when printed.**
Vega resolves `--series-*` at embed time and writes the result inline on the
SVG, so no print rule can reach it: a single-series bar prints as `#54becc`
on white, about 2:1, which does not meet WCAG 1.4.11 for a graphical object.
Not fixed because both available fixes are worse than the defect — restating
the fill in CSS would collapse a multi-series chart to one colour, since no
selector can tell the two cases apart, and re-embedding on `beforeprint` is
asynchronous and `page.pdf()` never fires it, so the fix could not be tested.
The reasoning is also in `web/src/styles/print.css`.

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
