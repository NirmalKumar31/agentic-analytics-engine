import { expect, type Page } from "@playwright/test";

/**
 * Reaching a Compare state in a browser, without a provider.
 *
 * Extracted from `compare.spec.ts` when `print.spec.ts` needed the same
 * route: a printed Compare is one of the four artefacts step H has to
 * produce, and reaching that state is the hard part. Copying these into a
 * second spec would have meant two ways of faking the AI side, which is
 * exactly the kind of drift that makes a fixture stop resembling the thing
 * it stands in for.
 *
 * **The left side is always a real run.** AI mode is unavailable on a local
 * or CI deployment, so `compareWith` starts a genuine deterministic
 * analysis, mints an id for the right side, and answers that id with the
 * real payload passed through `mutate`.
 *
 * Nothing here contacts a provider: the suite refuses to start unless
 * `/api/health` reports `provider_mode=fake`.
 */

/** Advertise AI and Compare so the selector offers them. */
export async function advertiseAi(page: Page) {
  await page.route("**/api/config", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.capabilities.modes = body.capabilities.modes.map(
      (mode: { mode: string }) =>
        mode.mode === "ai"
          ? { ...mode, available: true, reason: "", message: "" }
          : mode,
    );
    body.capabilities.compare_available = true;
    body.capabilities.ai_limits = {
      runs_per_session: 3,
      max_model_calls_per_run: 24,
      max_runtime_seconds: 180,
    };
    await route.fulfill({ response, json: body });
  });
}

export const AI_RUN_ID = "run_compare_ai";

/**
 * Route Compare so the right-hand side mirrors a real deterministic run.
 *
 * `mutate` receives the finished deterministic payload and returns what the
 * AI side should report. The default is the identity, which is the
 * agreement case.
 */
export async function compareWith(
  page: Page,
  mutate: (payload: Record<string, unknown>) => Record<string, unknown> = (p) => p,
) {
  let deterministicId = "";
  let finished: Record<string, unknown> | null = null;
  let reading: Promise<Record<string, unknown>> | null = null;

  await page.route("**/api/comparisons", async (route) => {
    const request = route.request().postDataJSON() as {
      session_id: string;
      question: string;
    };
    const started = await route.fetch({
      url: new URL("/api/analyses", page.url()).toString(),
      method: "POST",
      postData: JSON.stringify({ ...request, mode: "deterministic" }),
      headers: { "content-type": "application/json" },
    });
    const { run_id } = (await started.json()) as { run_id: string };
    deterministicId = run_id;
    await route.fulfill({
      status: 202,
      json: {
        comparison_id: "cmp_compare_spec",
        session_id: request.session_id,
        question: request.question,
        deterministic_run_id: run_id,
        ai_run_id: AI_RUN_ID,
      },
    });
  });

  await page.route(`**/api/analyses/${AI_RUN_ID}`, async (route) => {
    // The AI side can be polled before `/api/comparisons` has resolved, so
    // there may be no real run to mirror yet. Reporting "running" is the
    // truthful answer; fetching `/api/analyses/` with an empty id is a 404
    // that reads as a failed AI run.
    if (!deterministicId) {
      await route.fulfill({
        status: 200,
        json: { run_id: AI_RUN_ID, status: "running" },
      });
      return;
    }
    if (finished) {
      await route.fulfill({ status: 200, json: finished });
      return;
    }

    /*
     * One read at a time, shared between concurrent polls.
     *
     * This handler runs on every poll, and a response read through the
     * request context is disposed when a route is fulfilled -- so
     * re-reading it on the next poll failed with "Response has been
     * disposed", which surfaced as the AI side never finishing.
     *
     * Caching the finished *payload* fixed the common case but left a
     * race: two polls can both find the cache empty, both start a read,
     * and the first to fulfil disposes the second's response. That failed
     * one run in three of the full gate. Caching the in-flight *promise*
     * means there is only ever one read for one answer, and a poll that
     * arrives mid-read waits for it rather than starting another.
     */
    reading ??= (async () => {
      const real = await page.request.get(
        new URL(`/api/analyses/${deterministicId}`, page.url()).toString(),
      );
      return (await real.json()) as Record<string, unknown>;
    })();

    /*
     * A poll can arrive after the test body has finished, and the context
     * teardown then disposes the response mid-read: "apiResponse.json:
     * Response has been disposed", reported against the handler rather
     * than against anything the test did.
     *
     * Reporting the AI side as still running is the honest answer when the
     * read did not complete. It is not a swallowed failure: if the read
     * fails while a test is still watching, the side never finishes and
     * the test fails on its own assertion, which says what it was waiting
     * for.
     */
    let payload: Record<string, unknown>;
    try {
      payload = await reading;
    } catch {
      reading = null;
      await route.fulfill({
        status: 200,
        json: { run_id: AI_RUN_ID, status: "running" },
      });
      return;
    }
    if (payload.status === "running") {
      // Not the answer yet: clear the cache so the next poll reads again.
      reading = null;
      await route.fulfill({ status: 200, json: payload });
      return;
    }
    finished ??= mutate(payload);
    await route.fulfill({ status: 200, json: finished });
  });
}

export async function startCompare(page: Page, question: string) {
  await page.getByLabel("Business question").fill(question);
  await page.getByRole("radio", { name: /Compare planning strategies/ }).check();
  await page.getByRole("button", { name: /Compare strategies/ }).click();
  await expect(page.getByTestId("compare-workspace")).toBeVisible({
    timeout: 90_000,
  });
}

/**
 * Wait until neither side is still going.
 *
 * The application polls a run while it is running. A test that counts
 * requests has to start from a settled page, or a background poll lands in
 * the middle of the measurement and is read as something the interaction
 * caused.
 */
export async function settle(page: Page) {
  await expect
    .poll(
      async () =>
        page
          .getByTestId("pane-status")
          .evaluateAll((nodes) =>
            nodes.every(
              (node) =>
                node.getAttribute("data-state") !== "running" &&
                node.getAttribute("data-state") !== "not_started",
            ),
          ),
      { timeout: 90_000 },
    )
    .toBe(true);
  // One more poll interval, so an in-flight request has landed.
  await page.waitForTimeout(1_500);
}

export async function openDemo(page: Page) {
  await page.getByRole("button", { name: /Commerce demo warehouse/ }).click();
  await expect(page.getByTestId("composer")).toBeVisible();
}
