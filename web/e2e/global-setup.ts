import { assertFakeProvider, resolveBaseUrl, type HealthProbe } from "../src/test/preflight";

/**
 * Playwright's `globalSetup`. Throwing here aborts the run before any
 * browser launches, so no page, upload or analysis request can reach a
 * server whose provider mode was never established.
 */
export default async function globalSetup(): Promise<void> {
  const baseURL = resolveBaseUrl(process.env);
  await assertFakeProvider(baseURL, async (url): Promise<HealthProbe> => {
    const response = await fetch(url, {
      // A hung target must refuse, not stall the whole CI job.
      signal: AbortSignal.timeout(10_000),
    });
    return { status: response.status, body: await response.text() };
  }, process.env);
  console.log(`E2E safety check passed: ${baseURL} reports provider_mode=fake`);
}
