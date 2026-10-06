# Design sheet generators

These scripts reproduce the historical redesign package. They do not capture
or certify the current interface; use the
[production audit](../../RELEASE-EVIDENCE-production-2026-10-06.md) for that.

Every sheet in `docs/design` is generated. Nothing here is hand-drawn, which is
why a fact can be checked across sheets instead of trusted.

```
./docs/design/generators/build.sh      # from the repo root
```

| File | Emits |
|---|---|
| `mock.py` | the shared drawing library: palettes, the product mark, type, charts, tables, the header |
| `wf.py` | `wireframes/01..07` |
| `sheets.py` | `mockups/landing-*`, `mockups/report-*`, `mockups/terminal-*` (the documentation composite) |
| `compare.py` | `mockups/compare-*` — agreement, divergence, evidence drawer |
| `states.py` | `mockups/state-*` — the six terminal states, one per sheet |
| `render.mjs` | SVG → PNG at `deviceScaleFactor: 2`, at each sheet's natural size |
| `audit_mockups.py` | the cross-sheet consistency audit |

## The audit is the point

`audit_mockups.py` is what keeps the package honest. It checks that every sheet
is well-formed XML, that no `&` escaped unescaped, that a dataset named on two
sheets carries the same row count, that the build SHA is one real value, that
each terminal-state sheet reports exactly one status, that Compare carries
exactly one evidence trigger, and that the divergence caption matches the table
it labels.

It has already rejected two real defects:

- a Compare caption claiming five agreeing fields over a table with four;
- a `completed` run described as having "failed verification", which borrows
  the vocabulary of the `failed` terminal status for a run that did not fail.

Both were wrong in the way that matters: they would have been copied into
production code by whoever implemented from the sheet.
