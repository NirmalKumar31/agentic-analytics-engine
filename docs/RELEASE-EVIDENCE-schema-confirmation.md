# Release evidence: schema-role confirmation and documentation closeout

This document records the evidence at the merge of the session-scoped
schema-role confirmation work. It separates what was executed, what was not,
and what changed only in documentation.

**Measured implementation SHA:** 8ac05fc896f07e7495eff79d5a2fc980ecd925d8  
**Scope:** PR K safe-local boundary; PR L governed schema-role confirmation.  
**Deployment status at measurement:** source and CI evidence only. A merge is
not a statement that Render is serving the same SHA.

## What the feature proves

A person can settle an ambiguous inferred numeric upload field as a quantity
or category for that one session. The effective schema is consumed by the API,
planner, MCP profile and aggregation tools; a completed run preserves the
schema revision and role evidence it started with.

The endpoint refuses stale revisions and changes during an active run. Compare
children share one captured confirmation snapshot. A confirmation does not
make the engine infer business truth, and it does not make a quantity
automatically additive.

## Evidence

| Gate | Result |
| --- | --- |
| Python | 2,194 passed; 89% branch coverage |
| Targeted role semantics | 81 tests across semantic, session, API, execution, corpus and in-flight boundaries |
| Frontend unit | 453 passed |
| Browser: Chromium | 96 discovered, 96 passed, 0 skips |
| Browser: Firefox | 96 discovered, 95 passed, 1 declared PDF skip |
| Browser: WebKit | 96 discovered, 95 passed, 1 declared PDF skip |
| Browser accounting | every discovered test reconciled; no failed, timed-out, interrupted, unattributable or retry-rescued flaky test accepted |
| Accessibility | serious/critical axe violations: 0 in both role-control states on all three engines |
| Mutations | 19 effective mutations caught; 2 behaviour-equivalent mutations excluded rather than counted |
| CI | all 10 jobs green on the exact merge SHA |

The browser harness made a fake-provider health preflight part of every
server-backed run. It refuses a server reporting cloud mode before launching
a browser; no provider request was made while producing this evidence.

## The browser-stability correction

Firefox exposed a Playwright navigation lifecycle issue across multiple,
unrelated tests: traces showed the page shell fully rendered and all known
requests returning 200 while lifecycle waiting still timed out. Tests now
navigate in page context and wait for the application-owned shell marker
instead of treating browser load events as application readiness. The harness
also rejects zero-test selection, undeclared/missing browser projects,
mismatched skip allowances and retry-rescued flakes. Firefox and WebKit run
even if an earlier browser step fails, so their evidence is not silently
skipped.

This is test-harness evidence, not a claim about browser behaviour for every
network or deployment configuration.

## Documentation and visual closeout

The README was rewritten to describe the default governed route, explicit
planning audit paths, schema-role confirmation and the actual verification
boundary. The architecture atlas now lives in the repository with editable
SVG and PNG views, source mapping and icon attribution.

The atlas's three core plates were audited against an earlier source snapshot.
The later role-confirmation boundary is called out explicitly in its README
rather than being implied by artwork that predates it.

The obsolete system-preference palette override was removed. The product
defaults to light by deliberate design and writes its explicit theme state on
mount; the removed media rule could apply a dark first paint before that state
existed. Explicit dark mode remains implemented by the data-theme selector.

The Vega Tooltip dependency supplies a low-contrast default key colour. The
application now overrides its visible panel and key text with measured
semantic tokens, using intentional selector specificity because the dependency
injects its own stylesheet at runtime. This resolves the previously
unexplained tooltip contrast observation at the source instead of excluding it
from automated accessibility scans.

## What this does not establish

- No provider or paid run was made for this release.
- No deployment verification was made for the merge SHA.
- The scripted provider remains evidence of engine policy and execution, not
  general real-model planning quality.
- The source-level tooltip rule is tested, but a complete hover interaction is
  not part of the stable automated accessibility suite; Vega owns the markup
  and interaction timing.
- WCAG conformance is not claimed. The project has targeted contrast,
  keyboard and axe checks, not a complete conformance audit.

For product limits, see [LIMITATIONS.md](LIMITATIONS.md). For the earlier
frontend programme, see
[RELEASE-EVIDENCE-frontend-programme.md](RELEASE-EVIDENCE-frontend-programme.md).

