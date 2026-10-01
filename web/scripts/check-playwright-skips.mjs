import { readFileSync } from 'node:fs'

/**
 * Fail when Playwright skipped more tests than were declared.
 *
 * A skipped browser test reads as a pass in a summary line, and that has
 * already cost real coverage here twice: a full run once reported
 * "9 skipped, 29 passed" after the upload-session pool was exhausted, and
 * a mutation run reported "3 passed" that was actually 6 skipped --
 * briefly making a mutation look caught when nothing had executed.
 *
 * The allowance is declared per browser rather than assumed. WebKit and
 * Firefox legitimately skip the browser-PDF test, which calls
 * `page.pdf()` -- a Chromium-only Playwright API. That one is gated in
 * the spec. Any *other* skip is a hole, so the number is exact: a run
 * that skips fewer than declared also fails, because that means the
 * gating changed and the declaration is now wrong.
 */

const path = process.argv[2] ?? 'playwright-results.json'
const allowed = Number(process.env.AAE_E2E_ALLOWED_SKIPS ?? '0')
const browsers = process.env.AAE_E2E_BROWSERS ?? '(default)'
const report = JSON.parse(readFileSync(path, 'utf8'))

function collect(suites, out = []) {
  for (const suite of suites ?? []) {
    for (const spec of suite.specs ?? []) {
      for (const test of spec.tests ?? []) {
        if (test.status === 'skipped') {
          out.push(`${suite.title} > ${spec.title}`)
        }
      }
    }
    collect(suite.suites, out)
  }
  return out
}

const skipped = collect(report.suites)

if (skipped.length !== allowed) {
  const verb = skipped.length > allowed ? 'more' : 'fewer'
  console.error(
    `Playwright skipped ${skipped.length} test(s) on ${browsers}; ` +
      `${allowed} declared. ${verb} skips than declared means browser ` +
      `coverage changed without the declaration changing.`,
  )
  for (const title of skipped) console.error(`  skipped: ${title}`)
  if (Number.isNaN(allowed)) {
    console.error('AAE_E2E_ALLOWED_SKIPS was not a number.')
  }
  process.exit(1)
}

console.log(
  `Playwright skipped ${skipped.length} test(s) on ${browsers}, as declared.`,
)
