import { readFileSync } from 'node:fs'

const path = process.argv[2] ?? 'playwright-results.json'
const report = JSON.parse(readFileSync(path, 'utf8'))

function countSkipped(suites) {
  return suites.reduce((total, suite) => {
    const own = (suite.specs ?? []).reduce(
      (sum, spec) => sum + (spec.tests ?? []).filter((test) => test.status === 'skipped').length,
      0,
    )
    return total + own + countSkipped(suite.suites ?? [])
  }, 0)
}

const skipped = countSkipped(report.suites ?? [])
if (skipped > 0) {
  console.error(`Playwright reported ${skipped} skipped test(s); skipped browser coverage is not acceptable in CI.`)
  process.exit(1)
}
