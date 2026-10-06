/**
 * Hosted visual acceptance: nine states, six widths, two themes.
 *
 * What this run is evidence about, stated once so the report is not read as
 * more than it is:
 *
 *   it proves   how the deployed build **presents** a run -- layout at
 *               every width in both themes, hierarchy, reader labels,
 *               readable dates and numbers, the evidence drawer, focus
 *               restoration, reduced motion, print, and the execution
 *               graph -- against the exact commit `preflight.ts` checked.
 *
 *   it does not prove anything about the engine's own behaviour on the
 *               deployment, because it never makes it run. Classification,
 *               planning and verification are covered by the unit suite and
 *               by the browser suite against a container built from the
 *               same commit. A hosted sweep that ran real analyses to
 *               check those would spend a public deployment's capacity to
 *               re-prove what CI already proved for nothing.
 *
 * Every test title begins with its state slug, because
 * `check-hosted-acceptance.mjs` reconciles titles against `matrix.ts` and
 * fails a run that is missing a cell. A renamed test is a failed run, not a
 * quiet gap.
 */

import { describeDefects, readerDefects } from "../src/lib/readerQuality";
import { EVIDENCE_SECTIONS } from "../src/components/EvidenceDrawer";
import {
  ask,
  expect,
  openApp,
  openComposer,
  openRecordedReport,
  readRecording,
  stageAiAvailable,
  stageComparison,
  stageRun,
  stageSession,
  terminalFixture,
  test,
} from "./fixtures";
import type { Page, TestInfo } from "@playwright/test";

/**
 * The recording the report cases use.
 *
 * A segmentation question with one withheld finding, so the report carries
 * group labels, a chart, and the "withheld" path through the graph rather
 * than only the happy one.
 */
const RECORDING_ID = "returns-segments";

/* ------------------------------------------------------------ measuring */

async function overflow(page: Page): Promise<number> {
  return page.evaluate(
    () =>
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
  );
}

/** The sideways-scroll rule the browser suite uses, at every hosted width. */
async function expectNoOverflow(page: Page, where: string): Promise<void> {
  const measured = await overflow(page);
  expect(measured, `${where}: the page scrolls sideways by ${measured}px`)
    .toBeLessThanOrEqual(1);
}

/**
 * The report's prose, with the audit surfaces and identifiers removed.
 *
 * `drop` removes further selectors, which is how the two halves of a
 * recorded report are measured apart -- see the report case for why.
 */
async function proseOf(
  page: Page,
  selector: string,
  drop: string[] = [],
): Promise<string> {
  return page.evaluate(
    ({ target, extra }) => {
      const root = document.querySelector(target);
      if (!root) return "";
      const clone = root.cloneNode(true) as HTMLElement;
      const remove = [
        "[data-print-appendix]",
        '[data-testid="evidence-drawer"]',
        ".evidence-row",
        ".sr-only",
        ".activity-row",
        ".mono",
        ...extra,
      ];
      for (const audit of clone.querySelectorAll(remove.join(", "))) {
        audit.remove();
      }
      return clone.textContent ?? "";
    },
    { target: selector, extra: drop },
  );
}

/**
 * The four reader rules that have no honest reading on a primary surface:
 * a float tail, a stored timestamp, an engine identifier, and a value that
 * never arrived.
 *
 * Planner vocabulary and punctuation are left out for the same reason the
 * browser suite leaves them out -- the canvas legitimately carries prose
 * the engine wrote -- so this and `e2e/readerQuality.spec.ts` hold the
 * deployed build and the container to the same standard.
 */
function assertFitForAReader(where: string, text: string): void {
  expect(text.trim().length, `${where} rendered nothing to check`)
    .toBeGreaterThan(0);
  const defects = readerDefects(text).filter(
    (found) =>
      found.defect === "excess_precision" ||
      found.defect === "iso_timestamp" ||
      found.defect === "snake_case" ||
      found.defect === "missing_value" ||
      // Both halves of what the scope line published:
      // `Order date None ['2025-01-01', '2025-12-31']`.
      found.defect === "serialised_collection",
  );
  expect(defects, describeDefects(where, defects)).toEqual([]);
}

async function shot(page: Page, info: TestInfo, state: string): Promise<void> {
  const file = info.outputPath(`${state}.png`);
  await page.screenshot({ path: file, fullPage: true });
  await info.attach(`${info.project.name}-${state}`, {
    path: file,
    contentType: "image/png",
  });
}

/** The theme actually applied, so a cell cannot pass in the wrong one. */
async function expectTheme(page: Page, theme: string): Promise<void> {
  await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
}

/* ------------------------------------------------------------ the sweep */

test.describe("hosted visual acceptance", () => {
  test("landing: the first screen explains the modes and does not scroll sideways", async ({
    page,
    cell,
  }, info) => {
    await openApp(page);
    await expect(page.getByTestId("landing-headline")).toBeVisible();
    await expectTheme(page, cell.theme);

    // The two ways in, both visible before anything is explained: bring a
    // file, or open something already prepared.
    await expect(page.getByTestId("dropzone")).toBeVisible();
    await expect(page.getByTestId("prepared-data")).toBeVisible();

    await expectNoOverflow(page, `landing at ${cell.name}`);
    await shot(page, info, "landing");
  });

  test("composer: the modes are explained where they are chosen", async ({
    page,
    cell,
  }, info) => {
    // AI advertised, so all three modes render and the explanation has to
    // distinguish them. A credential-free deployment offers one, which
    // would let a one-mode selector pass this.
    stageAiAvailable(page);
    stageSession(page);
    await openApp(page);
    await openComposer(page);

    await expect(page.getByTestId("mode-selector")).toBeVisible();
    // What the modes *are*. A visitor who cannot tell them apart cannot
    // choose, and on this deployment the explanation is the only thing
    // that says why one of them is a rule-based stand-in rather than a
    // model.
    await expect(page.getByTestId("mode-taxonomy")).toBeVisible();
    await expect(page.getByTestId("mode-description")).toBeVisible();

    await expectNoOverflow(page, `composer at ${cell.name}`);
    await shot(page, info, "composer");
  });

  test("report: a recorded run reads as a document, in labels a reader owns", async ({
    page,
    cell,
  }, info) => {
    await openApp(page);
    const recordings = await page.request.get("/api/config");
    const config = (await recordings.json()) as {
      recordings: { recording_id: string; title: string }[];
    };
    const recording = config.recordings.find(
      (item) => item.recording_id === RECORDING_ID,
    );
    expect(recording, `the deployment offers ${RECORDING_ID}`).toBeTruthy();

    await openRecordedReport(page, recording!.title);
    await expectTheme(page, cell.theme);

    // One headline, and it is the first thing.
    const panel = page.getByTestId("report-panel");
    await expect(panel.getByTestId("direct-answer")).toBeVisible();

    /*
     * Reader quality on a recorded run, with no allowance.
     *
     * There used to be one. A recording carried no presentation snapshot,
     * so the whole report -- headline, findings, chart title, table
     * headers -- fell back to the engine's own words and the result's own
     * column names: `return_rate fell from 8.51% in 2025-01-01`. The
     * sweep bounded that to two defect kinds rather than ignoring it.
     *
     * It is fixed rather than bounded now. The presentation layer types
     * every derived column from what it derives from, recordings carry
     * the snapshot, and a replayed report reads exactly as the run that
     * produced it did. So the exemption is gone, and this is the same
     * assertion `e2e/readerQuality.spec.ts` makes of a live run.
     */
    const prose = await proseOf(page, '[data-testid="report-panel"]');
    assertFitForAReader(`recorded report at ${cell.name}`, prose);

    // The chart, if the recording produced one, stays inside the column.
    const chart = panel.locator(".chart-host").first();
    if (await chart.count()) {
      const [box, column] = await Promise.all([
        chart.boundingBox(),
        panel.boundingBox(),
      ]);
      expect(box!.width, `chart at ${cell.name}`).toBeLessThanOrEqual(
        column!.width + 1,
      );
    }

    await expectNoOverflow(page, `report at ${cell.name}`);
    await shot(page, info, "report");
  });

  test("execution-graph: the graph is derived from events and sits under the answer", async ({
    page,
    cell,
  }, info) => {
    await openApp(page);
    const recording = await readRecording(page, RECORDING_ID);
    await openRecordedReport(page, String(recording.title));

    // Not above the answer. The canvas carries the argument; the machinery
    // is a disclosure reached through one control.
    expect(
      await page
        .locator('[data-testid="execution-graph"]')
        .locator("visible=true")
        .count(),
      "the execution graph is resident on the canvas",
    ).toBe(0);

    await page.getByTestId("show-work").click();
    const drawer = page.getByTestId("evidence-drawer");
    await expect(drawer).toBeVisible();

    const region = drawer.getByRole("region", {
      name: "How this analysis ran",
    });
    await expect(region).toBeVisible();
    const graph = region.getByTestId("execution-graph");
    await expect(graph).toBeVisible();

    // One node per call the run actually made -- the recording's own count,
    // read from the payload rather than assumed.
    const expectedCalls = (recording.events as { type: string }[]).filter(
      (event) => event.type === "mcp_tool_called",
    ).length;
    await expect(graph.getByTestId("graph-call")).toHaveCount(expectedCalls);

    // Every node states its outcome in words, so the four states survive a
    // monochrome page and a reader who cannot separate the colours.
    for (const node of await graph.getByTestId("graph-call").all()) {
      const text = (await node.textContent()) ?? "";
      expect(text.trim().length, "a graph node rendered no outcome")
        .toBeGreaterThan(0);
      expect(await node.getAttribute("data-call-state")).toMatch(
        /^(completed|failed|refused|suppressed|running)$/,
      );
    }

    // The accessible alternative, reachable from the keyboard.
    const alternative = graph.getByTestId("graph-text-alternative");
    await expect(alternative).toBeVisible();
    await alternative.getByRole("group").or(alternative).first().isVisible();
    await alternative.locator("summary").focus();
    await expect(alternative.locator("summary")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(graph.getByTestId("graph-narrative")).toBeVisible();

    await expectNoOverflow(page, `execution graph at ${cell.name}`);
    await shot(page, info, "execution-graph");
  });

  test("evidence-drawer: every datum the canvas gave up is in the sheet", async ({
    page,
    cell,
  }, info) => {
    await openApp(page);
    const recording = await readRecording(page, RECORDING_ID);
    await openRecordedReport(page, String(recording.title));
    await page.getByTestId("show-work").click();

    const drawer = page.getByTestId("evidence-drawer");
    await expect(drawer).toBeVisible();
    const body = (await drawer.textContent()) ?? "";
    const missing = EVIDENCE_SECTIONS.filter(
      (section) => !body.includes(section),
    );
    expect(missing, `evidence sections missing at ${cell.name}`).toEqual([]);

    await expectNoOverflow(page, `evidence drawer at ${cell.name}`);
    await shot(page, info, "evidence-drawer");
  });

  test("focus-restoration: closing the sheet returns focus to the control that opened it", async ({
    page,
    cell,
  }, info) => {
    await openApp(page);
    const recording = await readRecording(page, RECORDING_ID);
    await openRecordedReport(page, String(recording.title));

    const trigger = page.getByTestId("show-work");
    await trigger.click();
    await expect(page.getByTestId("evidence-drawer")).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(page.getByTestId("evidence-drawer")).toHaveCount(0);
    await expect(
      trigger,
      `focus was not restored to Show work at ${cell.name}`,
    ).toBeFocused();

    await shot(page, info, "focus-restoration");
  });

  test("compare: two strategies, each with its own graph", async ({
    page,
    cell,
  }, info) => {
    stageAiAvailable(page);
    stageSession(page);
    await openApp(page);

    // Both sides come from a run this deployment produced. The right side
    // is perturbed so the structured diff has a divergence to render;
    // nothing is started and no provider is contacted.
    const recording = await readRecording(page, RECORDING_ID);
    const ai = structuredClone(recording);
    const report = ai.report as { headline?: string } | undefined;
    if (report?.headline) {
      report.headline = `${report.headline} The two strategies did not agree.`;
    }
    stageComparison(page, recording, ai);

    await openComposer(page);
    await page.getByRole("radio", { name: /^Compare/ }).click();
    await ask(page, "Which customer segment returns most?");

    const workspace = page.getByTestId("compare-workspace");
    await expect(workspace).toBeVisible({ timeout: 60_000 });

    await page.getByTestId("inspect-both-traces").click();
    const drawer = page.getByTestId("compare-evidence-drawer");
    await expect(drawer).toBeVisible();

    // One graph per strategy: one per tab, and switching tabs runs nothing.
    const tabs = drawer.getByRole("tab");
    await expect(tabs).toHaveCount(2);
    for (let index = 0; index < 2; index += 1) {
      await tabs.nth(index).click();
      await expect(
        drawer.getByTestId("execution-graph"),
        `strategy ${index + 1} has no execution graph at ${cell.name}`,
      ).toHaveCount(1);
    }

    await expectNoOverflow(page, `compare at ${cell.name}`);
    await shot(page, info, "compare");
  });

  test("terminal-refused: a refusal says what it refused, at every width", async ({
    page,
    cell,
  }, info) => {
    stageSession(page);
    await openApp(page);
    stageRun(page, terminalFixture("refused"));

    await openComposer(page);
    await ask(page, "Why did margin fall in Q3?");

    const state = page.getByTestId("terminal-state");
    await expect(state).toBeVisible({ timeout: 60_000 });

    const prose = await proseOf(page, '[data-testid="terminal-state"]');
    assertFitForAReader(`refusal prose at ${cell.name}`, prose);

    await expectNoOverflow(page, `refusal at ${cell.name}`);
    await shot(page, info, "terminal-refused");
  });

  test("reduced-motion: the same page, with nothing moving", async ({
    page,
    cell,
  }, info) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await openApp(page);
    const recording = await readRecording(page, RECORDING_ID);
    await openRecordedReport(page, String(recording.title));
    await page.getByTestId("show-work").click();
    // Scoped to the drawer: the print appendix holds a second copy of the
    // graph, hidden on screen but present in the document.
    await expect(
      page.getByTestId("evidence-drawer").getByTestId("execution-graph"),
    ).toBeVisible();

    const moving = await page.evaluate(() =>
      [...document.querySelectorAll("body *")]
        .filter((element) => {
          const style = getComputedStyle(element);
          const duration = Number.parseFloat(style.animationDuration);
          const iterations = style.animationIterationCount;
          return (
            (Number.isFinite(duration) && duration > 0.05) ||
            iterations === "infinite"
          );
        })
        .slice(0, 8)
        .map(
          (element) =>
            `${element.tagName.toLowerCase()}.${String(element.className || "").split(" ")[0]}`,
        ),
    );
    expect(moving, `still animating under reduced motion at ${cell.name}`)
      .toEqual([]);

    await expectNoOverflow(page, `reduced motion at ${cell.name}`);
    await shot(page, info, "reduced-motion");
  });

  test("print: paper carries the execution graph and none of the controls", async ({
    page,
    cell,
  }, info) => {
    await openApp(page);
    const recording = await readRecording(page, RECORDING_ID);
    await openRecordedReport(page, String(recording.title));
    await page.emulateMedia({ media: "print" });

    const appendix = page.locator("[data-print-appendix]").first();
    await expect(appendix).toBeVisible();

    // The graph is in the appendix, with its text alternative open: a
    // disclosure printed closed is a paragraph the reader cannot reach.
    const graph = appendix.getByTestId("execution-graph");
    await expect(graph).toBeVisible();
    await expect(graph.getByTestId("graph-narrative")).toBeVisible();

    // The stage sequence prints too. It is hidden on paper where it is a
    // progress indicator; inside the graph it is the record of a run.
    await expect(graph.getByTestId("run-timeline")).toBeVisible();

    // Controls are not things that exist on paper.
    expect(
      await page.locator(".no-print:visible").count(),
      `controls printed at ${cell.name}`,
    ).toBe(0);

    /*
     * No sideways-scroll assertion here, and that is deliberate.
     *
     * `expectNoOverflow` measures the document against the *screen*
     * viewport. Under print media the page is a sheet of paper -- around
     * 816px at 96dpi -- not the 360px phone the reader happened to print
     * from, so measuring the phone's width against print layout asks a
     * question about a page that does not exist. It failed four cells
     * saying the page scrolled 288px sideways, which was true of the
     * emulated viewport and false of the paper.
     *
     * What matters on paper is the opposite property: the result table
     * must *not* be clipped. On screen it scrolls inside its own frame;
     * `print.css` releases that so the whole table prints. Asserted here,
     * because releasing it is what makes the printed table complete and
     * removing it would be a silent loss of data on paper.
     */
    const unclipped = await page.evaluate(() => {
      const wrap = document.querySelector(".table-wrap");
      if (!wrap) return { present: false, overflow: "", maxHeight: "" };
      const style = getComputedStyle(wrap);
      return {
        present: true,
        overflow: style.overflowX,
        maxHeight: style.maxHeight,
      };
    });
    if (unclipped.present) {
      expect(unclipped.overflow, `the printed table is clipped at ${cell.name}`)
        .toBe("visible");
      expect(unclipped.maxHeight, `the printed table is cropped at ${cell.name}`)
        .toBe("none");
    }

    await shot(page, info, "print");
    await page.emulateMedia({ media: null });
  });
});
