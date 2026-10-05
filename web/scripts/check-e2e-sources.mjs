/**
 * Static rules the resource accounting depends on, checked before the suite
 * runs rather than inferred from its output afterwards.
 *
 * The budget guard reads what the suite recorded. That is only worth
 * something if the suite cannot issue a billed request the recorder never
 * sees, or answer one in a way the recorder cannot classify. Both are
 * ordinary-looking lines to write:
 *
 *   await page.request.post('/api/analyses', ...)     // not page traffic
 *   await route.fulfill({ status: 202, json: ... })   // no source header
 *
 * The first is invisible to `watchTraffic`, which listens to the page's
 * network events; `route.fetch` and `APIRequestContext` do not produce
 * them. The second is indistinguishable from the container answering.
 * Neither fails anything at runtime -- they just make the number smaller.
 *
 * So they are refused here, by reading the files. A spec that genuinely
 * needs to issue one goes through `ledgerHarnessRequest`, which records it.
 */

import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

const E2E = process.argv[2] ?? 'e2e'
const CONFIG = process.argv[3] ?? 'playwright.config.ts'

/**
 * The files that own the accounting, and may therefore do what the rest
 * may not: `helpers.ts` defines the recorder and the headers,
 * `fixtures.ts` is where `test` is extended and the shared pages are
 * opened, and `compareHelpers.ts` issues the one billed request that has
 * to go through the harness recorder.
 */
const OWNERS = new Set(['helpers.ts', 'fixtures.ts', 'compareHelpers.ts'])

/** A billed endpoint, written as a string anywhere in a request call. */
const BILLED = /['"`][^'"`]*\/api\/(analyses|comparisons)\b/

const problems = []

for (const name of readdirSync(E2E).filter((file) => file.endsWith('.ts'))) {
  if (OWNERS.has(name)) continue
  const text = readFileSync(join(E2E, name), 'utf8')
  const lines = text.split('\n')

  lines.forEach((line, index) => {
    const where = `${E2E}/${name}:${index + 1}`

    /*
     * A POST to a billed endpoint that does not go through the page.
     * `page.request`, `route.fetch` and a bare `request` context all reach
     * the container without producing a page request event.
     */
    if (
      /\b(request|context)\s*\.\s*post\s*\(/.test(line) &&
      BILLED.test(lines.slice(index, index + 5).join('\n'))
    ) {
      problems.push(
        `${where}: posts to a billed endpoint through an API context, which ` +
          '`watchTraffic` cannot see. Use `ledgerHarnessRequest` so the ' +
          'admission is recorded.',
      )
    }
    /*
     * The url is usually on the line after the call, not on it:
     *
     *   const started = await route.fetch({
     *     url: new URL("/api/analyses", page.url()).toString(),
     *
     * which is how `accessibility.spec.ts` kept its own copy of Compare's
     * route handler, issuing a real analysis the recorder never saw, for
     * as long as this rule only read one line at a time.
     */
    if (/route\s*\.\s*fetch\s*\(/.test(line)) {
      const call = lines.slice(index, index + 5).join('\n')
      if (BILLED.test(call)) {
        problems.push(
          `${where}: fetches a billed endpoint from a route handler, which ` +
            'produces no page traffic. Record it with `ledgerHarnessRequest`.',
        )
      }
    }
  })

  /*
   * `test` comes from the fixtures, whose `page` attaches the recorder.
   *
   * Six specs imported it straight from `@playwright/test`, so their pages
   * had no traffic observer and every admission they made was invisible --
   * about eleven per engine, which is a third of the suite's real cost
   * sitting outside the budget it is measured against.
   */
  if (/^import\s*\{[^}]*\btest\b[^}]*\}\s*from\s*['"]@playwright\/test['"]/m.test(text)) {
    problems.push(
      `${E2E}/${name}: imports \`test\` from @playwright/test. Import it ` +
        'from ./fixtures, whose `page` attaches the traffic recorder -- ' +
        'otherwise nothing this spec admits is counted.',
    )
  }

  /*
   * And a page a spec opens itself is not the fixture's page.
   *
   * `browser.newPage()` is the right thing for a describe that owns its
   * own session, and it bypasses the `page` fixture entirely, so the
   * recorder has to be attached by hand.
   */
  lines.forEach((line, index) => {
    if (!/browser\s*\.\s*newPage\s*\(/.test(line)) return
    const following = lines.slice(index + 1, index + 4).join('\n')
    if (!following.includes('watchTraffic')) {
      problems.push(
        `${E2E}/${name}:${index + 1}: opens a page without calling ` +
          '`watchTraffic` on it, so anything it admits goes unrecorded.',
      )
    }
  })

  /*
   * A route that answers a billed endpoint must say so in the response.
   * Checked per handler block rather than per line, because the fulfil is
   * usually several lines below the route it belongs to.
   */
  const handlers = text.matchAll(
    /page\s*\.\s*route\s*\(\s*['"`]([^'"`]*\/api\/(?:analyses|comparisons)[^'"`]*)['"`][\s\S]{0,1200}?\n\s*\}\s*\)/g,
  )
  for (const handler of handlers) {
    const body = handler[0]
    if (!body.includes('fulfill')) continue
    if (!body.includes('SOURCE_HEADER') && !body.includes('fixtureHeaders')) {
      const line = text.slice(0, handler.index).split('\n').length
      problems.push(
        `${E2E}/${name}:${line}: answers ${handler[1]} without declaring a ` +
          'source. Use `fixtureHeaders(payload)`, or the response is ' +
          "indistinguishable from the container's and the run is counted " +
          'as a real admission.',
      )
    }
  }
}

/*
 * And the two settings the whole resource model assumes.
 *
 * `retries` is not a convenience here. The report guard refuses a flaky
 * test outright, so a retry can never turn a job green -- it can only run
 * the test again and spend the budget a second time, with no entry in any
 * plan for what that costs. `workers: 1` is what makes one shared upload
 * session per engine possible at all.
 */
const config = readFileSync(CONFIG, 'utf8')
const retries = /retries\s*:\s*([^,\n}]+)/.exec(config)
if (!retries) {
  problems.push(`${CONFIG}: declares no \`retries\`, so Playwright's default applies`)
} else if (retries[1].trim() !== '0') {
  problems.push(
    `${CONFIG}: sets retries to ${retries[1].trim()}. A retry re-issues every ` +
      'admission the test made, and nothing in the resource budget accounts ' +
      'for a second attempt. Update the budget and this check together, or ' +
      'leave it at 0.',
  )
}
const workers = /workers\s*:\s*([^,\n}]+)/.exec(config)
if (!workers || workers[1].trim() !== '1') {
  problems.push(
    `${CONFIG}: the suite shares one upload session per engine, which ` +
      'requires `workers: 1`.',
  )
}

if (problems.length > 0) {
  console.error('The browser suite can spend resources it does not record:')
  for (const problem of problems) console.error(`  - ${problem}`)
  process.exit(1)
}
console.log(
  'Every billed request goes through the page or the harness recorder, every ' +
    'fulfilled one declares its source, and retries are off.',
)
