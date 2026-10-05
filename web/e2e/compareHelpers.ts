import { expect, type Page } from "@playwright/test";

import { CAPTURE_HEADER, ledgerHarnessRequest, payloadSha, registerPayload, routeConfig, selectMode, SOURCE_HEADER } from "./helpers";

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

/**
 * When the route went on, and when the application first asked.
 *
 * Kept so a test can assert the order rather than infer it from a timeout
 * ninety seconds later.
 */
const seen = new WeakMap<
  Page,
  { routed: number; requested: number | null; intercepted: number }
>();

/**
 * Did the configuration route exist before the application asked for it?
 *
 * `null` when `advertiseAi` was never called for this page, or when the
 * page never requested the config -- so a test cannot pass by forgetting
 * to set either up.
 */
export function configBeforeMount(page: Page): boolean | null {
  const record = seen.get(page);
  if (!record || record.requested === null) return null;
  return record.routed <= record.requested;
}

/**
 * How many times the override actually answered `/api/config`.
 *
 * Ordering alone is not enough to prove this fixture works. A deployment
 * that already advertises AI -- which the local strict server does, and
 * the CI container does not -- offers Compare whether or not the override
 * applied, so a test that only asserts "Compare is visible" passes over a
 * route that silently did nothing. Forcing the handler to fail locally
 * left all three `compareSetup` tests green, which is how that was found.
 *
 * `null` when `advertiseAi` was never called for this page, so a test
 * cannot pass by forgetting to set it up.
 */
export function configInterceptions(page: Page): number | null {
  return seen.get(page)?.intercepted ?? null;
}

/**
 * Advertise AI and Compare so the selector offers them.
 *
 * **Call this before `openApp`.** The application requests `/api/config`
 * as it mounts, so a route installed afterwards never sees that request:
 * the real config says AI is unavailable, the Compare strategy is never
 * offered, and `startCompare` waits out the full test timeout reporting
 * only that a radio did not appear.
 *
 * This is not hypothetical. Reordering these two lines in `print.spec.ts`
 * passed on Chromium -- which happened to win the race -- and timed out
 * every test in the file on Firefox. `configBeforeMount` in
 * `compareHelpers.spec.ts` pins the ordering so it cannot come back.
 */
export async function advertiseAi(page: Page) {
  seen.set(page, { routed: Date.now(), requested: null, intercepted: 0 });
  page.on("request", (request) => {
    if (!request.url().includes("/api/config")) return;
    const record = seen.get(page);
    if (record && record.requested === null) record.requested = Date.now();
  });
  await routeConfig(page, (body) => {
    const record = seen.get(page);
    if (record) record.intercepted += 1;
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
  });
}

export const AI_RUN_ID = "run_compare_ai";

/**
 * One captured deterministic result per question, per worker.
 *
 * Every Compare test started a genuine analysis -- ten in `compare.spec.ts`
 * alone, plus the Compare cells in `visualReview`, `print` and `chart` --
 * against a container that allows 200 analyses per IP per hour for all
 * three engines together. What those tests assert is how a comparison is
 * *presented*: one shared answer under agreement, a structured diff under
 * divergence, one drawer with a tab per strategy. The scenario is expressed
 * by `mutate`, which is applied to the captured payload exactly as it was
 * applied to a fresh one, so each case still differs only in the field it
 * is about.
 *
 * The first comparison of a question is real, and it is what fills this.
 */
const replayable = new Map<string, Record<string, unknown>>();

/** Keyed by the dataset the caller named, and the question. */
const key = (dataset: string, question: string) => `${dataset}\u0000${question}`;


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
  /**
   * Which data the comparison is over, named by the caller.
   *
   * The cache was keyed by the question alone, and `compare.spec.ts` asks
   * the same question of the demo warehouse and of an uploaded file -- so
   * "Compare over an uploaded dataset" was answered with the warehouse's
   * result. Every assertion still passed; what it stopped proving was the
   * thing in its own name.
   *
   * The session id would be the obvious key and is the wrong one: two
   * pages on the same unchanging warehouse hold two sessions over
   * identical data, and keying on the session makes each of them pay for
   * its own comparison. The caller knows what it opened, so it says.
   */
  dataset = "demo",
) {
  let deterministicId = "";
  /** What the AI side reports: the captured result passed through `mutate`. */
  let finished: Record<string, unknown> | null = null;
  /**
   * What the deterministic side reports on a replay: the capture, untouched.
   *
   * On the real path this side is answered by the server itself, so it is
   * the unmodified result and `mutate` applies only to the mirrored side.
   * A replay has to preserve that asymmetry -- serving the mutated payload
   * to both sides made every scenario agree with itself, so the structured
   * diff had nothing to show and a one-sided refusal refused both sides.
   */
  let deterministicPayload: Record<string, unknown> | null = null;
  let reading: Promise<Record<string, unknown>> | null = null;

  await page.route("**/api/comparisons", async (route) => {
    const request = route.request().postDataJSON() as {
      session_id: string;
      question: string;
    };
    const remembered = replayable.get(key(dataset, request.question));
    if (remembered) {
      deterministicPayload = structuredClone(remembered);
      finished = mutate(structuredClone(remembered));
      deterministicId = `run_replay_${Math.random().toString(36).slice(2, 10)}`;
      await route.fulfill({
        status: 202,
        headers: {
          [SOURCE_HEADER]: "capture",
          [CAPTURE_HEADER]: payloadSha(remembered),
        },
        json: {
          comparison_id: "cmp_replayed",
          session_id: request.session_id,
          question: request.question,
          deterministic_run_id: deterministicId,
          ai_run_id: AI_RUN_ID,
        },
      });
      return;
    }

    const started = await route.fetch({
      url: new URL("/api/analyses", page.url()).toString(),
      method: "POST",
      postData: JSON.stringify({ ...request, mode: "deterministic" }),
      headers: { "content-type": "application/json" },
    });
    const { run_id } = (await started.json()) as { run_id: string };
    /*
     * Recorded here because `route.fetch` is not page traffic: the request
     * exists only inside this handler, so `watchTraffic` cannot see it and
     * the one analysis a Compare scenario really costs would be invisible.
     */
    ledgerHarnessRequest(
      "/api/analyses",
      started.status(),
      `compare: ${request.question.slice(0, 36)}`,
    );
    deterministicId = run_id;
    /*
     * Captured here, by polling to completion before answering.
     *
     * Capturing opportunistically in the AI-side route handler missed:
     * that branch is only reached if a test drives that side to the end,
     * and three of `compare.spec.ts`'s ten tests assert on the verdict and
     * stop -- so each of those paid for a real analysis before the cache
     * ever filled. Polling here costs the *first* comparison a second of
     * waiting and is certain; every later one replays it.
     */
    for (let attempt = 0; attempt < 120; attempt += 1) {
      const poll = await page.request.get(
        new URL(`/api/analyses/${run_id}`, page.url()).toString(),
      );
      const settled = (await poll.json()) as Record<string, unknown>;
      if (settled.status && settled.status !== "running") {
        replayable.set(key(dataset, request.question), structuredClone(settled));
        // Registered, so a later replay of it reconciles against something
        // the guard can see was captured from a server-backed response.
        registerPayload(settled, "capture");
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 150));
    }
    await route.fulfill({
      status: 202,
      // The 202 itself is synthesised while a real run proceeds behind it.
      headers: { [SOURCE_HEADER]: "harness" },
      json: {
        comparison_id: "cmp_compare_spec",
        session_id: request.session_id,
        question: request.question,
        deterministic_run_id: run_id,
        ai_run_id: AI_RUN_ID,
      },
    });
  });

  /*
   * The deterministic side of a replayed comparison. There is no such run
   * on the server, so the page is answered from the captured payload --
   * the same one the AI side gets, which is what agreement means here.
   */
  await page.route("**/api/analyses/run_replay_*", async (route) => {
    await route.fulfill({
      status: 200,
      json: { ...(deterministicPayload ?? {}), run_id: deterministicId },
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
  /*
   * One real run, one mirrored.
   *
   * `compareWith` starts a genuine deterministic analysis and answers the
   * AI side with that same payload, so the server performs one analysis per
   * comparison, not two. Counting both as real would report pressure on a
   * ceiling that was never touched.
   */
  /*
   * Not counted here. Whether a comparison costs a real analysis is known
   * only inside `compareWith`'s route handler -- the first for a question
   * is real and the rest replay it -- so that is where the ledger is
   * written. Counting at the call site reported ten analyses for one.
   */
  await page.getByLabel("Business question").fill(question);
  await selectMode(page, "compare");
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
