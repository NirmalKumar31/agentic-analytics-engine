/**
 * Run the browser suite, then prove the run proved something.
 *
 * `npm run test:e2e` was `playwright test && node check-playwright-skips.mjs`,
 * and the `&&` is the bug: Playwright's non-zero exit short-circuits it, so
 * the guard never runs on exactly the runs that most need checking. On PR
 * #46 the browser job failed with 71 Chromium failures and the guard
 * produced no verdict at all: no reconciliation, no skip accounting, no
 * flake check. The one thing the pipeline could say was "something failed".
 *
 * So: the two halves run unconditionally, their outcomes are reported
 * separately, and the exit status is non-zero if *either* failed. There is
 * no `&&`, no `|| true`, no `set -e` subtlety and no `grep | head` in the
 * path, because every one of those can turn a failure into a silence.
 *
 * The stale-report problem is handled before Playwright starts. The report
 * is deleted first, so a crash that writes nothing cannot leave the
 * previous run's JSON for the guard to approve, which has happened here
 * once already, with `--reporter=line` overriding the config's JSON
 * reporter and the guard reading a file from the run before.
 */

import { spawnSync } from 'node:child_process'
import { existsSync, rmSync, statSync } from 'node:fs'

/*
 * Per engine, matching `playwright.config.ts`. The three engines ran into
 * one `playwright-results.json`, so the guard for the third read a file the
 * first two had already been judged on, and the artifact uploaded after a
 * failure held only the last engine's traces.
 */
const ENGINE = (process.env.AAE_E2E_BROWSERS ?? '').split(',')[0]?.trim() || 'all'
const REPORT =
  process.env.PLAYWRIGHT_JSON_OUTPUT_NAME ?? `playwright-results-${ENGINE}.json`

/** Delete the previous report, so a crashed run cannot pass on an old one. */
if (existsSync(REPORT)) {
  rmSync(REPORT)
}
const startedAt = Date.now()

const forwarded = process.argv.slice(2)
const playwright = spawnSync('npx', ['playwright', 'test', ...forwarded], {
  stdio: 'inherit',
  env: process.env,
})

const playwrightStatus =
  playwright.status === null
    ? `killed by ${playwright.signal ?? 'an unknown signal'}`
    : `exit ${playwright.status}`
const playwrightOk = playwright.status === 0

/*
 * The report must be newer than the run that was supposed to write it. A
 * file left by an earlier run is not evidence about this one, and the
 * delete above only covers the case where the file existed, and a reporter
 * misconfigured to write somewhere else would leave a *stale* file at the
 * expected path that the delete never saw.
 */
let stale = false
if (existsSync(REPORT)) {
  stale = statSync(REPORT).mtimeMs < startedAt
}

const guard = spawnSync('node', ['scripts/check-playwright-skips.mjs', REPORT], {
  stdio: 'inherit',
  env: process.env,
})

/*
 * And the resource budget, also unconditionally.
 *
 * The two guards answer different questions: "did every test run and
 * reconcile" and "what did the run cost the server", and a failing run
 * needs both answered. A job that fails on 71 assertions *and* exhausted
 * the analysis ceiling has two problems, and reporting one of them sends
 * the next hour in the wrong direction.
 *
 * `--scope engine`: everything one invocation can know by itself, such as a 429
 * it was served, a session it did not hand back, its own totals. The
 * job-wide totals and the engine roll-call need the last engine to have
 * finished, so CI runs the same script once more with `--scope job` after
 * all three. Reporting the engine's own cost here rather than waiting
 * means a Chromium leak is named before Firefox and WebKit spend another
 * ten minutes on top of it.
 */
const budget = spawnSync(
  'node',
  ['scripts/check-resource-budget.mjs', '--scope', 'engine'],
  {
    stdio: 'inherit',
    env: process.env,
  },
)

const guardOk = guard.status === 0 && !stale
const budgetOk = budget.status === 0

if (stale) {
  console.error(
    `The report at ${REPORT} is older than this run. It describes a ` +
      'different execution, so the guard above judged the wrong evidence.',
  )
}

console.log('')
console.log(`Playwright: ${playwrightOk ? 'passed' : `FAILED (${playwrightStatus})`}`)
console.log(`Report guard: ${guardOk ? 'passed' : 'FAILED'}`)
console.log(`Resource budget: ${budgetOk ? 'passed' : 'FAILED'}`)

process.exit(playwrightOk && guardOk && budgetOk ? 0 : 1)
