import {
  test as base,
  expect,
  type Browser,
  type Page,
  type Response,
} from "@playwright/test";

import {
  ambiguousCsv,
  answerRunWith,
  ask,
  closeCallCsv,
  endSession,
  openApp,
  plainCsv,
  sampleCsv,
  setTheme,
  twoClockCsv,
  twoDimensionCsv,
  uploadFile,
  waitForReport,
  registerPayload,
  selectMode,
  watchTraffic,
  wideCsv,
} from "./helpers";

/**
 * One profiled upload, shared by every test that only needs a dataset.
 *
 * ---------------------------------------------------------------- why
 *
 * The suite uploaded 150 datasets per engine, 450 across the three-engine
 * CI job, against a container that allows 24 *live* upload sessions and 200
 * uploads per IP per hour. Chromium exhausted the session pool at test 51
 * ("the demo is at capacity for uploaded datasets"); Firefox and WebKit
 * then exhausted the hourly allowance. Almost none of those uploads earned
 * their keep: 36 of them rendered the same report at different viewport
 * widths, 16 more rendered terminal states that are produced by
 * intercepting a response, not by the file.
 *
 * The product holds no session across a page load -- `App.tsx` keeps it in
 * React state, and the capability cookie alone cannot restore it, so
 * reusing a server-side session means keeping the *page* alive. That is
 * what this fixture does: worker-scoped, so with `workers: 1` there is one
 * upload per engine for every spec that opts in.
 *
 * ------------------------------------------------------------ isolation
 *
 * A shared page is shared state, so isolation is explicit rather than
 * implied. `profiled` hands back a page reset to the composer through the
 * product's own "Start over" control -- not a test-only endpoint, and not a
 * fresh upload. A test that needs a different dataset *shape*, or that
 * leaves the session unusable, uses the ordinary `page` fixture and pays
 * for its own upload; that is a deliberate choice each time, visible in the
 * spec.
 *
 * The session is handed back at the end of the worker with "End session",
 * which is the product's `DELETE /api/datasets/{id}`. A closed browser does
 * not free a server-side session: the server holds it until the capability
 * deletes it or the TTL expires, and the TTL outlives a CI run.
 */

/**
 * Dismiss whatever sheet the last test left open.
 *
 * A side sheet puts a scrim over the header, so every control up there is
 * in the document, visible, enabled, and not clickable. Playwright waits
 * for actionability and reports a timeout against a button the screenshot
 * plainly shows, which is a confusing way to learn that a drawer is open.
 */
/**
 * The viewport each shared page started with.
 *
 * Twenty-four places in the suite call `setViewportSize`, and on a shared
 * page the last one wins for every test that follows. `resetToComposer`
 * already put the theme and the emulated media back for exactly this
 * reason; the viewport was the one piece of context it left behind, and it
 * is the one that changes layout.
 *
 * It cost an hour to find. `evidence.spec.ts`'s focus-trap test ran after
 * `report.spec.ts`'s "a long report on a phone", so the drawer it tabbed
 * through was 390px wide rather than the project's default, and on WebKit
 * focus settled outside it after eight tabs. In isolation the same test
 * passes in two seconds. Nothing about the assertion was wrong; it was
 * being asked about a layout no test had chosen.
 */
const started = new WeakMap<Page, { width: number; height: number }>();

/** Remember a shared page's own viewport, so a test cannot keep another's. */
export function rememberViewport(page: Page): void {
  const size = page.viewportSize();
  if (size) started.set(page, size);
}

export async function closeAnySheet(page: Page): Promise<void> {
  for (const sheet of ["schema-sheet", "evidence-drawer", "compare-evidence-drawer"]) {
    const open = page.getByTestId(sheet);
    if ((await open.count()) > 0) {
      await page.keyboard.press("Escape");
      await expect(open).toHaveCount(0, { timeout: 5_000 });
    }
  }
}

/** Back to the composer, by the route a reader would take. */
async function resetToComposer(page: Page): Promise<void> {
  /*
   * Sheets first, then the control.
   *
   * A side sheet puts a scrim over the header, so "Start over" is in the
   * document and not clickable -- Playwright waits for actionability and
   * the test times out pointing at a button that is plainly visible in the
   * screenshot. Reversing these two is what fixed it.
   */
  await closeAnySheet(page);

  const startOver = page.getByRole("button", { name: "Start over" });
  if ((await startOver.count()) > 0) {
    await startOver.first().click({ timeout: 15_000 });
  }

  /*
   * The strategy, back to the default.
   *
   * "Start over" clears the run, not the mode. A test that selected
   * "Compare planning strategies" left the primary control reading
   * "Compare strategies", so the next test's wait for "Run analysis" timed
   * out and reported a *disabled* button -- the button it was waiting for
   * did not exist. Mode is the one piece of composer state that survives a
   * reset, so it is reset explicitly.
   */
  // Through the label, which is the control a reader clicks: `.check()`
  // clicks the covered input, and Firefox reports the label as
  // intercepting those pointer events and retries until the test timeout.
  await selectMode(page, "deterministic");

  // And the theme, which a visual test may have toggled.
  await setTheme(page, "light");

  /*
   * And any emulated media. `accessibility.spec.ts` scans under
   * `prefers-reduced-motion: reduce`, correctly, because axe composites
   * opacity and a mid-animation scan reports contrast failures that do not
   * exist, and `print.spec.ts` switches to print media. Neither is
   * discarded with the test on a shared session, and `motion.spec.ts`'s
   * control then measured a 1ms budget and concluded the product animates
   * nothing.
   */
  await page.emulateMedia({ media: "screen", reducedMotion: "no-preference" });

  // And the viewport, for the same reason and with more consequence: a
  // phone width left behind changes what every later layout assertion is
  // measuring.
  const original = started.get(page);
  const current = page.viewportSize();
  if (
    original &&
    (!current || current.width !== original.width || current.height !== original.height)
  ) {
    await page.setViewportSize(original);
  }

  await expect(
    page.getByTestId("composer"),
    "the shared session did not return to the composer",
  ).toBeVisible({ timeout: 20_000 });

  /*
   * Visible is not enough: it must be usable.
   *
   * While a run is in flight the composer is on screen with its field
   * disabled, so a reset that only checks visibility returns a page the
   * next test cannot type into, and `fill()` then waits for
   * actionability until the *test* timeout, reporting a two-minute hang
   * on a locator that is plainly present in the screenshot. That is how a
   * previous test leaving a run running got diagnosed as a problem with
   * the test that came after it.
   */
  await expect(
    page.getByLabel("Business question"),
    "the composer came back disabled, which means a previous test left a " +
      "run in flight on this shared session",
  ).toBeEnabled({ timeout: 20_000 });
  await page.getByLabel("Business question").fill("");
}

/**
 * The six dataset shapes this suite needs, one session each.
 *
 * `profiled` is the ordinary 200-row sample and carries most of the suite.
 * The others exist because a test needs data the sample cannot express:
 *
 *   wide          36 categories over 400 rows -- a long table and a
 *                 high-cardinality chart
 *   twoSeries     two dimensions, so the chart carries a colour encoding
 *   twoClocks     two date columns, so the composer must ask which is the
 *                 clock
 *   closeCall     a column whose role is genuinely ambiguous, for the
 *                 role-confirmation flow
 *   plain         no ambiguity at all, as the control for the above
 *   ambiguous     a column that reads as a measure and as a dimension
 *
 * A different filename is not a different shape. Each of these is uploaded
 * once per engine, and only by the specs that take it.
 */
interface WorkerFixtures {
  /**
   * The committed demo warehouse, opened once.
   *
   * Eight specs opened it on eight pages and asked it the same question,
   * for eight real analyses against a dataset that never changes. It holds
   * no upload session. Nothing to hand back, so sharing it costs
   * nothing and saves an admission per spec. Each one still asserts its own
   * thing; they just stop paying separately for the same result.
   *
   * A test that needs the warehouse *opened*, rather than open -- the
   * landing, the phase transition out of `choose_dataset`, the in-flight
   * timeline of a run nobody has performed yet -- takes a page of its own.
   */
  demo: Page;
  profiled: Page;
  wide: Page;
  twoSeries: Page;
  twoClocks: Page;
  closeCall: Page;
  plain: Page;
  ambiguous: Page;
}

type WorkerSession = [
  (args: { browser: Browser }, use: (page: Page) => Promise<void>) => Promise<void>,
  { scope: "worker" },
];

/**
 * The filename each shared session is uploaded under.
 *
 * Exported because the dataset strip echoes the reader's own filename, and
 * a test asserting that is asserting something real. It should name the
 * file the fixture actually sent rather than a literal that drifts.
 */
export const TWO_CLOCKS_FILE = "two-clocks.csv";

/** One context, one upload, handed back to the server at the end. */
function session(name: string, csv: () => string): WorkerSession {
  return [
    async ({ browser }: { browser: Browser }, use: (page: Page) => Promise<void>) => {
      // Captured while a test is still live: the teardown below runs after
      // the last test, where `test.info()` throws, and a close recorded
      // without an engine cannot be reconciled against that engine's opens.
      const project = test.info().project.name;
      const context = await browser.newContext();
      const page = await context.newPage();
      watchTraffic(page);
      rememberViewport(page);
      await openApp(page);
      await uploadFile(page, `${name}.csv`, csv());

      /*
       * A test cannot close the session it borrowed.
       *
       * This page is worker-scoped: one upload serves every test in the
       * file, and the server-side session behind it is handed back once,
       * in the teardown below. An individual test calling `page.close()`
       * -- a reasonable-looking line, and the ordinary thing to write
       * against a test-scoped page -- destroys the shared resource for
       * every test that comes after it. They then fail with "Target page,
       * context or browser has been closed", which names the symptom in
       * the *victim* and says nothing about the culprit; the leaked
       * upload session is reported separately, by the budget guard, as a
       * number that does not reconcile.
       *
       * So the close is refused, loudly, at the line that attempts it.
       * The fixture keeps the real one for its own teardown.
       * `sharedSession.spec.ts` is the regression test.
       */
      const refuse = (what: string) => async () => {
        throw new Error(
          `The "${name}" session is shared by every test in this worker, ` +
            `so a test may not close its ${what}: doing so fails every ` +
            "test after this one and leaves an upload session on the " +
            "server. Use a page of your own (`browser.newPage()`, closed " +
            "in `afterAll`) if the test needs to destroy its context.",
        );
      };
      const releasePage = page.close.bind(page);
      const releaseContext = context.close.bind(context);
      Object.defineProperty(page, "close", {
        configurable: true,
        value: refuse("page"),
      });
      // The context too: closing it takes the page with it, by a different
      // line that reads just as innocently.
      Object.defineProperty(context, "close", {
        configurable: true,
        value: refuse("context"),
      });

      await use(page);

      Object.defineProperty(page, "close", { configurable: true, value: releasePage });
      Object.defineProperty(context, "close", {
        configurable: true,
        value: releaseContext,
      });
      await endSession(page, project);
      try {
        await context.close();
      } catch {
        // The worker-scoped `browser` is torn down around this one, and
        // Playwright disposes its contexts with it, so by the time this
        // runs the context may already be gone. The close above is the one
        // that matters: it is what gives the *server* its session back,
        // and it happens while the page is still alive.
      }
    },
    { scope: "worker" },
  ];
}

export const test = base.extend<{ page: Page }, WorkerFixtures>({
  /*
   * Every per-test page, instrumented.
   *
   * The ledger has to be job-wide to mean anything: a 429 on a page that
   * nothing was watching is a ceiling reached silently, which is exactly
   * how PR #46's browser job failed. Overriding the built-in `page` fixture
   * is the only place that covers every spec without each one remembering.
   */
  page: async ({ page }, use) => {
    watchTraffic(page);
    await use(page);
  },

  profiled: session("shared-profile", sampleCsv),
  wide: session("wide", wideCsv),
  twoSeries: session("two-series", twoDimensionCsv),
  twoClocks: session("two-clocks", twoClockCsv),
  closeCall: session("close-call", closeCallCsv),
  plain: session("plain", plainCsv),
  ambiguous: session("ambiguous", ambiguousCsv),

  demo: [
    async ({ browser }, use) => {
      const context = await browser.newContext();
      const page = await context.newPage();
      watchTraffic(page);
      rememberViewport(page);
      await openApp(page);
      await page.getByRole("button", { name: /Commerce demo warehouse/ }).click();
      await expect(page.getByTestId("composer")).toBeVisible();

      const refuse = (what: string) => async () => {
        throw new Error(
          `The demo warehouse page is shared by every test in this worker, ` +
            `so a test may not close its ${what}.`,
        );
      };
      const releasePage = page.close.bind(page);
      const releaseContext = context.close.bind(context);
      Object.defineProperty(page, "close", { configurable: true, value: refuse("page") });
      Object.defineProperty(context, "close", {
        configurable: true,
        value: refuse("context"),
      });

      await use(page);

      Object.defineProperty(page, "close", { configurable: true, value: releasePage });
      Object.defineProperty(context, "close", {
        configurable: true,
        value: releaseContext,
      });
      // No upload, so no server-side session to return, only the context.
      await context.close();
    },
    { scope: "worker" },
  ],
});

/**
 * One real analysis per distinct question, per engine. Everything else
 * replays it.
 *
 * ----------------------------------------------------------------- why
 *
 * The container allows **200 analyses per IP per hour**, shared by all three
 * engines because they run from one address inside one hour. The suite was
 * running one per rendering, and `POST /api/analyses`
 * began returning 429 partway through Firefox while every upload check was
 * still green. Cutting uploads had moved the pressure here without anyone
 * noticing, because nothing counted analyses.
 *
 * Almost none of those runs were under test. A report inspected at six
 * widths in two themes, scanned by axe, printed to PDF, and read for its
 * chart colours is **one** run looked at nine ways. So each distinct
 * question is asked of the engine once per worker, its finished payload is
 * captured, and every later request for that question is answered from the
 * capture.
 *
 * ------------------------------------------------------------ what this
 * ------------------------------------------------------------ does not do
 *
 * It does not replace the engine where the engine is the subject. A refusal,
 * a stopped timeline, a run in flight, a schema-role confirmation, a
 * comparison -- those call `ask` or `startCompare` directly and get a real
 * analysis, because what they assert is how the engine *behaves*, not how a
 * result *renders*. `docs` in `e2e/UPLOADS.md` lists every remaining real
 * analysis and why it is one.
 */
/*
 * Keyed by dataset *and* question, not by question alone.
 *
 * Two pages can ask the same question of different data -- `golden.spec.ts`
 * asks the demo warehouse what `chart.spec.ts` asks a 200-row upload, and
 * a question-keyed cache would replay one dataset's result on the other's
 * session, so the test would assert against a report for a file it never
 * opened.
 *
 * Keying on the dataset the page has open fixes that and does something
 * better than keying on the page would: every spec that opens the *demo
 * warehouse* is looking at the same data, on its own page, in its own file.
 * One of them admits the question and the rest replay it. That is where
 * most of the remaining admissions were -- five separate real runs of
 * "What is the total revenue by region?" against one unchanging warehouse.
 *
 * The identity comes from the context strip, which is the application's own
 * statement of what is open: the file name for an upload, the warehouse's
 * name for the warehouse. Not from the session id, because two sessions
 * over the same uploaded bytes are the same data as far as a report is
 * concerned, and not from the page, because that is the thing we want to
 * share across.
 */
const captured = new Map<string, Record<string, unknown>>();

async function datasetKey(page: Page): Promise<string> {
  const strip = page.getByTestId("dataset-context");
  if ((await strip.count()) === 0) return "unknown";
  const text = ((await strip.first().textContent()) ?? "").replace(/\s+/g, " ").trim();
  return text === "" ? "unknown" : text;
}

/**
 * Start listening for the payload *before* the run that produces it.
 *
 * This used to be the first line of `capturePayload`, called after `ask()`
 * had already started the run, and that is a race the suite lost. The
 * application polls `GET /api/analyses/<id>` while a run is going and
 * stops when it finishes; in fake mode a run can finish before the
 * listener is attached, and then no further poll ever arrives and
 * `waitForResponse` waits until the *test* timeout. Chromium and Firefox
 * happened to lose the race the other way (a poll was always still in
 * flight); WebKit finished first and four tests died at 120 seconds with
 * no assertion to point at. The trace named this line.
 *
 * Armed first, so the response cannot be missed. Bounded too, so a run
 * that genuinely never answers fails with a sentence rather than
 * consuming the whole test budget.
 */
function armCapture(page: Page): Promise<Response | null> {
  return page
    .waitForResponse(
      (response) =>
        /\/api\/analyses\/[^/]+$/.test(response.url()) &&
        response.request().method() === "GET" &&
        response.status() === 200,
      { timeout: 60_000 },
    )
    .catch(() => null);
}

/** Read the finished payload of the run the armed listener caught. */
async function capturePayload(
  page: Page,
  armed: Promise<Response | null>,
): Promise<Record<string, unknown>> {
  const settled = await armed;
  if (settled === null) {
    throw new Error(
      "no run payload arrived within 60s, so there was nothing to capture: " +
        "the run never started, or it finished before the listener was armed",
    );
  }
  const body = (await settled.json()) as Record<string, unknown>;
  if (body.status && body.status !== "running") return body;

  // The first poll can land while the run is still going.
  for (let attempt = 0; attempt < 60; attempt += 1) {
    await page.waitForTimeout(200);
    const again = await page.request.get(
      new URL(`/api/analyses/${String(body.run_id)}`, page.url()).toString(),
    );
    const payload = (await again.json()) as Record<string, unknown>;
    if (payload.status && payload.status !== "running") return payload;
  }
  throw new Error("the run never finished, so its payload could not be captured");
}

/**
 * A finished report for `question`, from the engine once and from the
 * capture thereafter.
 */
export async function reportFor(
  page: Page,
  question: string,
  options: {
    /**
     * Applied after the reset and before the report renders.
     *
     * `motion.spec.ts` needs `prefers-reduced-motion` active *while* the
     * report appears -- an entrance that has already started keeps its
     * original duration, and the reset puts media emulation back so that
     * one spec cannot leave the shared session under a preference the next
     * never asked for. Passing it here is the only point that satisfies
     * both.
     */
    media?: Parameters<Page["emulateMedia"]>[0];
    /**
     * Likewise applied before the report renders, and for the same kind of
     * reason: Vega resolves `--series-*` at embed time and writes the
     * result inline on the SVG, so a chart embedded in light and then
     * switched to dark keeps the light ramp. A test asserting that every
     * chart colour comes from the dark palette has to have been dark when
     * the chart was drawn.
     */
    theme?: "light" | "dark";
  } = {},
): Promise<void> {
  const { media, theme } = options;
  const shown = page.getByTestId("report-question");
  if ((await shown.count()) > 0) {
    const asked = ((await shown.first().textContent()) ?? "").trim();
    const wanted = theme ?? "light";
    /*
     * Already on screen, in the theme being asked for: close whatever is
     * over it and hand it back.
     *
     * The theme has to be part of this test rather than a reason to skip
     * the fast path. `visualReview.spec.ts` walks twelve cells grouped by
     * theme, so eleven of them already have the report they want, and
     * treating any requested theme as a miss made every one of them reset
     * to the composer and render again. On Chromium that is a few hundred
     * milliseconds; on WebKit it is seconds, repeated across three
     * matrices, and it is what pushed WebKit's heaviest tests past their
     * timeout on a slow machine.
     *
     * A theme *change* still goes the long way round, because Vega
     * resolves `--series-*` at embed time and writes them inline: a chart
     * drawn in light and then switched to dark keeps the light ramp.
     */
    const current = await page.locator("html").getAttribute("data-theme");
    if (asked === question && !media && current === wanted) {
      await closeAnySheet(page);
      await page.emulateMedia({ media: "screen", reducedMotion: "no-preference" });
      return;
    }
  }

  await resetToComposer(page);
  if (media) await page.emulateMedia(media);
  if (theme) await setTheme(page, theme);

  const key = `${await datasetKey(page)}\u0000${question}`;
  const remembered = captured.get(key);
  if (remembered) {
    const release = await answerRunWith(page, remembered, "capture");
    await ask(page, question);
    await waitForReport(page);
    // The route comes off: the report is rendered, and leaving it installed
    // would answer the *next* question with this one's result.
    await release();
    return;
  }

  // Armed before the run, not after it: see `armCapture`.
  const armed = armCapture(page);
  await ask(page, question);
  const payload = await capturePayload(page, armed);
  await waitForReport(page);
  assertReplayable(payload, question);
  captured.set(key, payload);
  // Registered against its sha, so a later replay of it reconciles against
  // something the guard can see was captured from a real server response.
  registerPayload(payload, "capture");
}

/**
 * Everything a replay has to keep, checked at the moment of capture.
 *
 * A capture is served back to the application as though the engine had
 * produced it, so a capture taken too early -- while the run is still
 * going, or from an error body -- would be replayed for the rest of the
 * job as a finished result. These are the fields `RunPayload` in
 * `src/lib/types.ts` declares non-optional, plus the ones that carry the
 * provenance and the terminal classification the redesign asserts on. A
 * payload missing any of them is not a result; it is a stage of one.
 *
 * Checked here rather than in the replay path because the capture is the
 * thing that must be sound: by replay time the original response is gone
 * and there is nothing left to compare against.
 */
const REQUIRED_FIELDS = [
  "run_id",
  "question",
  "dataset",
  "report",
  "findings",
  "rejected",
  "charts",
  "tasks",
  "results",
  "mcp_trace",
  "events",
  "metrics",
  "stopped_reason",
] as const;

/** Present, possibly null: how the run ended and what it ran against. */
const PROVENANCE_FIELDS = [
  "status",
  "outcome",
  "query_contract",
  "question_coverage",
  "presentation",
  "timings",
] as const;

function assertReplayable(payload: Record<string, unknown>, question: string): void {
  const missing = REQUIRED_FIELDS.filter((field) => !(field in payload));
  expect(
    missing,
    `the captured payload for "${question}" is missing ${missing.join(", ")}, so ` +
      "it is not a finished run and must not be replayed as one",
  ).toEqual([]);

  const unprovenanced = PROVENANCE_FIELDS.filter((field) => !(field in payload));
  expect(
    unprovenanced,
    `the captured payload for "${question}" carries no ${unprovenanced.join(", ")}, ` +
      "so a replay of it would assert a provenance the engine never stated",
  ).toEqual([]);

  expect(
    payload.status,
    `the captured payload for "${question}" is still running`,
  ).not.toBe("running");
}

/**
 * Reset the shared page before each test that uses it.
 *
 * Exported rather than applied here, because a spec that does not take
 * `profiled` must not pay for the fixture: referencing it in a global
 * `beforeEach` would instantiate the upload for every test in the file.
 */
export async function freshComposer(page: Page): Promise<void> {
  await resetToComposer(page);
}

export { expect };
