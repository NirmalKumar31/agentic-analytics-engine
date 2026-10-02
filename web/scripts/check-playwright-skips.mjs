import { readFileSync } from 'node:fs'

import { GateError, parseBrowsers, summarise } from './e2eGate.mjs'

/**
 * Fail unless the requested engines actually executed tests.
 *
 * A skipped browser test reads as a pass in a summary line, and that has
 * already cost real coverage here three times: a full run once reported
 * "9 skipped, 29 passed" after the upload-session pool was exhausted; a
 * mutation run reported "3 passed" that was actually 6 skipped, briefly
 * making a mutation look caught when nothing had executed; and a shell
 * quoting bug set the browser selection to "chromium 0", ran **zero**
 * tests, and this script said "0 skipped, as declared" and exited 0.
 *
 * The third one is why the check no longer counts skips alone. The
 * reasoning lives in ./e2eGate.mjs, which is also what the Playwright
 * config parses the selection with, so a run and its gate cannot disagree
 * about which engines were asked for.
 */

const path = process.argv[2] ?? 'playwright-results.json'
const declared = process.env.AAE_E2E_ALLOWED_SKIPS ?? '0'
const allowedSkips = /^\d+$/.test(declared.trim()) ? Number(declared) : Number.NaN

let requested
try {
  requested = parseBrowsers(process.env.AAE_E2E_BROWSERS)
} catch (reason) {
  console.error(reason instanceof GateError ? reason.message : String(reason))
  process.exit(1)
}

let report
try {
  report = JSON.parse(readFileSync(path, 'utf8'))
} catch (reason) {
  console.error(
    `Could not read the Playwright report at ${path}: ${reason.message}\n` +
      'Without it there is no evidence any test ran, which is a failure.',
  )
  process.exit(1)
}

const { ok, lines, errors } = summarise(report, { requested, allowedSkips })

if (!ok) {
  console.error(`The browser run did not prove what it needed to (${path}):`)
  for (const problem of errors) console.error(`  - ${problem}`)
  process.exit(1)
}

for (const line of lines) console.log(line)
