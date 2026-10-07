import { defineConfig, devices } from "@playwright/test";

import { PROJECTS } from "./hosted/matrix";
import { baseUrl } from "./hosted/preflight";

/**
 * Credential-free visual acceptance against a deployment.
 *
 * Separate from `playwright.config.ts` on purpose. That suite drives a
 * container it owns, spends a measured budget of uploads and analyses, and
 * runs on three engines. This one runs against a public deployment nobody
 * gave it a budget on, so it spends **nothing**: every state is staged from
 * the deployment's own recordings or from committed fixtures, and
 * `hosted/fixtures.ts` aborts anything that would reach a billed endpoint.
 *
 * It is not part of CI. CI cannot know which commit a deployment is serving
 * -- Render redeploys on its own schedule, and a sweep filed against the
 * wrong SHA is worse than no sweep. `hosted/preflight.ts` reads
 * `/api/health` and refuses unless the build matches `AAE_HOSTED_SHA`.
 *
 * One engine, because this measures layout rather than engine behaviour,
 * and twelve projects, because the matrix is six widths in two themes.
 */
const HEIGHTS: Record<number, number> = {
  360: 780,
  390: 844,
  768: 1024,
  1024: 768,
  1440: 900,
  1920: 1080,
};

export default defineConfig({
  testDir: "./hosted",
  outputDir: "hosted-results/artefacts",
  globalSetup: "./hosted/preflight.ts",
  /*
   * Parallel is safe here in a way it is not in the browser suite: no
   * worker takes a server-side session, starts an analysis or holds any
   * resource the deployment counts. What is left is free reads of static
   * assets and three JSON documents.
   */
  workers: Number(process.env.AAE_HOSTED_WORKERS ?? 4),
  fullyParallel: true,
  forbidOnly: true,
  /*
   * No retries. A visual acceptance result that needed a second attempt
   * has not demonstrated what it asserts, and the report checker refuses a
   * flaky run outright.
   */
  retries: 0,
  timeout: 150_000,
  expect: { timeout: 25_000 },
  /*
   * Named, so two sweeps do not overwrite each other's evidence.
   *
   * A hosted acceptance run is filed against one deployment. Running the
   * same suite against a second one -- a local build of the branch and the
   * live service, which is the normal pair -- wrote both into one file and
   * the checker then reconciled whichever finished last. The default keeps
   * the single-target case simple; `AAE_HOSTED_REPORT` separates the pair.
   */
  reporter: [
    ["list"],
    [
      "json",
      {
        outputFile:
          process.env.AAE_HOSTED_REPORT ?? "hosted-results/playwright.json",
      },
    ],
  ],
  use: {
    baseURL: baseUrl(process.env),
    trace: "retain-on-failure",
    screenshot: "off",
    ignoreHTTPSErrors: false,
  },
  projects: PROJECTS.map(({ width, theme, name }) => ({
    name,
    use: {
      ...devices["Desktop Chrome"],
      viewport: { width, height: HEIGHTS[width] ?? 900 },
      colorScheme: theme,
    },
  })),
});
