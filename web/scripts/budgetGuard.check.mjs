/**
 * The resource budget must fail on each condition it exists to catch.
 *
 * `check-resource-budget.mjs` is the only thing standing between this suite
 * and the ceilings that took PR #46's browser job down twice, once on
 * upload sessions, once on analyses. It then passed a job that was over its
 * analysis budget, because the suite's own accounting mislabelled real runs
 * as replays and the guard believed it. A guard whose failure paths are
 * untested is a guard nobody has seen fail, so each one is driven here with
 * a synthetic ledger.
 *
 * `.check.mjs`, not `.test.mjs`: vitest's default glob claims `*.test.*`
 * anywhere in the project and cannot run a script that spawns processes.
 */

import { strict as assert } from 'node:assert'
import { spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const GUARD = resolve('scripts/check-resource-budget.mjs')

let failures = 0
const tests = []
const test = (name, fn) => tests.push([name, fn])

/** Write a ledger and run the guard over it. */
function run(engines, { expected = '', job = 'job-1', scope = 'job' } = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'budget-'))
  const ledger = join(dir, 'ledger')
  mkdirSync(ledger, { recursive: true })

  let at = Date.now()
  let n = 0
  for (const [engine, events] of Object.entries(engines)) {
    const lines = events.map((event) => {
      at += 1
      n += 1
      return JSON.stringify({
        id: event.id ?? `${job}:${engine}:${n}`,
        job: event.job ?? job,
        project: 'project' in event ? event.project : engine,
        detail: event.detail ?? '',
        test: event.test ?? 'a test',
        at,
        ...event,
      })
    })
    writeFileSync(join(ledger, `${engine}.jsonl`), lines.join('\n') + '\n')
  }

  const result = spawnSync('node', [GUARD, ledger, '--scope', scope], {
    encoding: 'utf8',
    env: { ...process.env, AAE_E2E_LEDGER_ENGINES: expected, AAE_E2E_JOB_ID: job },
  })
  rmSync(dir, { recursive: true, force: true })
  return { ...result, output: `${result.stdout}${result.stderr}` }
}

/** `n` upload attempts that each became a session and were handed back. */
const sessions = (n) =>
  Array.from({ length: n }, (_, i) => [
    { event: 'upload' },
    { event: 'open', session: `s${i}` },
    { event: 'close', session: `s${i}` },
  ]).flat()

/** `n` server-backed admissions, each a matched request/response pair. */
const admissions = (n, test = 'a test') =>
  Array.from({ length: n }, (_, i) => [
    { event: 'request', seq: `s${i + 1}`, path: '/api/analyses', test },
    {
      event: 'response',
      seq: `s${i + 1}`,
      status: 202,
      source: 'server',
      capture: null,
      path: '/api/analyses',
      test,
    },
  ]).flat()

/** A registered capture, and `n` replays of it. */
const replays = (sha, n, from = 'capture') => [
  { event: from === 'capture' ? 'capture' : 'fixture', sha, from },
  ...Array.from({ length: n }, (_, i) => [
    { event: 'request', seq: `r${900 + i}`, path: '/api/analyses' },
    {
      event: 'response',
      seq: `r${900 + i}`,
      status: 202,
      source: from,
      capture: sha,
      path: '/api/analyses',
    },
  ]).flat(),
]

// ----------------------------------------------------------------- cases

test('a run inside every budget passes', () => {
  const r = run({ chromium: [...sessions(4), ...admissions(10)] })
  assert.equal(r.status, 0, r.output)
  assert.match(r.output, /Within budget/)
})

test('a real response is an admission however many fixtures are registered', () => {
  // The defect this file was rewritten for: accounting that could be told
  // a real run was free. Source comes from a header the server cannot
  // send, so registering fixtures nearby changes nothing.
  const r = run({
    chromium: [...sessions(1), ...replays('abc123', 3), ...admissions(5)],
  })
  assert.equal(r.status, 0, r.output)
  assert.match(r.output, /5 admissions, 3 replayed/)
})

test('an analysis nobody routed through a helper is still counted', () => {
  // A test that clicks "Run analysis" rather than calling `ask()` produces
  // traffic and nothing else. Traffic is all the guard reads.
  const r = run({
    chromium: [
      ...sessions(1),
      ...admissions(30, 'a test that clicked the button'),
    ],
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /admitted 30 analyses, over the/)
})

test('a fulfilled response with no source header counts as an admission', () => {
  // The safe direction, asserted so it stays safe: an un-headed response
  // is treated as the container's, so a fixture that forgets its header is
  // over-counted rather than free.
  const r = run({
    chromium: [
      ...sessions(1),
      { event: 'request', seq: '1', path: '/api/analyses' },
      { event: 'response', seq: '1', status: 202, source: 'server', path: '/api/analyses' },
    ],
  })
  assert.equal(r.status, 0, r.output)
  assert.match(r.output, /1 admissions/)
})

test('a replay that references a payload nobody registered fails', () => {
  const r = run({
    chromium: [
      ...sessions(1),
      ...admissions(1),
      { event: 'request', seq: '50', path: '/api/analyses' },
      {
        event: 'response',
        seq: '50',
        status: 202,
        source: 'capture',
        capture: 'deadbeef',
        path: '/api/analyses',
      },
    ],
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /replayed capture payload deadbeef, which was never registered/)
})

test('a replay that names no payload at all fails', () => {
  const r = run({
    chromium: [
      ...sessions(1),
      ...admissions(1),
      { event: 'request', seq: '50', path: '/api/analyses' },
      { event: 'response', seq: '50', status: 202, source: 'capture', path: '/api/analyses' },
    ],
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /replayed a capture payload without naming it/)
})

test('a request with no response fails', () => {
  const r = run({
    chromium: [
      ...sessions(1),
      ...admissions(1),
      { event: 'request', seq: '77', path: '/api/analyses', test: 'the lost one' },
    ],
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /sent billed request 77/)
  assert.match(r.output, /the lost one/)
})

test('two responses for one request fail', () => {
  const r = run({
    chromium: [
      ...sessions(1),
      { event: 'request', seq: '1', path: '/api/analyses' },
      { event: 'response', seq: '1', status: 202, source: 'server', path: '/api/analyses' },
      { event: 'response', seq: '1', status: 202, source: 'server', path: '/api/analyses' },
    ],
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /two responses for sequence 1/)
})

test('a duplicated ledger record fails', () => {
  const r = run({
    chromium: [
      ...sessions(1),
      { event: 'request', seq: '1', path: '/api/analyses', id: 'same' },
      { event: 'response', seq: '1', status: 202, source: 'server', id: 'same' },
    ],
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /share the id same/)
})

test('a record with no id fails', () => {
  const r = run({ chromium: [...sessions(1), ...admissions(1), { event: 'upload', id: '' }] })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /has no id/)
})

test('an unrecognised source fails rather than being guessed', () => {
  const r = run({
    chromium: [
      ...sessions(1),
      { event: 'request', seq: '1', path: '/api/analyses' },
      { event: 'response', seq: '1', status: 202, source: 'probably-free' },
    ],
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /unrecognised source/)
})

test('a 429 fails the job, whatever else passed', () => {
  const r = run({
    chromium: [
      ...sessions(2),
      ...admissions(5),
      { event: 'refused', detail: '429 POST /api/analyses' },
    ],
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /refused 1 request\(s\) with 429/)
})

test('a session that is never handed back fails, by id', () => {
  const r = run({
    chromium: [
      { event: 'upload' },
      { event: 'open', session: 'sX' },
      ...admissions(1),
    ],
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /never handed session sX back/)
})

test('closing the same session twice fails even though the counts reconcile', () => {
  // Two opens and two closes, and only one session: the totals balance and
  // the pool does not.
  const r = run({
    chromium: [
      { event: 'upload' },
      { event: 'open', session: 'sA' },
      { event: 'upload' },
      { event: 'open', session: 'sB' },
      { event: 'close', session: 'sA' },
      { event: 'close', session: 'sA' },
      ...admissions(1),
    ],
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /closed session sA twice/)
})

test('an open with no session id fails', () => {
  const r = run({
    chromium: [{ event: 'upload' }, { event: 'open' }, ...admissions(1)],
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /opened a session with no id/)
})

test('an engine that uploaded and admitted nothing fails as vacuous', () => {
  const r = run({ chromium: [...sessions(3)] })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /no longer counting/)
})

test('an orchestrated response that stands for nothing fails', () => {
  // Compare's 202 is synthesised while a real run proceeds behind it. One
  // with no admission and no replay in the same test stands for nothing.
  const r = run({
    chromium: [
      ...sessions(1),
      ...admissions(1, 'another test'),
      { event: 'request', seq: '60', path: '/api/comparisons', test: 'compare' },
      {
        event: 'response',
        seq: '60',
        status: 202,
        source: 'harness',
        path: '/api/comparisons',
        test: 'compare',
      },
    ],
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /stood for went unrecorded/)
})

test('too many admissions for one engine fails', () => {
  const r = run({ chromium: [...sessions(2), ...admissions(30)] })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /admitted 30 analyses, over the/)
})

test('too many admissions across the job fails, even when each engine is fine', () => {
  /*
   * Four engines at twenty each: eighty admissions, and not one engine
   * anywhere near its own allowance of twenty-six.
   *
   * Four rather than three because the job budget is three times the
   * per-engine one, so with three engines the per-engine rule always fires
   * first and this one could never be seen to work. The guard does not
   * care how many engines a ledger holds, which is what makes the case
   * expressible, and the day a fourth engine is added, the job rule is
   * the one that has to hold.
   */
  const r = run({
    chromium: [...sessions(2), ...admissions(20)],
    firefox: [...sessions(2), ...admissions(20)],
    webkit: [...sessions(2), ...admissions(20)],
    chromiumBeta: [...sessions(2), ...admissions(20)],
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /admissions across the job, over the/)
})

test('too many uploads for one engine fails', () => {
  const r = run({ chromium: [...sessions(30), ...admissions(2)] })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /upload attempts, over the/)
})

test('too many live sessions at once fails', () => {
  const opens = Array.from({ length: 12 }, (_, i) => [
    { event: 'upload' },
    { event: 'open', session: `p${i}` },
  ]).flat()
  const closes = Array.from({ length: 12 }, (_, i) => ({
    event: 'close',
    session: `p${i}`,
  }))
  const r = run({ chromium: [...opens, ...closes, ...admissions(2)] })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /upload sessions at once, over the/)
})

test('an expected engine missing from the ledger fails', () => {
  const r = run(
    { chromium: [...sessions(2), ...admissions(5)] },
    { expected: 'chromium,firefox,webkit' },
  )
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /firefox was expected in the ledger/)
  assert.match(r.output, /webkit was expected in the ledger/)
})

test('an unattributable event fails', () => {
  const r = run({
    chromium: [...sessions(1), ...admissions(1), { event: 'upload', project: null }],
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /could not be attributed to an engine/)
})

test('a ledger mixing two jobs fails', () => {
  const r = run({
    chromium: [...sessions(1), ...admissions(2)],
    firefox: [{ event: 'upload', job: 'job-0' }, ...sessions(1), ...admissions(2)],
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /mixes 2 jobs/)
})

test('a ledger left by an earlier job fails rather than being counted', () => {
  const dir = mkdtempSync(join(tmpdir(), 'budget-'))
  const ledger = join(dir, 'ledger')
  mkdirSync(ledger, { recursive: true })
  writeFileSync(
    join(ledger, 'chromium.jsonl'),
    JSON.stringify({ id: 'a', event: 'upload', job: 'job-0', project: 'chromium', at: 1 }) +
      '\n',
  )
  const r = spawnSync('node', [GUARD, ledger], {
    encoding: 'utf8',
    env: { ...process.env, AAE_E2E_JOB_ID: 'job-1' },
  })
  rmSync(dir, { recursive: true, force: true })
  assert.equal(r.status, 1, `${r.stdout}${r.stderr}`)
  assert.match(`${r.stdout}${r.stderr}`, /records from job "job-0"/)
})

test('a record that does not name its job fails', () => {
  const dir = mkdtempSync(join(tmpdir(), 'budget-'))
  const ledger = join(dir, 'ledger')
  mkdirSync(ledger, { recursive: true })
  writeFileSync(
    join(ledger, 'chromium.jsonl'),
    JSON.stringify({ id: 'a', event: 'upload', project: 'chromium', at: 1 }) + '\n',
  )
  const r = spawnSync('node', [GUARD, ledger], { encoding: 'utf8' })
  rmSync(dir, { recursive: true, force: true })
  assert.equal(r.status, 1, `${r.stdout}${r.stderr}`)
  assert.match(`${r.stdout}${r.stderr}`, /does not say which job wrote it/)
})

test('an unreadable ledger line fails rather than being skipped', () => {
  const dir = mkdtempSync(join(tmpdir(), 'budget-'))
  const ledger = join(dir, 'ledger')
  mkdirSync(ledger, { recursive: true })
  writeFileSync(join(ledger, 'chromium.jsonl'), 'not json at all\n')
  const r = spawnSync('node', [GUARD, ledger], { encoding: 'utf8' })
  rmSync(dir, { recursive: true, force: true })
  assert.equal(r.status, 1, `${r.stdout}${r.stderr}`)
  assert.match(`${r.stdout}${r.stderr}`, /Unreadable ledger line/)
})

test('no ledger at all fails', () => {
  const r = spawnSync('node', [GUARD, join(tmpdir(), 'definitely-not-here')], {
    encoding: 'utf8',
  })
  assert.equal(r.status, 1, `${r.stdout}${r.stderr}`)
  assert.match(`${r.stdout}${r.stderr}`, /No ledger at/)
})

test('replayed runs cost nothing against the admission ceiling', () => {
  const r = run({ chromium: [...sessions(2), ...admissions(8), ...replays('cafe01', 60)] })
  assert.equal(r.status, 0, r.output)
  assert.match(r.output, /8 admissions, 60 replayed/)
})

test('engine scope does not judge the job roll-call', () => {
  const r = run(
    { chromium: [...sessions(2), ...admissions(5)] },
    { expected: 'chromium,firefox,webkit', scope: 'engine' },
  )
  assert.equal(r.status, 0, r.output)
  assert.match(r.output, /the job-wide totals are checked after the last engine/)
})

test('engine scope does not judge the shared job totals', () => {
  const r = run(
    {
      chromium: [...sessions(2), ...admissions(26)],
      firefox: [...sessions(2), ...admissions(26)],
      webkit: [...sessions(2), ...admissions(26)],
    },
    { scope: 'engine' },
  )
  assert.equal(r.status, 0, r.output)
})

test('engine scope still fails a 429 and still fails a leak', () => {
  const refused = run(
    {
      chromium: [
        ...sessions(2),
        ...admissions(1),
        { event: 'refused', detail: '429 POST /x' },
      ],
    },
    { scope: 'engine' },
  )
  assert.equal(refused.status, 1, refused.output)
  assert.match(refused.output, /429/)

  const leaked = run(
    { chromium: [{ event: 'upload' }, { event: 'open', session: 'q' }, ...admissions(1)] },
    { scope: 'engine' },
  )
  assert.equal(leaked.status, 1, leaked.output)
  assert.match(leaked.output, /never handed session q back/)
})

test('an unrecognised scope is refused rather than guessed', () => {
  const r = spawnSync('node', [GUARD, '--scope', 'everything'], { encoding: 'utf8' })
  assert.equal(r.status, 1, `${r.stdout}${r.stderr}`)
  assert.match(`${r.stdout}${r.stderr}`, /--scope takes "engine" or "job"/)
})

// ------------------------------------------------------------------ run

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
console.log(`${tests.length - failures}/${tests.length} budget checks passed`)
process.exit(failures === 0 ? 0 : 1)
