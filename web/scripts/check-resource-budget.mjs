/**
 * What the browser suite cost the server, as one job-wide budget.
 *
 * CI runs three engines against one container, and that container holds
 * three ceilings this suite can exhaust:
 *
 *   max_active_upload_sessions   24, which the workflow now sets
 *                                explicitly. It did not, and this was
 *                                the ceiling nothing named
 *   uploads_per_ip_per_hour      200, shared by all three engines because
 *                                they run from one address inside one hour
 *   analyses_per_ip_per_hour     200, shared the same way
 *
 * All three were reached on PR #46, and nothing in the suite could say by
 * how much: the figures had to be reconstructed afterwards from rejection
 * messages in a CI log. Chromium died with "the demo is at capacity for
 * uploaded datasets"; Firefox and WebKit then died on the hourly
 * allowances.
 *
 * So the suite counts itself, at the HTTP boundary, and this reads the
 * result. It is a script rather than a test because the budget is a
 * property of the *run* and not of any one case, and because it has to fail
 * a build rather than be a number somebody remembers.
 *
 * No measured totals live in this file. They were here, and they went
 * stale the first time the suite changed, and worse, one of them was a
 * prediction rather than a measurement. The numbers belong in
 * `e2e/UPLOADS.md`, next to the run they came from.
 *
 * ------------------------------------------------------------ what it reads
 *
 * `playwright-ledger/<engine>.jsonl`, one JSON object per line, outside
 * `test-results/` because Playwright clears its output directory at the
 * start of every run and the three engines are three runs against one
 * container. A ledger kept there can only ever report the last engine's
 * numbers, which is the opposite of what a shared ceiling needs.
 *
 *   upload    a POST /api/datasets/upload was attempted
 *   open      ...and the server returned a session, by id
 *   close     ...and that session was handed back, by id
 *   request   a billed POST left the page, with a sequence number
 *   response  ...and came back, with a status and a source
 *   capture   a real server response was remembered for replay, by sha
 *   fixture   a payload committed to the repository was registered, by sha
 *   refused   the server answered 429
 *
 * An earlier version inferred "real analysis" from a client-side marker
 * that a spec could leave stale, and did: real analyses were recorded as
 * free replays and the guard passed a job that was over its budget. Nothing
 * here consults test intent. A response carrying the suite's own
 * `x-aae-e2e-analysis-source` header was answered by the suite; one without
 * it reached the container. That is the whole classification.
 */

import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

const args = process.argv.slice(2)
/*
 * Two scopes, because the facts have two scopes.
 *
 * `engine` is everything one invocation can know on its own: a 429 it was
 * served, a session it did not hand back, its own totals. Those are
 * reported the moment that engine finishes, by `scripts/e2e.mjs`, because
 * waiting for two more engines before saying "Chromium leaked four
 * sessions" wastes the twenty minutes in between.
 *
 * `job` is the rest: the totals against ceilings the three engines share,
 * and the roll-call of engines that were expected to write. Neither can be
 * judged until the last engine has finished, so CI runs this scope once,
 * after all three, with `if: always()`.
 */
const SCOPE = (() => {
  const flag = args.indexOf('--scope')
  if (flag === -1) return 'job'
  const value = args[flag + 1]
  if (value !== 'engine' && value !== 'job') {
    console.error(`--scope takes "engine" or "job", not ${JSON.stringify(value)}`)
    process.exit(1)
  }
  args.splice(flag, 2)
  return value
})()
const LEDGER = args[0] ?? 'playwright-ledger'

/**
 * The job this ledger is supposed to describe.
 *
 * The ledger outlives a Playwright invocation on purpose, and the price is
 * that a file left by an earlier job is indistinguishable from this job's
 * unless every line says who wrote it. A stale `chromium.jsonl` would
 * otherwise satisfy the engine roll-call while describing nothing that
 * happened.
 */
const JOB = process.env.AAE_E2E_JOB_ID ?? null

/**
 * The engines this job is expected to produce ledgers for.
 *
 * An engine that ran but wrote nothing is indistinguishable from an engine
 * that never ran, and both are a hole in the accounting, so a selection
 * naming three engines that leaves two ledgers is a failure, not a smaller
 * number.
 */
const EXPECTED = (process.env.AAE_E2E_LEDGER_ENGINES ?? '')
  .split(',')
  .map((name) => name.trim())
  .filter((name) => name !== '')

/*
 * Headroom, not targets. Each sits above what the suite measures, so an
 * ordinary addition does not fail the build, and far below the server
 * ceiling it protects, so a regression does.
 */
/** Upload attempts per engine. */
const MAX_UPLOADS_PER_ENGINE = 22
/** Across the job, against `uploads_per_ip_per_hour` of 200. */
const MAX_UPLOADS_PER_JOB = 60
/** Live at any instant, against `max_active_upload_sessions` of 24. */
const MAX_LIVE_SESSIONS = 8
/** Server-backed admissions per engine, against 200/hour shared by three. */
const MAX_ADMISSIONS_PER_ENGINE = 26
/**
 * Across the job, against `analyses_per_ip_per_hour` of 200.
 *
 * 78, not 199. The ceiling is shared by three engines inside one hour and
 * the suite has to leave room for a spec added next month, for a failure
 * that re-runs nothing but still costs what it spent, and for the margin
 * between "passed" and "nearly refused". A budget set at the ceiling
 * reports success right up to the moment CI goes red.
 *
 * Three times the per-engine allowance, so the per-engine rule is the one
 * that normally fires, which is the right way round, because a single
 * engine drifting upwards is a change somebody made, while the job total
 * is the thing the container actually enforces.
 */
const MAX_ADMISSIONS_PER_JOB = 78

/** The values a response's source header may carry. Anything else fails. */
const SOURCES = new Set(['server', 'capture', 'committed', 'harness'])

if (!existsSync(LEDGER)) {
  console.error(
    `No ledger at ${LEDGER}. Either no browser test ran, or the suite ` +
      "stopped recording -- both of which leave the job's resource cost " +
      'unmeasured, which is the state this check exists to end.',
  )
  process.exit(1)
}

const files = readdirSync(LEDGER).filter((name) => name.endsWith('.jsonl'))
if (files.length === 0) {
  console.error(`The resource ledger at ${LEDGER} is empty.`)
  process.exit(1)
}

const events = []
for (const file of files) {
  for (const line of readFileSync(join(LEDGER, file), 'utf8').split('\n')) {
    if (line.trim() === '') continue
    try {
      events.push(JSON.parse(line))
    } catch {
      console.error(`Unreadable ledger line in ${file}: ${line.slice(0, 120)}`)
      process.exit(1)
    }
  }
}

events.sort((a, b) => a.at - b.at)

const problems = []
const jobs = new Set()
const ids = new Set()
const byEngine = new Map()
const captures = new Map()
let live = 0
let peak = 0

const row = (engine) => {
  if (!byEngine.has(engine)) {
    byEngine.set(engine, {
      uploads: 0,
      opens: 0,
      closes: 0,
      live: 0,
      peak: 0,
      sessions: new Map(),
      requests: new Map(),
      responses: new Map(),
      admissions: 0,
      replays: 0,
      harness: 0,
      refused: 0,
      refusals: [],
      perTest: new Map(),
    })
  }
  return byEngine.get(engine)
}

for (const event of events) {
  // ---------------------------------------------------------- identity
  if (typeof event.job !== 'string' || event.job === '') {
    problems.push(
      'a ledger record does not say which job wrote it, so it cannot be ' +
        `told apart from a leftover: ${JSON.stringify(event).slice(0, 160)}`,
    )
    continue
  }
  jobs.add(event.job)

  if (typeof event.id !== 'string' || event.id === '') {
    problems.push(
      `a ledger record has no id: ${JSON.stringify(event).slice(0, 160)}`,
    )
    continue
  }
  if (ids.has(event.id)) {
    problems.push(
      `two ledger records share the id ${event.id}, so one of them is a ` +
        'duplicate and every total computed from them is wrong',
    )
    continue
  }
  ids.add(event.id)

  const engine = event.project
  if (!engine) {
    problems.push(
      `an event could not be attributed to an engine: ${JSON.stringify(event).slice(0, 160)}`,
    )
    continue
  }
  const r = row(engine)

  switch (event.event) {
    case 'upload':
      r.uploads += 1
      break

    case 'open': {
      r.opens += 1
      r.live += 1
      live += 1
      const session = event.session
      if (typeof session !== 'string' || session === '') {
        problems.push(
          `${engine} opened a session with no id, so it cannot be ` +
            'reconciled against the close that was supposed to return it',
        )
        break
      }
      if (r.sessions.has(session)) {
        problems.push(`${engine} opened session ${session} twice`)
      }
      r.sessions.set(session, 'open')
      break
    }

    case 'close': {
      r.closes += 1
      r.live -= 1
      live -= 1
      const session = event.session
      if (typeof session !== 'string' || session === '') {
        problems.push(`${engine} closed a session without naming it`)
        break
      }
      const state = r.sessions.get(session)
      if (state === undefined) {
        problems.push(
          `${engine} closed session ${session}, which it never opened -- so ` +
            'the counts reconcile while the sessions do not',
        )
      } else if (state === 'closed') {
        problems.push(`${engine} closed session ${session} twice`)
      } else {
        r.sessions.set(session, 'closed')
      }
      break
    }

    case 'request': {
      if (typeof event.seq !== 'string' || event.seq === '') {
        problems.push(`${engine} recorded a billed request with no sequence`)
        break
      }
      if (r.requests.has(event.seq)) {
        problems.push(`${engine} recorded sequence ${event.seq} twice`)
      }
      r.requests.set(event.seq, event)
      break
    }

    case 'response': {
      if (typeof event.seq !== 'string' || event.seq === '') {
        problems.push(
          `${engine} recorded a response with no sequence, so it cannot be ` +
            'matched to the request that caused it',
        )
        break
      }
      if (r.responses.has(event.seq)) {
        problems.push(
          `${engine} recorded two responses for sequence ${event.seq}`,
        )
        break
      }
      r.responses.set(event.seq, event)

      const source = event.source
      if (!SOURCES.has(source)) {
        problems.push(
          `${engine} recorded a response from an unrecognised source ` +
            `${JSON.stringify(source)}. A source that is neither the ` +
            'container nor a registered fixture cannot be put on either ' +
            'side of the budget.',
        )
        break
      }

      const test = event.test ?? 'unattributed'
      const perTest = r.perTest.get(test) ?? { admissions: 0, replays: 0, harness: 0 }
      if (source === 'server') {
        r.admissions += 1
        perTest.admissions += 1
      } else if (source === 'harness') {
        r.harness += 1
        perTest.harness += 1
      } else {
        /*
         * Served from a registered payload: the container was never asked
         * to compute anything, so it costs nothing against the analysis
         * ceiling. Counted anyway, because "how much of this suite is
         * replay" is worth being able to answer, and a fall in replays
         * with a matching rise in admissions is the regression this file
         * exists to catch.
         */
        r.replays += 1
        perTest.replays += 1
        if (typeof event.capture !== 'string' || event.capture === '') {
          problems.push(
            `${engine} replayed a ${source} payload without naming it, so ` +
              'there is nothing to check it against',
          )
        } else {
          r.replayed ??= new Set()
          r.replayed.add(`${source}:${event.capture}`)
        }
      }
      r.perTest.set(test, perTest)
      break
    }

    case 'capture':
    case 'fixture': {
      const sha = event.sha
      if (typeof sha !== 'string' || sha === '') {
        problems.push(`${engine} registered a payload with no sha`)
        break
      }
      const from = event.from
      if (from !== 'capture' && from !== 'committed') {
        problems.push(
          `${engine} registered payload ${sha} with provenance ` +
            `${JSON.stringify(from)}, which is neither a capture of a real ` +
            'response nor a payload committed to the repository',
        )
        break
      }
      captures.set(`${engine}:${from}:${sha}`, event)
      break
    }

    case 'refused':
      /*
       * A 429 from the server. Not a budget *estimate*; the server
       * stating that a ceiling was reached, which is the condition every
       * other number here is trying to stay away from. Counted as spend:
       * a refused admission was still an admission attempt.
       */
      r.refused += 1
      r.refusals.push(event.detail ?? 'unknown')
      break

    default:
      problems.push(`unknown ledger event "${event.event}"`)
  }

  const current = byEngine.get(engine)
  if (current) {
    current.peak = Math.max(current.peak, current.live)
  }
  peak = Math.max(peak, live)
}

// ----------------------------------------------------------- job identity
if (jobs.size > 1) {
  problems.push(
    `the ledger mixes ${jobs.size} jobs (${[...jobs].join(', ')}). One of ` +
      'them is a leftover, and the totals below belong to neither run.',
  )
}
if (JOB !== null) {
  for (const seen of jobs) {
    if (seen !== JOB) {
      problems.push(
        `the ledger holds records from job "${seen}" while this is job ` +
          `"${JOB}" -- stale data from an earlier run, which would make this ` +
          'budget a statement about something that is not being tested.',
      )
    }
  }
} else if (jobs.has('local-unpinned')) {
  console.log(
    '  note: AAE_E2E_JOB_ID is unset, so a ledger left by an earlier local ' +
      'run cannot be told from this one. CI always sets it.',
  )
}

// ------------------------------------------------------------ per engine
console.log(`Resource budget (${SCOPE} scope):`)
let totalUploads = 0
let totalAdmissions = 0
let totalReplays = 0

for (const [engine, r] of byEngine) {
  console.log(
    `  ${engine}: ${r.uploads} upload attempts, ${r.opens} sessions, ` +
      `${r.closes} returned, peak ${r.peak} live, ${r.live} left open, ` +
      `${r.admissions} admissions, ${r.replays} replayed, ` +
      `${r.harness} orchestrated, ${r.refused} refused`,
  )
  totalUploads += r.uploads
  totalAdmissions += r.admissions
  totalReplays += r.replays

  if (r.refused > 0) {
    problems.push(
      `${engine} was refused ${r.refused} request(s) with 429: ` +
        `${[...new Set(r.refusals)].join('; ')}. A ceiling was reached, which ` +
        'no amount of passing assertions makes acceptable.',
    )
  }

  // ------------------------------------------- request/response pairing
  for (const seq of r.requests.keys()) {
    if (!r.responses.has(seq)) {
      const request = r.requests.get(seq)
      problems.push(
        `${engine} sent billed request ${seq} (${request.path ?? '?'} in ` +
          `${request.test ?? 'an unknown test'}) and recorded no response. ` +
          'An admission with no outcome is an admission the budget cannot ' +
          'account for.',
      )
    }
  }
  for (const seq of r.responses.keys()) {
    if (!r.requests.has(seq)) {
      problems.push(
        `${engine} recorded a response for sequence ${seq} with no request`,
      )
    }
  }

  // ------------------------------------------------- replays vs captures
  for (const reference of r.replayed ?? []) {
    const [from, sha] = reference.split(':')
    if (!captures.has(`${engine}:${from}:${sha}`)) {
      problems.push(
        `${engine} replayed ${from} payload ${sha}, which was never ` +
          'registered. A replay that references nothing is a free analysis ' +
          'the budget never saw.',
      )
    }
  }

  // --------------------------------------------- harness orchestration
  for (const [test, counts] of r.perTest) {
    if (counts.harness > 0 && counts.admissions === 0 && counts.replays === 0) {
      problems.push(
        `${engine}'s "${test}" synthesised ${counts.harness} orchestrated ` +
          'response(s) and neither admitted anything nor replayed anything, ' +
          'so whatever it stood for went unrecorded',
      )
    }
  }

  // --------------------------------------------------------- the budget
  if (r.admissions > MAX_ADMISSIONS_PER_ENGINE) {
    problems.push(
      `${engine} admitted ${r.admissions} analyses, over the ` +
        `${MAX_ADMISSIONS_PER_ENGINE} allowed per engine (the container ` +
        'allows 200 per IP per hour, shared by all three)',
    )
  }
  if (r.uploads > MAX_UPLOADS_PER_ENGINE) {
    problems.push(
      `${engine} made ${r.uploads} upload attempts, over the ` +
        `${MAX_UPLOADS_PER_ENGINE} allowed per engine`,
    )
  }
  if (r.peak > MAX_LIVE_SESSIONS) {
    problems.push(
      `${engine} held ${r.peak} upload sessions at once, over the ` +
        `${MAX_LIVE_SESSIONS} allowed (the container holds 24, and a run ` +
        'that approaches it fails with "the demo is at capacity for ' +
        'uploaded datasets")',
    )
  }
  if (r.live !== 0) {
    problems.push(
      `${engine} left ${r.live} upload session(s) open. A closed browser ` +
        'does not free one, so these outlive the engine and count against ' +
        'the next.',
    )
  }
  for (const [session, state] of r.sessions) {
    if (state === 'open') {
      problems.push(`${engine} never handed session ${session} back`)
    }
  }

  /*
   * Non-vacuity. An engine that uploaded datasets and admitted nothing did
   * not run a cheap suite. It ran a suite whose accounting stopped
   * working, which is exactly the failure this file was rewritten for. The
   * floor is 1 rather than a measured number so that a genuine reduction
   * does not have to move it.
   */
  if (r.uploads > 0 && r.admissions === 0) {
    problems.push(
      `${engine} attempted ${r.uploads} uploads and recorded no admission ` +
        'at all. A suite that asks the engine nothing is not a cheaper ' +
        'suite; it is a suite that is no longer counting.',
    )
  }

  // ------------------------------- per-test sums must equal the engine's
  const summed = [...r.perTest.values()].reduce(
    (total, counts) => total + counts.admissions,
    0,
  )
  if (summed !== r.admissions) {
    problems.push(
      `${engine}'s per-test admissions sum to ${summed} but the engine ` +
        `recorded ${r.admissions}`,
    )
  }
}

// -------------------------------------------------------------- job wide
if (SCOPE === 'job') {
  for (const name of EXPECTED) {
    if (!byEngine.has(name)) {
      problems.push(
        `${name} was expected in the ledger and is not there. An engine ` +
          'that wrote nothing is indistinguishable from one that never ran.',
      )
    }
  }
}

console.log(
  `  ${SCOPE === 'job' ? 'job' : 'so far'}: ${totalUploads} upload attempts, ` +
    `peak ${peak} live, ${totalAdmissions} admissions, ${totalReplays} replayed`,
)

if (SCOPE === 'job') {
  if (totalAdmissions > MAX_ADMISSIONS_PER_JOB) {
    problems.push(
      `${totalAdmissions} admissions across the job, over the ` +
        `${MAX_ADMISSIONS_PER_JOB} allowed (the container allows 200 per IP ` +
        'per hour for all engines together)',
    )
  }
  if (totalUploads > MAX_UPLOADS_PER_JOB) {
    problems.push(
      `${totalUploads} upload attempts across the job, over the ` +
        `${MAX_UPLOADS_PER_JOB} allowed (the container allows 200 per IP per ` +
        'hour, shared by all three engines)',
    )
  }
  if (peak > MAX_LIVE_SESSIONS) {
    problems.push(
      `${peak} upload sessions were live at once, over the ` +
        `${MAX_LIVE_SESSIONS} allowed`,
    )
  }
}

if (problems.length > 0) {
  console.error('The browser suite exceeded its resource budget:')
  for (const problem of problems) console.error(`  - ${problem}`)
  process.exit(1)
}
console.log(
  SCOPE === 'job'
    ? 'Within budget, every session was handed back, and every replay named ' +
        'a payload that was registered.'
    : 'This engine is within budget; the job-wide totals are checked after ' +
        'the last engine.',
)
