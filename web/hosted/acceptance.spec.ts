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
  stageRunInFlight,
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

/* -------------------------------------------------------- bounding boxes
 *
 * The sweep already catches a page that scrolls sideways. These catch the
 * two failures that fit *inside* the viewport and are still broken: boxes
 * that sit on top of each other, and boxes squeezed below the width a word
 * needs.
 *
 * Both are measured from the rendered layout rather than asserted about
 * the stylesheet, because that is the only place they exist. A grid that
 * collapses to four 70px columns is valid CSS.
 */

/** Siblings that overlap each other by more than a hairline. */
async function overlappingSiblings(page: Page, selector: string): Promise<string[]> {
  return page.evaluate((target) => {
    const found: string[] = [];
    const groups = new Map<Element, Element[]>();
    for (const node of document.querySelectorAll(target)) {
      if (!node.parentElement) continue;
      const siblings = groups.get(node.parentElement) ?? [];
      siblings.push(node);
      groups.set(node.parentElement, siblings);
    }
    const name = (node: Element) =>
      `${node.tagName.toLowerCase()}.${String(node.className || "").split(" ")[0]}`;

    for (const siblings of groups.values()) {
      for (let i = 0; i < siblings.length; i += 1) {
        for (let j = i + 1; j < siblings.length; j += 1) {
          const a = siblings[i]!.getBoundingClientRect();
          const b = siblings[j]!.getBoundingClientRect();
          if (a.width === 0 || b.width === 0 || a.height === 0 || b.height === 0) continue;
          const across = Math.min(a.right, b.right) - Math.max(a.left, b.left);
          const down = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
          // One pixel of shared edge is a border, not an overlap.
          if (across > 1 && down > 1) {
            found.push(
              `${name(siblings[i]!)} over ${name(siblings[j]!)} by ` +
                `${Math.round(across)}x${Math.round(down)}px`,
            );
          }
        }
      }
    }
    return found;
  }, selector);
}

/**
 * Boxes whose own content does not fit inside them.
 *
 * Measured against the content, not against a pixel threshold. A first
 * attempt asserted a 40px minimum and flagged the result table's
 * row-number column -- `#`, then `0`, `1`, `2`, `3` -- at 27px, which is
 * exactly as wide as it should be. "Too narrow" only means anything
 * relative to what a box is holding.
 */
async function clipped(page: Page, selector: string): Promise<string[]> {
  return page.evaluate(
    (target) =>
      [...document.querySelectorAll(target)]
        .filter((node) => (node.textContent ?? "").trim().length > 0)
        .filter((node) => {
          const box = node.getBoundingClientRect();
          if (box.height === 0 || box.width === 0) return false;
          const style = getComputedStyle(node);
          // A deliberate scroll container is not a clipped box; it is a
          // box that said it would scroll.
          if (style.overflowX === "auto" || style.overflowX === "scroll") return false;
          return node.scrollWidth > node.clientWidth + 1;
        })
        .map(
          (node) =>
            `${node.tagName.toLowerCase()}.${String(node.className || "").split(" ")[0]} ` +
            `holds ${node.scrollWidth}px in ${node.clientWidth}px`,
        ),
    selector,
  );
}

/** Prose boxes narrower than a line of text needs. */
async function unreadableProse(
  page: Page,
  selector: string,
  least: number,
): Promise<string[]> {
  return page.evaluate(
    ({ target, least: minimum }) =>
      [...document.querySelectorAll(target)]
        .filter((node) => (node.textContent ?? "").trim().length > 24)
        .filter((node) => {
          const box = node.getBoundingClientRect();
          return box.height > 0 && box.width > 0 && box.width < minimum;
        })
        .map(
          (node) =>
            `${node.tagName.toLowerCase()}.${String(node.className || "").split(" ")[0]} ` +
            `at ${Math.round(node.getBoundingClientRect().width)}px`,
        ),
    { target: selector, least },
  );
}

/**
 * Wait for the chart to actually paint, and prove it did.
 *
 * Vega is a lazily-loaded 860 kB chunk, so a report is interactive and
 * readable well before its chart exists. Every screenshot of a report in
 * the first production sweep showed a **blank 320px band** where the chart
 * belongs -- the page was fine, the capture was early.
 *
 * That is two defects in one. The artefacts were not faithful evidence of
 * the thing they were filed as evidence of; and the `report` case's only
 * chart assertion was that its box is no wider than the column, which an
 * empty box satisfies. So this waits, and then asserts the mark is really
 * there -- a chart that silently stops drawing now fails the cell instead
 * of passing it at full width.
 *
 * Returns false when the result legitimately has no chart, which is a
 * decision the engine records and states.
 */
async function chartPainted(page: Page, where: string): Promise<boolean> {
  const host = page.locator(".chart-host").first();
  if ((await host.count()) === 0) return false;

  const svg = host.locator("svg").first();
  await expect(svg, `${where}: the chart never painted`).toBeVisible({
    timeout: 30_000,
  });

  // An <svg> with no marks is still an <svg>. Vega draws the axes as path
  // and text nodes, so a drawn chart has both.
  const drawn = await host.evaluate((node) => ({
    paths: node.querySelectorAll("svg path").length,
    labels: node.querySelectorAll("svg text").length,
  }));
  expect(drawn.paths, `${where}: the chart drew no marks`).toBeGreaterThan(0);
  expect(drawn.labels, `${where}: the chart drew no labels`).toBeGreaterThan(0);
  return true;
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

    // Four selectable cards, each saying what it is for without being
    // chosen first, and exactly one of them marked as chosen.
    const cards = page.locator(".mode-option");
    await expect(cards).toHaveCount(4);
    await expect(page.locator(".mode-option.selected")).toHaveCount(1);
    for (const purpose of await page.locator(".mode-option-purpose").all()) {
      await expect(purpose).toBeVisible();
    }

    // Governed is the default. On a credential-free deployment it is also
    // the only one that can run, which is exactly when a reader most needs
    // the others to explain why they cannot.
    await expect(page.locator("#mode-auto")).toBeChecked();

    // The explanation is one control away, not resident. A visitor who
    // cannot tell the modes apart has somewhere to go; a visitor who can
    // is not made to scroll past three paragraphs first.
    await expect(page.getByTestId("mode-taxonomy")).toBeHidden();
    await page.getByTestId("mode-explainer").locator("summary").click();
    await expect(page.getByTestId("mode-taxonomy")).toBeVisible();
    await expect(page.getByTestId("mode-description")).toBeVisible();

    await expectNoOverflow(page, `composer at ${cell.name}`);
    await shot(page, info, "composer");
  });


  test("layout: nothing overlaps, and no text box is narrower than a word", async ({
    page,
    cell,
  }, info) => {
    await openApp(page);
    const recording = await readRecording(page, RECORDING_ID);
    await openRecordedReport(page, String(recording.title));

    /*
     * The sibling groups that actually lay out beside each other, and are
     * therefore the ones a narrow viewport can collide: the report's
     * bands, the highlight list, the table's cells, the header controls
     * and the action row. A blanket sweep of every element reports every
     * parent as overlapping its children, which is not a defect.
     */
    const groups = [
      ".report > *",
      ".finding-list > .finding-item",
      "table.data tr > *",
      ".report-actions > *",
      ".topbar > *",
      ".composer-chips > *",
    ];
    for (const group of groups) {
      expect(
        await overlappingSiblings(page, group),
        `${group} overlaps at ${cell.name}`,
      ).toEqual([]);
    }

    // No box holds more than it can show. The row-number column is 27px
    // wide and correct at that width, because it holds one digit; what is
    // wrong is a box whose own content does not fit.
    expect(
      await clipped(page, ".report p, .report li, .report td, .report th, .mode-option label"),
      `clipped boxes at ${cell.name}`,
    ).toEqual([]);

    // And a sentence has a line to sit on. 180px is about four words at
    // this type scale; narrower than that is a column, not prose.
    expect(
      await unreadableProse(page, ".report p, .report li", 180),
      `prose squeezed below a readable width at ${cell.name}`,
    ).toEqual([]);

    // And the prose column itself stays readable rather than collapsing
    // next to the chart.
    const answer = await page.getByTestId("direct-answer").boundingBox();
    expect(
      answer!.width,
      `the answer is ${Math.round(answer!.width)}px wide at ${cell.name}`,
    ).toBeGreaterThanOrEqual(Math.min(260, cell.width - 32));

    await chartPainted(page, `layout at ${cell.name}`);
    await expectNoOverflow(page, `layout at ${cell.name}`);
    await shot(page, info, "layout");
  });

  test("focus-visible: every stop on the keyboard tour is visible and on screen", async ({
    page,
    cell,
  }, info) => {
    await openApp(page);
    const recording = await readRecording(page, RECORDING_ID);
    await openRecordedReport(page, String(recording.title));

    /*
     * Tabbed, not queried.
     *
     * `:focus-visible` only matches when the browser decides focus should
     * be shown, which a script setting `.focus()` does not always
     * trigger. Pressing Tab is what a keyboard user does, so it is what
     * this does -- and it also exercises the order, which is the other
     * half of focus safety.
     */
    const seen: string[] = [];
    const invisible: string[] = [];
    const offscreen: string[] = [];

    for (let step = 0; step < 25; step += 1) {
      await page.keyboard.press("Tab");
      const stop = await page.evaluate(() => {
        const node = document.activeElement;
        if (!node || node === document.body) return null;
        const style = getComputedStyle(node);
        const box = node.getBoundingClientRect();
        return {
          name:
            `${node.tagName.toLowerCase()}.${String(node.className || "").split(" ")[0]}`,
          outline: Number.parseFloat(style.outlineWidth) || 0,
          outlineStyle: style.outlineStyle,
          shadow: style.boxShadow,
          left: box.left,
          right: box.right,
          width: box.width,
          height: box.height,
          viewport: document.documentElement.clientWidth,
        };
      });
      if (!stop) break;
      if (seen.includes(`${step}:${stop.name}`)) continue;
      seen.push(`${step}:${stop.name}`);

      const ringed =
        (stop.outline > 0 && stop.outlineStyle !== "none") ||
        (stop.shadow !== "none" && stop.shadow !== "");
      if (!ringed) invisible.push(stop.name);

      // A focus ring on a control the reader cannot see is not a ring.
      if (stop.width > 0 && (stop.right < 0 || stop.left > stop.viewport)) {
        offscreen.push(stop.name);
      }
    }

    expect(seen.length, `nothing was focusable at ${cell.name}`).toBeGreaterThan(2);
    expect(invisible, `focused with no visible ring at ${cell.name}`).toEqual([]);
    expect(offscreen, `focused off screen at ${cell.name}`).toEqual([]);

    await shot(page, info, "focus-visible");
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

    // The chart, if the recording produced one: painted, then inside the
    // column. Measured after it has drawn -- an empty host is the right
    // width and the wrong picture.
    const chart = panel.locator(".chart-host").first();
    if (await chartPainted(page, `report at ${cell.name}`)) {
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

    await page.getByTestId("inspect-evidence").click();
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
    await page.getByTestId("inspect-evidence").click();

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

    const trigger = page.getByTestId("inspect-evidence");
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

    await chartPainted(page, `compare at ${cell.name}`);
    await expectNoOverflow(page, `compare at ${cell.name}`);
    await shot(page, info, "compare");
  });

  test("ai-in-progress: a waiting run says what it has done, and never fakes a bar", async ({
    page,
    cell,
  }, info) => {
    /*
     * The state a reader spends the longest in and the one the sweep could
     * not reach until now.
     *
     * An AI run can sit on one stage for half a minute while a provider
     * thinks, and an unchanged picture is what a hung request looks like
     * too. What is asserted here is that the screen is made of facts: the
     * stage the engine reported, the reader's own elapsed wait, and the
     * work that has actually finished -- and that there is no completion
     * fraction anywhere, because the planner decides how many calls a run
     * makes as it goes.
     */
    stageAiAvailable(page);
    stageSession(page);
    await openApp(page);

    // A real run's events, cut off part way: the engine's own sequence up
    // to the point where two queries have returned.
    const recording = await readRecording(page, RECORDING_ID);
    const upTo = (recording.events as { type: string }[]).findIndex(
      (event) => event.type === "finding_verified",
    );
    expect(upTo, "the recording has no partial point to stop at").toBeGreaterThan(3);
    stageRunInFlight(page, recording, upTo);

    await openComposer(page);
    await page.getByRole("radio", { name: /^AI Analytics/ }).click();
    await ask(page, "Which customer segment returns most?");

    const progress = page.getByTestId("run-progress");
    await expect(progress).toBeVisible({ timeout: 60_000 });

    // The stage, in the engine's own words rather than a spinner.
    const stage = page.getByTestId("run-progress-stage");
    await expect(stage).toBeVisible();
    await expect(stage).not.toHaveText("Starting");

    // The work that finished. These numbers came from events; the run is
    // deliberately cut off before `finding_verified`, so the queries are
    // counted and the findings are not.
    const work = page.getByTestId("run-progress-work");
    await expect(work).toBeVisible();
    await expect(work).toContainText(/quer(y|ies) returned/);
    await expect(work).not.toContainText(/finding verified/);

    // Why it is waiting, with no estimate: the provider does not give one.
    await expect(page.getByTestId("run-progress-note")).toContainText(/cloud model/i);

    /*
     * And nothing simulated. A percentage, a `progressbar`, or a figure
     * that moves while only time passes would each be the one claim this
     * product cannot afford to get wrong.
     */
    expect(await page.locator('[role="progressbar"], progress').count()).toBe(0);

    /*
     * The figures named, not the whole block minus a substring.
     *
     * A first version compared `innerText()` before and after with the
     * elapsed string removed by `String.replace`, which replaces only the
     * first occurrence -- so once the clock read a value that also appeared
     * elsewhere in the block the subtraction took out the wrong text and
     * the comparison failed on one cell in twelve. The assertion was
     * fragile; the product was not. Naming the two figures that must not
     * move says what the claim is and cannot misfire.
     */
    const stageBefore = await page.getByTestId("run-progress-stage").textContent();
    const workBefore = await page.getByTestId("run-progress-work").textContent();
    const marksBefore = await progress.locator('[data-state="complete"]').count();
    const elapsedBefore = await page.getByTestId("run-progress-elapsed").textContent();

    await page.waitForTimeout(3500);

    expect(
      await page.getByTestId("run-progress-elapsed").textContent(),
      "the elapsed clock did not advance",
    ).not.toBe(elapsedBefore);
    expect(
      await page.getByTestId("run-progress-stage").textContent(),
      `the stage moved while only time passed at ${cell.name}`,
    ).toBe(stageBefore);
    expect(
      await page.getByTestId("run-progress-work").textContent(),
      `the work count moved while only time passed at ${cell.name}`,
    ).toBe(workBefore);
    expect(
      await progress.locator('[data-state="complete"]').count(),
      `a stage completed while only time passed at ${cell.name}`,
    ).toBe(marksBefore);

    // The answer is not on screen, because there is not one yet.
    await expect(page.getByTestId("report-panel")).toHaveCount(0);

    await expectNoOverflow(page, `in progress at ${cell.name}`);
    await shot(page, info, "ai-in-progress");
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
    await page.getByTestId("inspect-evidence").click();
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

    await chartPainted(page, `reduced motion at ${cell.name}`);
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

    // Painted before the media switch: a chart that is still loading when
    // print styles apply prints as a blank band, which is exactly what
    // the first production sweep captured.
    await chartPainted(page, `print at ${cell.name}`);
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
