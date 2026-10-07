/**
 * The wrapper must run the guard even when Playwright fails.
 *
 * `.check.mjs`, not `.test.mjs`: vitest's default glob claims `*.test.*`
 * anywhere in the project, and it cannot run this. It is a plain Node
 * script that spawns processes, not a vitest suite. Named this way it
 * stays out of vitest's way and keeps its own CI step.
 *
 * That is the whole point of it: `playwright test && node check-skips.mjs`
 * never reached the guard on a failing run, which is the run whose evidence
 * matters most. These drive the real script with a stub `npx` and a stub
 * guard on PATH, so what is tested is the wrapper's control flow rather
 * than a description of it.
 */

import { strict as assert } from 'node:assert'
import { spawnSync } from 'node:child_process'
import { chmodSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const WRAPPER = resolve('scripts/e2e.mjs')
const GUARD = resolve('scripts/check-playwright-skips.mjs')
const BUDGET = resolve('scripts/check-resource-budget.mjs')

let failures = 0
const tests = []
function test(name, fn) {
  tests.push([name, fn])
}

/**
 * Run the wrapper in a scratch directory with a fake `npx`.
 *
 * `playwright` is replaced by a shell script that writes whatever report
 * the case wants and exits with whatever status the case wants, so every
 * combination of (Playwright passed/failed) x (report good/bad) is
 * reachable without running a browser.
 */
function run({
  playwrightExit = 0,
  report = null,
  reportAge = 'fresh',
  allowedSkips = '0',
  ledger = 'clean',
}) {
  const dir = mkdtempSync(join(tmpdir(), 'e2e-wrapper-'))
  mkdirSync(join(dir, 'bin'))
  mkdirSync(join(dir, 'scripts'))

  // Per engine, as `scripts/e2e.mjs` now names it: the three engines used
// to overwrite one another's evidence.
const reportPath = join(dir, 'playwright-results-chromium.json')
  /*
   * The stale case backdates the file the stub just wrote. A report cannot
   * simply be left over -- the wrapper deletes it before starting, so the
   * failure being modelled is a reporter that puts an older document at the
   * expected path *during* the run, which is what `--reporter=line`
   * overriding the config's JSON reporter did here once.
   */
  const backdate =
    reportAge === 'stale' ? `touch -t 202001010000 ${JSON.stringify(reportPath)}\n` : ''
  const writes =
    report === null
      ? ''
      : `cat > ${JSON.stringify(reportPath)} <<'EOF'\n${JSON.stringify(report)}\nEOF\n${backdate}`
  writeFileSync(
    join(dir, 'bin', 'npx'),
    `#!/bin/sh\n${writes}exit ${playwrightExit}\n`,
  )
  chmodSync(join(dir, 'bin', 'npx'), 0o755)

  // The real guard, reached by the path the wrapper uses.
  writeFileSync(
    join(dir, 'scripts', 'check-playwright-skips.mjs'),
    `import('${GUARD}')\n`,
  )
  /*
   * And the real budget guard, likewise. The wrapper runs both on every
   * invocation, so a sandbox that stubbed one of them would prove the
   * wrapper reaches a script rather than that it reaches the guard.
   * `import()` leaves `process.argv` alone, so `--scope engine` arrives
   * at the real argument parser.
   */
  writeFileSync(
    join(dir, 'scripts', 'check-resource-budget.mjs'),
    `import('${BUDGET}')\n`,
  )

  /*
   * A ledger for the run to be judged against. `clean` is two matched
   * sessions; `leak` opens one and never hands it back; `missing` writes
   * nothing, which is the state a suite that stopped recording leaves
   * behind.
   */
  if (ledger !== 'missing') {
    mkdirSync(join(dir, 'playwright-ledger'))
    let id = 0
    const line = (event, at, extra = {}) =>
      JSON.stringify({
        id: `wrapper-check:${(id += 1)}`,
        event,
        job: 'wrapper-check',
        project: 'chromium',
        at,
        ...extra,
      })
    const admission = (seq, at) => [
      line('request', at, { seq, path: '/api/analyses' }),
      line('response', at + 1, { seq, status: 202, source: 'server', path: '/api/analyses' }),
    ]
    const lines =
      ledger === 'leak'
        ? [line('upload', 1), line('open', 2, { session: 's1' }), ...admission("w1", 3)]
        : [
            line('upload', 1),
            line('open', 2, { session: 's1' }),
            line('close', 3, { session: 's1' }),
            ...admission("w1", 4),
          ]
    writeFileSync(
      join(dir, 'playwright-ledger', 'chromium.jsonl'),
      lines.join('\n') + '\n',
    )
  }

  const result = spawnSync('node', [WRAPPER], {
    cwd: dir,
    encoding: 'utf8',
    env: {
      ...process.env,
      PATH: `${join(dir, 'bin')}:${process.env.PATH}`,
      AAE_E2E_BROWSERS: 'chromium',
      AAE_E2E_ALLOWED_SKIPS: allowedSkips,
      AAE_E2E_JOB_ID: 'wrapper-check',
    },
  })
  rmSync(dir, { recursive: true, force: true })
  return { ...result, output: `${result.stdout}${result.stderr}` }
}

/** A report Playwright would write for `n` tests in the given buckets. */
function reportOf({
  passed = 0,
  skipped = 0,
  failed = 0,
  flaky = 0,
  timedOut = 0,
  interrupted = 0,
  unknown = 0,
  engine = 'chromium',
} = {}) {
  const specs = []
  const add = (status, results, project = engine) =>
    specs.push({
      title: `t${specs.length}`,
      file: 'x.spec.ts',
      line: 1,
      tests: [{ projectName: project, status, expectedStatus: 'passed', results }],
    })
  for (let i = 0; i < passed; i += 1) add('expected', [{ status: 'passed' }])
  for (let i = 0; i < skipped; i += 1) add('skipped', [{ status: 'skipped' }])
  for (let i = 0; i < failed; i += 1) add('unexpected', [{ status: 'failed' }])
  for (let i = 0; i < timedOut; i += 1) add('unexpected', [{ status: 'timedOut' }])
  for (let i = 0; i < interrupted; i += 1)
    add('unexpected', [{ status: 'interrupted' }])
  for (let i = 0; i < unknown; i += 1) add('something-else', [{ status: 'passed' }])
  for (let i = 0; i < flaky; i += 1)
    add('flaky', [{ status: 'failed' }, { status: 'passed' }])
  return { stats: {}, suites: [{ title: 'x.spec.ts', specs }] }
}

// ----------------------------------------------------------------- cases

test('a clean run passes, and says both halves passed', () => {
  const r = run({ playwrightExit: 0, report: reportOf({ passed: 3 }) })
  assert.equal(r.status, 0, r.output)
  assert.match(r.output, /Playwright: passed/)
  assert.match(r.output, /Report guard: passed/)
})

test('the guard still runs when Playwright fails', () => {
  // The defect this script exists for. `&&` skipped the guard here.
  const r = run({ playwrightExit: 1, report: reportOf({ passed: 2, failed: 1 }) })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /Playwright: FAILED/)
  assert.match(r.output, /Report guard: FAILED/)
  assert.match(r.output, /1 test\(s\) failed/)
})

test('a missing report fails even when Playwright says it passed', () => {
  const r = run({ playwrightExit: 0, report: null })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /Could not read the Playwright report/)
})

test('a stale report fails', () => {
  // A crashed run that writes nothing must not be judged on the last run's
  // evidence. The wrapper deletes the report first; this covers the case
  // where one is left behind at the expected path by something else.
  const r = run({
    playwrightExit: 0,
    report: reportOf({ passed: 3 }),
    reportAge: 'stale',
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /older than this run/)
})

test('a report with no tests fails', () => {
  const r = run({ playwrightExit: 0, report: reportOf({}) })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /no tests were discovered/)
})

test('an engine that executed nothing fails', () => {
  const r = run({ playwrightExit: 0, report: reportOf({ skipped: 4 }), allowedSkips: '4' })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /no tests were executed/)
})

test('an undeclared skip fails', () => {
  const r = run({ playwrightExit: 0, report: reportOf({ passed: 3, skipped: 1 }) })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /1 test\(s\) skipped; 0 declared\. more skips/)
})

test('fewer skips than declared fails', () => {
  const r = run({
    playwrightExit: 0,
    report: reportOf({ passed: 3, skipped: 1 }),
    allowedSkips: '2',
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /1 test\(s\) skipped; 2 declared\. fewer skips/)
})

test('more skips than declared fails', () => {
  const r = run({
    playwrightExit: 0,
    report: reportOf({ passed: 3, skipped: 3 }),
    allowedSkips: '2',
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /3 test\(s\) skipped; 2 declared\. more skips/)
})

test('exactly the declared skips passes', () => {
  const r = run({
    playwrightExit: 0,
    report: reportOf({ passed: 3, skipped: 2 }),
    allowedSkips: '2',
  })
  assert.equal(r.status, 0, r.output)
})

test('a flaky test fails, with no allowance', () => {
  // A test that passes only on retry has not demonstrated what it asserts.
  const r = run({ playwrightExit: 0, report: reportOf({ passed: 2, flaky: 1 }) })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /flaky tests are not accepted/)
})

test('a timed-out test fails, and is named as timed out', () => {
  // Distinguished from an ordinary failure on purpose: a timeout usually
  // means something never arrived, which is a different investigation.
  const r = run({ playwrightExit: 1, report: reportOf({ passed: 2, timedOut: 1 }) })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /1 test\(s\) timed out/)
})

test('an interrupted test fails', () => {
  const r = run({ playwrightExit: 1, report: reportOf({ passed: 2, interrupted: 1 }) })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /1 test\(s\) interrupted/)
})

test('a status the gate does not recognise fails rather than being ignored', () => {
  // The accounting invariant: every discovered test lands in exactly one
  // bucket. A test that cannot be placed is a hole in the summary, and a
  // summary with a hole in it is not evidence.
  const r = run({ playwrightExit: 0, report: reportOf({ passed: 2, unknown: 1 }) })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /unrecognised status/)
})

test('an engine in the report that was not requested fails', () => {
  // The skip allowance is per engine. An extra project silently doubles
  // what "5 declared skips" means.
  const report = reportOf({ passed: 2 })
  report.suites[0].specs.push({
    title: 'extra',
    file: 'x.spec.ts',
    line: 2,
    tests: [
      { projectName: 'webkit', status: 'expected', expectedStatus: 'passed', results: [{ status: 'passed' }] },
    ],
  })
  const r = run({ playwrightExit: 0, report })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /webkit appears in the report but was not requested/)
})

test('a test with no engine at all fails', () => {
  const report = reportOf({ passed: 1 })
  delete report.suites[0].specs[0].tests[0].projectName
  const r = run({ playwrightExit: 0, report })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /has no projectName/)
})

test('a requested engine missing from the report fails', () => {
  const report = reportOf({ passed: 2 })
  for (const spec of report.suites[0].specs) spec.tests[0].projectName = 'webkit'
  const r = run({ playwrightExit: 0, report })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /chromium was requested but does not appear/)
})

// ------------------------------------------------------------------ run

test('a passing run that leaked a session still fails', () => {
  // Both guards run on every invocation, and either one failing fails the
  // job. A suite that passes every assertion while leaving an upload
  // session behind has broken the next engine, not succeeded.
  const r = run({ report: reportOf({ passed: 3 }), ledger: 'leak' })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /Resource budget: FAILED/)
  assert.match(r.output, /never handed session s1 back/)
})

test('a run that recorded nothing fails rather than reporting no cost', () => {
  // A missing ledger is not a cheap run. It is an unmeasured one, and the
  // ceilings that took this job down twice are exactly what goes
  // unmeasured.
  const r = run({ report: reportOf({ passed: 3 }), ledger: 'missing' })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /Resource budget: FAILED/)
  assert.match(r.output, /No ledger at/)
})

for (const [name, fn] of tests) {
  try {
    fn()
    console.log(`  ok  ${name}`)
  } catch (reason) {
    failures += 1
    console.error(`FAIL  ${name}`)
    console.error(`      ${reason.message.split('\n')[0]}`)
  }
}
console.log(`${tests.length - failures}/${tests.length} wrapper checks passed`)
process.exit(failures === 0 ? 0 : 1)
