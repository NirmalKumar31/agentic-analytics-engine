# Principal audit corrections — release evidence

This record describes the locally verified implementation commit `77627cc`,
based on `main` at `cd83683`. It is evidence for a proposed change, not proof
that the change is deployed. At the time of this record the branch had not
been pushed, merged, tagged or deployed, and no paid or external model call
had been made.

## What changed

### Time semantics fail closed

A year or trend no longer inherits any lone date column merely because it is
the only date available. Generic event clocks such as `order_date` can be the
default. Lifecycle dates such as `signup_date` must be named explicitly. The
same policy governs the deterministic resolver and validation of typed cloud
plans; an AI proposal cannot supply business-clock meaning absent from the
question and schema.

### Aggregate denominators are explicit

Aggregate SQL now records both population rows (`COUNT(*)`) and contributing
non-null measure values (`COUNT(measure)`). Coverage SQL uses the same row
predicate as result SQL, including exclusion of null trend axes. Lineage,
presentation scope, caveats and browser copy preserve the distinction. If
rows match but the requested measure is entirely null, the run completes as
no findings and explains why; it does not publish a null aggregate or report
an execution failure.

`value_count` remains in the result evidence and CSV export. It is not
repeated as a visible business-table column; the scope and coded caveat state
the effective observation count. This keeps the denominator auditable without
forcing the assembled phone layout wider than the viewport.

### Build identity is observable

Health, configuration and run payloads now expose `build_sha`, sourced from
`AAE_BUILD_SHA` or Render's `RENDER_GIT_COMMIT`. The package version still
names the release line; the SHA identifies the exact source revision. The
planning audit displays the revision when one is available.

### Claims and limits were corrected

The README now distinguishes the deterministic canonical-upload fast path
from the richer multi-task path, where models may propose findings and judge
semantic support subject to deterministic vetoes. Resource documentation now
matches the checked-in public Render configuration and the measured capacity
rehearsal. Browser documentation reflects three-engine CI. The limitations
also state that the build is audited but is not hermetic, reproducible or
SLSA-attested while action and image tags remain mutable.

## Verification on the implementation tree

| Gate | Result |
| --- | --- |
| Python, exact CI coverage command | **2,212 passed**, **89% branch coverage** |
| Frontend unit | **457 passed** across 26 files |
| Frontend typecheck | clean |
| Frontend production build | clean; Vega remains a separately emitted lazy chunk |
| Chromium assembled application | **96 passed**, 0 skipped, 0 flaky |
| Firefox assembled application | **95 passed**, 1 declared PDF skip, 0 flaky |
| WebKit assembled application | **95 passed**, 1 declared PDF skip, 0 flaky |
| Ruff lint and format | clean |
| mypy application / scripts | clean / clean |
| `git diff --check` | clean |

Every browser run first confirmed `provider_mode: fake` against the actual
server. The golden report test exercised 360, 390, 768, 1024, 1440 and 1920
pixel widths. The first Chromium attempt found a real phone overflow caused
by exposing the new evidence column; the final three-engine results above are
after the evidence/presentation separation fixed it.

## What this does not prove

- No new real-model evaluation was run. Existing paid evidence is not evidence
  that arbitrary future model outputs or phrasings will be handled correctly.
- No production deploy, production restart or production load test was part of
  this branch verification.
- The test corpus is broad but finite. It does not prove correctness for every
  schema, locale, date convention, missing-data mechanism or business meaning.
- The accessibility gate reports its configured axe scope and browser
  scenarios; it is not a WCAG 2.2 AA conformance claim.
- Missing measure values are disclosed, not imputed. The engine cannot infer
  whether data is missing at random or whether a total should include an
  unknown amount.
- The event-clock allow-list is deliberately conservative. An uncommon but
  valid date name may require the reader to name the column explicitly.
- Supply-chain scanning and dependency locks reduce risk but do not provide a
  hermetic build, signed provenance or a vulnerability-free guarantee.

