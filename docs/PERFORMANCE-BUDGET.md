# Performance budget

Observed numbers, and conservative thresholds derived from them. Nothing
here is aspirational: every figure was measured on the commit that
introduced this file, and the thresholds sit far enough above the
observations that normal variation will not trip them.

A tight threshold on a noisy measurement gets disabled within a month and
then protects nothing. These are set to catch a *regression in kind*, such as
a dependency landing on the critical path or the chart bundle becoming eager,
rather than to police a few kilobytes.

Measured: 2026-10-01, production build, local server, Chromium.
Re-measured on `8e385fc`, after the frontend programme (component
extraction, CSS modules, the information architecture, a replaced palette
and the chart series ramp).

---

## What loads, and when

| asset | raw | gzip | when |
|---|---|---|---|
`index.css` | 32.93 kB | 7.59 kB | initial |
`index.js` | 310.16 kB | 95.26 kB | initial |
`vega.js` | 860.99 kB | 295.66 kB | **only when a chart renders** |

**Initial transfer: 343 kB raw / ~103 kB gzip**, across two files.

Up 7 kB raw on the figure this file first recorded. The stylesheet grew by
0.9 kB (a palette with a data-series ramp in two themes) and the entry
chunk by 5.7 kB across thirteen new components and the restructured report.
The architectural claim below is the one that matters, and it is unchanged.

The large chunk is not on the critical path. A network trace of the
landing page requests exactly `index.css` and `index.js`; `vega.js` is
requested only after a report containing a chart appears. That is
`Chart.tsx` doing `await import('vega-embed')` inside its effect.

This is why Vite's "chunk larger than 500 kB" warning is cosmetic here,
and why no code-splitting work is warranted: the thing the warning is
about already does not block first render. Measured before altering, as
the brief asked.

## Report render

**153 ms** from submitting the question to the report panel being visible,
against a local server with the scripted provider and a 200-row fixture.

This number is a *floor*, not a user-facing promise. It excludes network
latency to a hosted service, cold starts on the free tier (~50 s), and any
provider call. It is useful only as a regression signal on the same
fixture and the same machine.

## Thresholds

Deliberately loose, for the reason given above.

| budget | threshold | observed | headroom |
|---|---|---|---|
Initial transfer (raw) | **450 kB** | 343 kB | 31% |
Initial file count | **4** | 2 | — |
Vega on the critical path | **must not happen** | it does not | — |
Report render, same fixture | **1,000 ms** | 153 ms | 6.5× |

The third is the one that matters most and is the only binary one. The
others are size ceilings; that one is a statement about architecture, and
crossing it would mean every visitor downloads 861 kB to read a number.

## What is not measured

Stated so that silence is not read as success.

- **No Lighthouse score, and none is claimed.** The figures above are
  transfer sizes and one timing, not a performance grade.
- **Cumulative layout shift.** Not measured. A threshold on it would be
  guesswork without a stable capture harness, and a flaky budget is worse
  than none.
- **Long tasks and CPU time.** Not measured.
- **Hosted timings.** Everything here is local. The deployed service runs
  on a free tier that cold-starts in roughly 50 seconds, which dominates
  any figure in this document.
- **Firefox and WebKit timings.** The browser suite runs on all three
  engines; these measurements were taken on Chromium only.

## How to re-measure

```bash
cd web && rm -rf dist && npm run build     # the table of asset sizes
```

For the load pattern and the render timing, the method is a Playwright
response listener that records every `.js`/`.css` request and whether it
arrives before or after the report is visible. It is not kept as a
committed test: a timing assertion on a shared machine is the definition
of a flaky gate. The architectural claim, that Vega is not on the critical
path, *is* worth asserting, and
`e2e/foundation.spec.ts` already does it by failing if the landing page
makes any request beyond the two expected files.
