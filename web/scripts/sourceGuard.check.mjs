/**
 * The static accounting rules must fail on each thing they forbid.
 *
 * `check-e2e-sources.mjs` is what stops a billed request being issued by a
 * path the recorder cannot see, or answered in a way it cannot classify.
 * Both are one plausible line, neither breaks a test, and both make the
 * measured cost of the suite smaller than the real one, which is how the
 * job came to be over its analysis budget while the guard reported it
 * passing.
 */

import { strict as assert } from 'node:assert'
import { spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const GUARD = resolve('scripts/check-e2e-sources.mjs')

let failures = 0
const tests = []
const test = (name, fn) => tests.push([name, fn])

const CLEAN_CONFIG = `export default { retries: 0, workers: 1 }\n`

function run(files, config = CLEAN_CONFIG) {
  const dir = mkdtempSync(join(tmpdir(), 'sources-'))
  const e2e = join(dir, 'e2e')
  mkdirSync(e2e, { recursive: true })
  for (const [name, body] of Object.entries(files)) {
    writeFileSync(join(e2e, name), body)
  }
  const configPath = join(dir, 'playwright.config.ts')
  writeFileSync(configPath, config)
  const result = spawnSync('node', [GUARD, e2e, configPath], { encoding: 'utf8' })
  rmSync(dir, { recursive: true, force: true })
  return { ...result, output: `${result.stdout}${result.stderr}` }
}

const DECLARED = `
test("x", async ({ page }) => {
  await page.route("**/api/analyses", async (route) => {
    await route.fulfill({ status: 202, headers: fixtureHeaders("x"), json: {} });
  });
});
`

test('a spec that declares its sources passes', () => {
  const r = run({ 'a.spec.ts': DECLARED })
  assert.equal(r.status, 0, r.output)
})

test('an API-context POST to a billed endpoint fails', () => {
  // Invisible to `watchTraffic`: an APIRequestContext produces no page
  // request event, so the admission happens and nothing records it.
  const r = run({
    'a.spec.ts': `await page.request.post("/api/analyses", { data: {} });\n`,
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /through an API context/)
})

test('a route handler fetching a billed endpoint fails', () => {
  const r = run({
    'a.spec.ts': `await route.fetch({ url: "/api/analyses", method: "POST" });\n`,
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /produces no page traffic/)
})

test('answering a billed route without declaring a source fails', () => {
  const r = run({
    'a.spec.ts': `
test("x", async ({ page }) => {
  await page.route("**/api/analyses", async (route) => {
    await route.fulfill({ status: 202, json: { run_id: "r" } });
  });
});
`,
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /without declaring a source/)
})

test('the files that own the accounting are exempt', () => {
  // `helpers.ts` is where the recorder and the headers live; it has to be
  // able to do both or there is nothing to exempt the others from.
  const r = run({
    'helpers.ts': `await route.fetch({ url: "/api/analyses", method: "POST" });\n`,
  })
  assert.equal(r.status, 0, r.output)
})

test('importing `test` from @playwright/test fails', () => {
  // Six specs did, so their pages had no recorder and about eleven
  // admissions per engine were invisible to the budget they are measured
  // against.
  const r = run({
    'a.spec.ts': `import { expect, test } from "@playwright/test";\n${DECLARED}`,
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /Import it from \.\/fixtures/)
})

test('opening a page without attaching the recorder fails', () => {
  const r = run({
    'a.spec.ts': `const page = await browser.newPage();\nawait openApp(page);\n`,
  })
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /without calling `watchTraffic`/)
})

test('a page that is given the recorder passes', () => {
  const r = run({
    'a.spec.ts': `const page = await browser.newPage();\nwatchTraffic(page);\n`,
  })
  assert.equal(r.status, 0, r.output)
})

test('retries above zero fail', () => {
  const r = run({ 'a.spec.ts': DECLARED }, `export default { retries: 2, workers: 1 }\n`)
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /sets retries to 2/)
  assert.match(r.output, /re-issues every admission/)
})

test('no retries setting at all fails', () => {
  const r = run({ 'a.spec.ts': DECLARED }, `export default { workers: 1 }\n`)
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /declares no `retries`/)
})

test('more than one worker fails', () => {
  const r = run({ 'a.spec.ts': DECLARED }, `export default { retries: 0, workers: 4 }\n`)
  assert.equal(r.status, 1, r.output)
  assert.match(r.output, /requires `workers: 1`/)
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
console.log(`${tests.length - failures}/${tests.length} source checks passed`)
process.exit(failures === 0 ? 0 : 1)
