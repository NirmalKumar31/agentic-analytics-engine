import { defineConfig, devices } from '@playwright/test'

import { parseBrowsers } from './scripts/e2eGate.mjs'
import { resolveBaseUrl } from './src/test/preflight'

/**
 * Browser-level acceptance against a running server.
 *
 * Deliberately not self-starting: the target is either a container built
 * from ./Dockerfile or a deployed service, and the point of these tests is
 * to exercise the thing that will actually be served. Set AAE_E2E_BASE_URL
 * and start the target yourself.
 *
 * CI selects Chromium, Firefox and WebKit in separate steps. The closed-set
 * parser below makes the selected engine explicit, and the report guard
 * reconciles every discovered test for that engine.
 */
const baseURL = resolveBaseUrl(process.env)

const available = {
  chromium: { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  firefox: { name: 'firefox', use: { ...devices['Desktop Firefox'] } },
  webkit: { name: 'webkit', use: { ...devices['Desktop Safari'] } },
}

/**
 * Validated here, before Playwright starts, so an unusable selection is an
 * error rather than zero projects.
 *
 * The filter this replaced turned any unrecognised value into an empty
 * project list, and an empty project list runs no tests and reports
 * success. A shell quoting bug produced exactly that: `AAE_E2E_BROWSERS`
 * held "chromium 0", nothing ran, and the skip guard approved it.
 */
const selected = parseBrowsers(process.env.AAE_E2E_BROWSERS)
const projects = selected.map((name) => available[name as keyof typeof available])

// Printed before execution so the log says which engines a run covered,
// rather than leaving it to be inferred from the request.
console.log(`E2E engines selected: ${selected.join(', ')}`)

/*
 * One set of paths per engine.
 *
 * The three engines are three invocations against one container, and they
 * used to write to the same `playwright-results.json`, `test-results/` and
 * `playwright-report/`. A later engine therefore overwrote the evidence of
 * an earlier one's failure: Chromium could fail, Firefox could clear the
 * output directory on its way in, and the artifact uploaded at the end held
 * traces for WebKit only. The one engine whose failure started the
 * investigation was the one with nothing left to look at.
 */
const engine = selected.length === 1 ? selected[0] : 'all'
const report = process.env.PLAYWRIGHT_JSON_OUTPUT_NAME ?? `playwright-results-${engine}.json`

export default defineConfig({
  testDir: './e2e',
  outputDir: `test-results/${engine}`,
  // Refuses the whole run unless /api/health reports provider_mode=fake.
  // A paid provider was once listening on this suite's default port; see
  // ./e2e/preflight.ts. There is no bypass flag on purpose.
  globalSetup: './e2e/global-setup.ts',
  // Sessions are server-side and the deployment admits only a couple of
  // concurrent analyses, so these run one at a time rather than racing each
  // other into a 429.
  workers: 1,
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  /*
   * No retries, anywhere.
   *
   * CI used to retry once. That cannot rescue a run -- the report guard
   * refuses a flaky result outright, because a test that passes only on
   * retry has not demonstrated what it asserts -- so the retry could never
   * turn the job green. What it could do is spend the budget again: every
   * retried test re-uploads its dataset and re-runs its analyses, against a
   * container that allows 200 analyses and 200 uploads per IP per hour for
   * all three engines together. A worst-case budget including retries would
   * have to be half the size for no gain.
   *
   * So the policy is: retries off, and the budget is the true cost of one
   * pass. An infrastructure flake now fails the job honestly instead of
   * being papered over at twice the price.
   */
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  reporter: process.env.CI
    ? [
        ['list'],
        ['json', { outputFile: report }],
        ['html', { open: 'never', outputFolder: `playwright-report/${engine}` }],
      ]
    : [['list'], ['json', { outputFile: report }]],
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    // The capability cookie is HttpOnly and host-only; ignoring HTTPS
    // errors would let a misconfigured certificate pass unnoticed.
    ignoreHTTPSErrors: false,
  },
  projects,
})
