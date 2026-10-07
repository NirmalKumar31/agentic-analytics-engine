# Release evidence: the frontend programme (PRs F–J)

What was built, what it fixed, and what each claim rests on. Written to be
checkable: every figure below came from a run, and where something is
unverified it says so.

Measured on `8e385fc`.

> Historical evidence. Later releases added cross-browser CI, schema-role
> confirmation, a fake-provider browser preflight and further browser-harness
> checks. For the current closeout evidence, see
> [RELEASE-EVIDENCE-schema-confirmation.md](RELEASE-EVIDENCE-schema-confirmation.md).

---

## What shipped

| PR | merge | what |
|---|---|---|
#27 | `e7f008f` | **F**: cross-browser and accessibility gate |
#28 | `434836a` | **G**: thirteen component boundaries out of `App.tsx` |
#29 | `5180381` | **H**: twelve CSS modules, palette bridge retired |
#30 | `2f7606a` | **I-a**: information architecture |
#31 | `15953d5` | a query that matched nothing is not a query that failed |
#32 | `eaae8be` | dark-mode text contrast, and a gate that measures it |
#33 | `7f41850` | the demo report said the analysis failed |
#34 | `5506de6` | **I-b**: an entirely new palette |
#35 | `5b89046` | "Compare both", and a disagreement announced mid-run |
#36 | `8e385fc` | a colour-blind-safe chart series ramp |

## Before and after

| | before | after |
|---|---|---|
`App.tsx` | 914 lines | **209** |
Components | 16 | **31** |
Stylesheet | one file, 2,234 lines | **12 modules** + tokens |
Legacy palette references | 199 across 26 names | **0** |
Frontend tests | 197 | **380** |
Python tests | 2,078 | **2,097** |

## The defects this programme fixed

Nine, none of which CI caught, because the suite asserted that elements
existed rather than that the words were true.

1. **Every demo run said the analysis had failed.** The presentation builder
   needs an `aggregate_for_question` snapshot; the demo resolves through the
   metric registry and produces none, while still setting `query_mapping`.
   The guard returned early only when *both* were missing, so a FAILURE
   presentation replaced a verified figure, "Revenue by region: Midwest
   $1,918,803.05; …", with "The analysis could not be completed.", and the
   workflow index showed Verify complete beside it.
2. **A filter matching no rows was reported as a failure.** The SQL ran and
   the table held nothing matching; `_canonical_for` returned None and the
   run fell through to a reason `_outcome_from_reason` does not recognise.
   Two shapes: grouped queries return no rows, ungrouped ones return a single
   row with a NULL measure and `row_count: 0`. The first fix handled only
   the grouped case.
3. **Dark mode rendered warning text at 2.92:1.** `--action-text` and
   `--warning-text` were defined only in the light palette, and CSS inherits:
   an undefined override is not an absent colour, it is the wrong one.
4. **A refusal was labelled "Verified answer."** Nothing was verified.
5. **The stop reason appeared three times**: headline, Notes, and "The run
   stopped early". The builder sets a refusal's headline and caveat from one
   sentence; a fourth source was added when `RunStateCard` landed.
6. **The state card sat below the execution panels**, because the report is
   lifted with `order: -1` and the card is a sibling.
7. **The header still said "Compare both."** The rename covered the selector,
   the button and the heading; the badge's label lives in a lookup table, so
   reading the JSX did not show it.
8. **Compare announced "Different governed interpretations" mid-run.** A run
   in flight has no contract, and a missing contract was read as a differing
   one. The two planners went on to produce the identical figure.
9. **Three of five chart series colours were hardcoded** for a palette that
   no longer existed, unchanged in dark mode, and survived a full palette
   replacement untouched.

Four of these were found in a single pass by rendering the principal states
and looking at them. Two more were found by one paid Compare run against a
real model, because a stubbed AI run arrives complete and both defects lived
in the window where it is not.

## Gates that are now enforced rather than documented

| gate | what it measures | size |
|---|---|---|
`contrast.test.ts` | every text token against all four surfaces, both themes, 4.5:1; UI colours at 3:1 | 87 |
`chartSeries.test.ts` | series marks at 3:1 against the plot surface; pairwise separation under three dichromacies | 23 |
`cssArchitecture.test.ts` | import order, no retired aliases, critical rules in their modules | 22 |
`informationArchitecture.test.tsx` | ambiguity shown honestly, six terminal states distinct, auto route never invented | 39 |

The contrast gate found a live defect on its first run, and then caught a
test of mine that claimed to scan dark mode and did not.

## Evidence at `8e385fc`

- Python **2,097 tests**; ruff, ruff format, mypy (95 source files, 7
  scripts) clean.
- Frontend **380 tests**; typecheck and production build clean.
- Browser: **Chromium 73 passed, zero skipped; Firefox 72; WebKit 72**, each
  with one declared Chromium-only PDF skip and the skip guard satisfied per
  engine.
- **Twelve axe scans**, zero serious or critical, now including dark mode.
- Every local run forced `AAE_PROVIDER_MODE=fake AAE_LIVE_ANALYTICS_ENABLED=true`,
  with `provider_mode: fake` confirmed from `/api/health` first.

### The one paid run

`gpt-6-luna` via cloud, one governed Compare on the demo warehouse: 9,779
input / 2,353 output tokens, 1 finding published, 5 withheld, agreeing
exactly with the deterministic lane. It validated the automatic-route
disclosure against a real `contract_resolved` event and found defects 7 and
8 above.

## No WCAG conformance claim

What is true: zero serious or critical axe violations across twelve scanned
states on three engines, and every text token measured at or above 4.5:1 on
every surface in both themes. That is a set of requirements checked, not
conformance.

## Open at this historical snapshot, and why

- **`prefers-color-scheme` is dead code.** `useTheme` writes `data-theme` on
  every render and defaults to light, so `:root:not([data-theme="light"])`
  can never match and a reader whose system is dark gets light until they
  press the control. Defaulting to light is deliberate; a media block that
  can never match is not. Changing first-load behaviour is a design
  decision, so it was left rather than quietly altered.
- **The schema-role override** was deferred at this snapshot in ADR 0006.
  It was later implemented as session-scoped confirmation in
  [ADR 0007](adr/0007-session-scoped-role-confirmation.md); the underlying
  age-versus-Store ambiguity remains a fact values alone cannot settle.
- **One unexplained `color-contrast` violation** at `tr:nth-child(1) > .key`
  in a single loaded WebKit run. `.key` is vega-tooltip's markup, which this
  project does not style. Not reproducible in three isolated runs or a clean
  full-suite re-run. Nothing was excluded from the scan to make it pass.

## Deployment

`main` is ahead of what is serving. Deploying is the owner's step, and
`merged` and `live` are different claims; see `docs/DEPLOYMENT.md`.

Worth one deploy rather than several: the information architecture, the new
palette, the series ramp and nine defect fixes land together.
