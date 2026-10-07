# Redesign brief — design review package

**Historical design input.** This was the approval package measured against
`5913f6e`; its present-tense descriptions record that earlier interface. The
redesign was later implemented. Current production evidence is in
[`../RELEASE-EVIDENCE-production-2026-10-06.md`](../RELEASE-EVIDENCE-production-2026-10-06.md).

Measured against the deployed service at `5913f6e`. Every number below was
taken from the live site, not estimated.

---

## 1. Why this is a redesign and not a cleanup

The previous programme replaced the palette, split one stylesheet into twelve
modules, fixed contrast, charts, tables and terminal states, and added
information architecture. That work was real and is not being discarded.

It did not change the **composition**. The deployed report is still a vertical
stack of bordered, rounded, ALL-CAPS-labelled panels, and it is identical at
every width:

| | 1440px | 768px | 390px |
|---|---|---|---|
| panels | 7 | 7 | 7 |
| bordered elements | 65 | 65 | 65 |
| rounded containers | 99 | 99 | 99 |
| ALL-CAPS headings | 20 | 20 | 20 |
| disclosures (`<details>`) | **0** | **0** | **0** |
| page height | 4,565px | 4,615px | **5,539px** |

Two findings worth stating plainly:

- **Nothing is progressively disclosed on the report.** All seven panels are
  permanently open.
- **`ANALYSIS` appears twice** as a panel heading on the same page. That is an
  information-architecture defect, not a matter of taste.

## 2. Before / after page hierarchy

**Today — pipeline first**

```
header → 5-step stepper
  REPORT              question, counts, print
  KEY FINDINGS        6 identical cards, each SUPPORTED + CALCULATED + "Show work →"
  CHARTS              4 charts, each in its own bordered card
  ANALYSIS            finding chips
  LIMITATIONS AND NEXT QUESTIONS
  ANALYSIS            ← duplicate heading: agent DAG diagram
  STAGES              5 more cards
  ACTIVITY            MCP trace log
```

**Proposed — answer first**

```
thin header (dataset identity when one exists)
  question
  ANSWER                      one sentence, display scale
  context line                population · observations · period · coverage
  CHART                       full content width
  findings 1–4                headline · key driver · exception · caveat
  result table                sticky header, aligned numerals, CSV
  [ Show work → ]             everything technical, behind one control
```

Everything removed from the canvas is **relocated, not deleted**: contract,
route, build SHA, coverage, verification, cited cells, timings, planner
fallback and the activity trace all live in the evidence drawer.

## 3. Component ownership

| Replace | Reuse behind a new surface | Untouched |
|---|---|---|
| `ReportView` (392) · `ExecutionLanes` (305) · `ExecutionFlow` (278) · `RightRail` (186) · `WorkflowIndex` (101) | `Chart` · `ResultPanel` · `ProvenanceDrawer` · `ActivityLog` · `PlanningAudit` · `RoleConfirmation` | every resolver, contract, verification, provenance and MCP path |

New components: `DatasetContextBar`, `SchemaInspectorDrawer`, `StrategySelector`,
`RunTimeline`, `AnswerHeader`, `InsightNarrative`, `ChartWorkspace`,
`EvidenceDrawer`, `CompareWorkspace`, `TerminalState`.

## 4. Wireframes

| # | State | Sheet |
|---|---|---|
| 1 | Landing / no dataset | `wireframes/01-landing.svg` |
| 2 | Dataset ready / composition | `wireframes/02-composer.svg` |
| 3 | Active run | `wireframes/03-active-run.svg` |
| 4 | Successful report | `wireframes/04-report.svg` |
| 5 | Compare strategies | `wireframes/05-compare.svg` |
| 6 | Terminal states (six) | `wireframes/06-terminal-states.svg` |
| 7 | Mobile report | `wireframes/07-mobile-report.svg` |

Each sheet carries desktop and mobile side by side plus numbered notes giving
layout regions, what disappears from the default canvas, disclosure contents,
focus behaviour and motion.

## 5. Visual system

`visual-system.svg`. Typography, light and dark palettes, the chart ramp,
geometry and motion principles.

**Measured contrast** — every foreground against every surface:

| | light | dark |
|---|---|---|
| ink on surfaces | 14.98 – 18.19 | 14.62 – 17.15 |
| secondary ink | 6.47 – 7.86 | 7.35 – 8.62 |
| muted | 4.82 – 5.21 | 5.63 – 6.61 |
| signal | 5.03 – 6.10 | 7.67 – 9.01 |
| warning | 5.29 – 6.42 | 5.63 – 6.61 |

Every pair clears 4.5:1 for body text. One candidate muted tone measured
**4.30** on the inset surface and was darkened to `#5C666F` rather than
waived.

**The chart ramp is reused, not replaced.** It was verified against
Viénot/Brettel dichromacy simulation with CIE76 ΔE separation. Replacing it
would discard proven work for a preference.

## 6. Acceptance criteria

### Hierarchy, not height

An earlier draft of this brief set "mobile report ≤ 2,200px" as a target. That
was wrong and is withdrawn. A report's height legitimately depends on the
number of rows, the length of the question, chart label length and how many
findings were verified. A height ceiling rewards truncating real content,
which is the opposite of what this product is for.

What is tested instead, at 360 and 390:

1. The answer and its context line are visible **above the first viewport
   break**, without scrolling.
2. The chart begins within the first reading sequence — no intervening panel.
3. Exactly **one** finding is expanded on arrival; the rest are collapsed.
4. No technical material is resident: no activity log, no stage list, no
   planning audit, no DAG on the default canvas.
5. The table is contained in its own scroll frame; the page never scrolls
   sideways.
6. No duplicated pipeline panel appears (today `ANALYSIS` appears twice).
7. The evidence drawer is reachable by keyboard and returns focus to its
   trigger on Escape.

### Disclosure architecture

The earlier "≥ 3 disclosures" target is also withdrawn — it is satisfiable by
adding meaningless disclosures. These are the disclosures, by name and
content. No others are added to reach a count:

| Disclosure | Surface | Contains |
|---|---|---|
| Dataset and schema inspector | Side sheet from the context bar | Field list, types, roles, null and distinct counts, ambiguity marks, role confirmation |
| Method and route | Inline, under the context line | Which strategy ran, whether a model call occurred, why that route was chosen |
| Evidence and provenance | Edge sheet (desktop) / bottom sheet (mobile) | Contract, canonical hash, build SHA, coverage, verification outcomes, cited cells, timings, planner fallback, activity trace |
| Findings 2–4 | Inline, mobile only | The ranked findings after the headline |
| Planning audit | **Inside** the evidence sheet only | The full typed-plan record |

### Terminal states — one per run

The composite sheet `terminal-*.svg` shows two states side by side. That is
documentation. **A run reaches exactly one terminal state, and the page renders
exactly that one.** There is no surface on which two terminal states coexist.

Each state below has its own mockup pair, its own status string taken from
`RunOutcome` in `src/agentic_analytics/graph/runner.py` or `RunRecord.status`,
and its own required test state.

| State | Status reported by the API | Mockup | Distinguishing affordance |
|---|---|---|---|
| Refused | `refused` | `state-refused-{light,dark}.svg` | Two concrete re-ask buttons — the refusal is a question and the answer is one click away |
| No findings | `completed` | `state-no-findings-{light,dark}.svg` | **No action button.** Nothing was decided wrongly, so there is nothing to retry |
| Verification withheld | `completed` | `state-verification-withheld-{light,dark}.svg` | The computed result is kept and marked *not interpreted*; each withheld claim is named with its `verdict_rule` |
| Quota stopped | `budget_exhausted` | `state-quota-stopped-{light,dark}.svg` | Recorded spend, plus a free deterministic route offered |
| Failed | `failed` | `state-failed-{light,dark}.svg` | Run reference for the server log, and a retry |
| Cancelled | `cancelled` | `state-cancelled-{light,dark}.svg` | Re-upload, and wording that keeps a withdrawn input apart from an engine failure |

Three rules these sheets encode:

1. **Severity lives in the rule bar, never in the primary button.** A recovery
   action rendered in the warning colour reads as the dangerous thing on the
   page when it is the way out of it. Every primary button is the signal
   colour regardless of state.
2. **A completed run never borrows failure vocabulary.** "No findings" and
   "verification withheld" both report `completed`; neither says *failed*,
   *error* or *problem*. The consistency audit enforces this — it rejected an
   earlier draft that said "both failed verification" on a completed run.
3. **A state with nothing to decide gets no button.** An action that only
   restates the state teaches people to ignore the buttons that matter.

### Compare — one evidence action

Two persistent per-strategy evidence buttons were replaced by a single
**Inspect both traces** control opening one drawer with a tab per strategy.
Two buttons implied two destinations and made the reader pick a side before
reading anything.

The space that reclaims is used, not held empty for symmetry:

| Outcome | Mockup | What occupies the reclaimed space |
|---|---|---|
| Strategies agree | `compare-{light,dark}.svg` | **Why this counts as agreement** — the accepted contract, the canonical hash, and the coverage both runs matched. Agreement that is asserted but not specified is a slogan |
| Strategies differ | `compare-diff-{light,dark}.svg` | The **structured difference**, field by field, differing rows marked. Not two results side by side, which invites picking the preferred number |
| Either | `compare-evidence-{light,dark}.svg` | The drawer itself: edge sheet on desktop, bottom sheet on mobile, two tabs, page visible behind |

The divergence sheet states plainly that **neither result is presented as the
answer**, and the agree/differ counts in its caption are computed from the diff
table rather than written by hand.

Differences are computed over the canonical contract, which excludes
`explanation`, `interpretation`, `named_columns`, `planner_note` and `issues`.
Wording that differs between two narrations is not a disagreement and is not
reported as one.

### Comparative measures

| Measure | Today | Target |
|---|---|---|
| panels on report | 7 | 0 |
| ALL-CAPS headings | 20 | 0 |
| rounded containers | 99 | ≤ 12 |
| duplicated panel headings | 1 (`ANALYSIS`) | 0 |
| page horizontal overflow | 0 | 0 (unchanged) |
| chart width share of content column | ~88% | ≥ 90% at all six widths |

## 7. Test requirements

These measure **layout behaviour**, not element existence — the weakness the
current suite has.

1. Answer and context above the first viewport break at 360 and 390.
2. Chart bounding box ≥ 90% of content width at each of the six widths.
3. `document.scrollWidth - clientWidth ≤ 1` in every state.
4. Table contained: scrolls within its own wrapper, never the page.
5. Exactly one finding expanded on arrival at mobile widths.
6. No element matching the resident-technical set on the default report canvas.
7. No duplicated panel heading text anywhere in one view.
8. Evidence drawer traps focus; Escape closes and returns focus to the trigger.
9. Compare matrix remains readable at 390px.
10. `prefers-reduced-motion` honoured by every effect in the storyboard.
11. axe on landing, upload, active run, report, compare, refusal, no-findings,
    evidence-open and mobile.
12. Chromium, Firefox and WebKit under the existing reconciling guard.
13. Bundle delta measured; Vega stays lazy-loaded.
14. **One terminal state per run.** For each of the six states, a test drives
    the run to that outcome and asserts the page renders that state's headline
    and status, and that no other terminal state's headline is in the
    document. Six tests, not one parametrised existence check.
15. No element on a `completed` terminal state matches the failure vocabulary
    set (`failed`, `error`, `went wrong`, `problem`).
16. Compare renders exactly one evidence trigger; opening it mounts a tabbed
    drawer with one tab per strategy, and switching tabs issues no request.
17. On divergence, the structured diff is present and its agree/differ caption
    equals the counts in the table it labels.
18. The design-package consistency audit (`generators/audit_mockups.py`) runs
    green — it is what keeps the sheets from contradicting each other.

## 8. Print / PDF — a first-class output

`print.css` (233 lines) encodes the current panel structure and the browser-PDF
test asserts against it. Replacing the shell **will** break both. That is
in-scope work, not a surprise.

The printed report is not a screenshot of the screen. It is the same argument
in a fixed medium:

```
question
ANSWER                      display scale, unchanged
context line                population · observations · period · coverage
CHART                       vector, full page width, annotation retained
findings 1–4                all four expanded — print has no disclosure
full result table           every row, repeating header across pages
─────────────────────────── page break
APPENDIX: evidence          contract · canonical hash · build SHA · coverage
                            verification outcomes · cited cells · timings
                            planner fallback · activity trace
```

**The evidence drawer becomes a visible appendix.** Technical truth cannot
vanish from a document merely because the screen hid it behind a control. Any
disclosure that is collapsed on screen is expanded in print.

The ambient field, the run timeline and all motion are omitted. The product
mark appears once, in the page header.

## 9. Visual signature

Defined in `MOTION-STORYBOARD.md`: the product mark, the contour motif and
where it is permitted, the single display-scale typographic signature for the
headline answer, chart annotation styling, and the theme transition.

## 10. Deliverables in this package

| | |
|---|---|
| Wireframes, 7 states | `wireframes/*.svg` |
| Core mockups, light and dark | `mockups/{landing,report}-{light,dark}.svg` |
| Compare: agreement, divergence, evidence drawer | `mockups/compare-{,diff-,evidence-}{light,dark}.svg` |
| Terminal states, six, one per sheet | `mockups/state-{refused,no-findings,verification-withheld,quota-stopped,failed,cancelled}-{light,dark}.svg` |
| Terminal-state composite (documentation only) | `mockups/terminal-{light,dark}.svg` |
| Visual system with measured contrast | `visual-system.svg` |
| Motion and signature storyboard | `MOTION-STORYBOARD.md` |
| Generators and the consistency audit | `generators/` |

Every sheet is generated, not drawn: `generators/` regenerates all 32 SVGs and
`generators/audit_mockups.py` checks them against each other. The audit
currently runs **300 checks over 32 sheets**. It has already rejected two real
defects — a Compare sheet whose caption claimed five agreeing fields over a
table with four, and a completed run described with failure vocabulary.

## 11. What this package deliberately does not do

- No production frontend code.
- No change to analytics computation, contracts, provenance, API semantics,
  routing, security boundaries or budget controls.
- No claim that the redesign is done. It is not started.
