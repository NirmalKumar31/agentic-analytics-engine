import { defineConfig, devices } from '@playwright/test'

/**
 * Browser-level acceptance against a running server.
 *
 * Deliberately not self-starting: the target is either a container built
 * from ./Dockerfile or a deployed service, and the point of these tests is
 * to exercise the thing that will actually be served. Set AAE_E2E_BASE_URL
 * and start the target yourself.
 *
 * Chromium alone in CI, because installing three browser engines on every
 * push costs more than it finds. Firefox and WebKit run against the
 * deployed URL at release time, where the cross-browser question is real.
 */
const baseURL = process.env.AAE_E2E_BASE_URL ?? 'http://127.0.0.1:8000'

const projects = [
  { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  { name: 'firefox', use: { ...devices['Desktop Firefox'] } },
  { name: 'webkit', use: { ...devices['Desktop Safari'] } },
]

export default defineConfig({
  testDir: './e2e',
  // Sessions are server-side and the deployment admits only a couple of
  // concurrent analyses, so these run one at a time rather than racing each
  // other into a 429.
  workers: 1,
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    // The capability cookie is HttpOnly and host-only; ignoring HTTPS
    // errors would let a misconfigured certificate pass unnoticed.
    ignoreHTTPSErrors: false,
  },
  projects: projects.filter(
    (p) => !process.env.AAE_E2E_BROWSERS || process.env.AAE_E2E_BROWSERS.split(',').includes(p.name),
  ),
})
