/**
 * A browser pass over a deployment that costs the deployment nothing.
 *
 * The public ceilings are a few uploads an hour and the point of this sweep
 * is presentation, not engine behaviour -- so **not one upload, analysis,
 * comparison or provider call leaves this harness.** That is enforced
 * rather than intended: every `/api/**` request goes through one handler,
 * anything a staged fixture does not claim is allowed through only if it is
 * a free read, and everything else is recorded as a violation and aborted
 * before it reaches the network. The test then fails on the record.
 *
 * Fail-closed matters more here than in the CI suite. There the budget
 * guard reconciles afterwards against a container nobody else is using;
 * here a mistake spends a public deployment's capacity and cannot be
 * undone by noticing it later.
 *
 * **Where the content comes from.**
 *
 *   successful reports   the deployment's own recordings, read over
 *                        `GET /api/recordings/{id}`, which is free. A real
 *                        run this build produced, not a payload we wrote.
 *   Compare              both sides assembled from a recording, with the
 *                        right side perturbed so the structured diff has
 *                        something to show.
 *   terminal states      the committed fixtures in `src/test/runs/states`,
 *                        captured from a local server in fake mode.
 *   the session          a committed capture of `POST /api/datasets/demo`
 *                        taken from a local server, so reaching the
 *                        composer costs the deployment no session.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { test as base, expect, type Page, type Route } from "@playwright/test";

import type { Theme } from "./matrix";

const HERE = dirname(fileURLToPath(import.meta.url));
const STATES_DIR = join(HERE, "..", "src", "test", "runs", "states");

/** Reads that cost the deployment nothing and may reach it. */
const FREE_READ = /^\/api\/(health|config|recordings(\/[^/]+)?)$/;

export const SESSION: Record<string, unknown> = JSON.parse(
  readFileSync(join(HERE, "fixtures", "session.json"), "utf8"),
);

export type TerminalState =
  | "refused"
  | "no-findings"
  | "verification-withheld"
  | "quota-stopped"
  | "failed"
  | "cancelled";

export function terminalFixture(name: TerminalState): Record<string, unknown> {
  return JSON.parse(readFileSync(join(STATES_DIR, `${name}.json`), "utf8"));
}

/**
 * A handler a test stages for this page. Returns true if it answered.
 *
 * Stages are consulted newest first, so a test can narrow an earlier stage
 * without unrouting it.
 */
type Stager = (route: Route, url: URL, method: string) => Promise<boolean>;

const stages = new WeakMap<Page, Stager[]>();
const violations = new WeakMap<Page, string[]>();

function stage(page: Page, stager: Stager): void {
  stages.get(page)?.push(stager);
}

/** What this page tried to send that the guard refused to let out. */
export function refusedTraffic(page: Page): string[] {
  return [...(violations.get(page) ?? [])];
}

export const test = base.extend<{
  /** The width and theme this project stands for, for naming artefacts. */
  cell: { width: number; theme: Theme; name: string };
}>({
  cell: async ({}, use, testInfo) => {
    const match = /^w(\d+)-(light|dark)$/.exec(testInfo.project.name);
    if (!match) {
      throw new Error(
        `Project "${testInfo.project.name}" is not a width/theme cell; ` +
          "the report checker parses these names.",
      );
    }
    await use({
      width: Number(match[1]),
      theme: match[2] as Theme,
      name: testInfo.project.name,
    });
  },

  page: async ({ page, cell }, use) => {
    stages.set(page, []);
    violations.set(page, []);

    // Light is the product default; dark is a remembered choice. Seeding it
    // before the first script runs puts the page in the theme from its
    // first paint, rather than letting it repaint after a click.
    await page.addInitScript((theme) => {
      try {
        window.localStorage.setItem("aae-theme", theme);
      } catch {
        /* private mode; the toggle still works */
      }
    }, cell.theme);

    await page.route("**/api/**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const method = request.method();

      for (const stager of [...(stages.get(page) ?? [])].reverse()) {
        if (await stager(route, url, method)) return;
      }

      if ((method === "GET" || method === "HEAD") && FREE_READ.test(url.pathname)) {
        await route.continue();
        return;
      }

      /*
       * Everything else. Not continued, not fulfilled with something
       * plausible -- recorded and aborted, so the test fails naming what
       * it tried to send rather than quietly spending.
       */
      violations.get(page)?.push(`${method} ${url.pathname}`);
      await route.abort();
    });

    await use(page);

    expect(
      refusedTraffic(page),
      "this sweep must send the deployment nothing but free reads",
    ).toEqual([]);
  },
});

export { expect };

/* ------------------------------------------------------------- staging */

/**
 * Answer the session endpoints from the committed capture.
 *
 * `POST /api/datasets/demo` on a deployment takes one of a small number of
 * concurrent upload sessions and holds it until its TTL expires. There are
 * ninety-six cells in this sweep.
 */
export function stageSession(page: Page): void {
  stage(page, async (route, url, method) => {
    if (method === "POST" && /\/api\/datasets\/(demo|upload)$/.test(url.pathname)) {
      await route.fulfill({ status: 200, json: SESSION });
      return true;
    }
    if (method === "GET" && /\/api\/datasets\/[^/]+$/.test(url.pathname)) {
      await route.fulfill({ status: 200, json: SESSION });
      return true;
    }
    // Ending a session the deployment never opened would be a 404 at best.
    if (method === "DELETE" && /\/api\/datasets\/[^/]+$/.test(url.pathname)) {
      await route.fulfill({ status: 200, json: { status: "ended" } });
      return true;
    }
    return false;
  });
}

/** The server's own SSE framing, so the page parses it the way it would live. */
function eventStream(payload: Record<string, unknown>): string {
  const events = Array.isArray(payload.events) ? payload.events : [];
  const frames = events.map((event) => {
    const typed = event as { type?: string };
    return `event: ${typed.type ?? "message"}\ndata: ${JSON.stringify(event)}\n\n`;
  });
  frames.push("event: stream_end\ndata: {}\n\n");
  return frames.join("");
}

function fulfilRun(route: Route, payload: Record<string, unknown>, runId: string) {
  return route.fulfill({
    status: 200,
    json: { ...payload, run_id: runId },
  });
}

/**
 * Answer the next analysis with `payload`, without starting one.
 *
 * The stream is answered too, rather than left to 404 against the
 * deployment: the execution graph is derived from the events the page
 * receives, so a sweep that let the stream fail would be looking at a graph
 * built from the finished payload alone and would not notice if the live
 * derivation had broken.
 */
export function stageRun(page: Page, payload: Record<string, unknown>): string {
  const runId = `run_hosted_${Math.random().toString(36).slice(2, 10)}`;
  stage(page, async (route, url, method) => {
    if (method === "POST" && url.pathname === "/api/analyses") {
      const body = route.request().postDataJSON() as { question?: string };
      await route.fulfill({
        status: 202,
        json: { run_id: runId, question: body?.question ?? "" },
      });
      return true;
    }
    if (method === "GET" && url.pathname === `/api/analyses/${runId}/events`) {
      await route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream" },
        body: eventStream(payload),
      });
      return true;
    }
    if (method === "GET" && url.pathname === `/api/analyses/${runId}`) {
      await fulfilRun(route, payload, runId);
      return true;
    }
    return false;
  });
  return runId;
}

/** Advertise AI and Compare, which no credential-free deployment offers. */
export function stageAiAvailable(page: Page): void {
  stage(page, async (route, url, method) => {
    if (method !== "GET" || url.pathname !== "/api/config") return false;
    const response = await route.fetch();
    const body = (await response.json()) as {
      capabilities: {
        modes: { mode: string; available: boolean; reason?: string; message?: string }[];
        compare_available: boolean;
        ai_limits?: unknown;
      };
    };
    body.capabilities.modes = body.capabilities.modes.map((mode) =>
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
    await route.fulfill({ status: 200, json: body });
    return true;
  });
}

/**
 * Answer a comparison from two finished payloads.
 *
 * Both sides are assembled from a recording the deployment produced; the
 * right side is perturbed by `mutate` so the structured diff has a
 * divergence to render. Nothing is started and nothing is polled for.
 */
export function stageComparison(
  page: Page,
  deterministic: Record<string, unknown>,
  ai: Record<string, unknown>,
): void {
  const detId = "run_hosted_cmp_deterministic";
  const aiId = "run_hosted_cmp_ai";
  const sides: Record<string, Record<string, unknown>> = {
    [detId]: deterministic,
    [aiId]: ai,
  };

  stage(page, async (route, url, method) => {
    if (method === "POST" && url.pathname === "/api/comparisons") {
      const body = route.request().postDataJSON() as {
        session_id?: string;
        question?: string;
      };
      await route.fulfill({
        status: 202,
        json: {
          comparison_id: "cmp_hosted_acceptance",
          session_id: body?.session_id ?? "ses_hosted_acceptance",
          question: body?.question ?? "",
          deterministic_run_id: detId,
          ai_run_id: aiId,
        },
      });
      return true;
    }

    const stream = /^\/api\/analyses\/([^/]+)\/events$/.exec(url.pathname);
    if (method === "GET" && stream && sides[stream[1]!]) {
      await route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream" },
        body: eventStream(sides[stream[1]!]!),
      });
      return true;
    }

    const run = /^\/api\/analyses\/([^/]+)$/.exec(url.pathname);
    if (method === "GET" && run && sides[run[1]!]) {
      await fulfilRun(route, sides[run[1]!]!, run[1]!);
      return true;
    }
    return false;
  });
}

/* ------------------------------------------------------------ navigation */

export async function openApp(page: Page): Promise<void> {
  await page.goto("/", { waitUntil: "commit" });
  await expect(page.getByTestId("app-shell")).toBeVisible({ timeout: 60_000 });
}

/** One of the deployment's recordings, read over the free endpoint. */
export async function readRecording(
  page: Page,
  id: string,
): Promise<Record<string, unknown>> {
  const response = await page.request.get(`/api/recordings/${id}`);
  expect(response.ok(), `GET /api/recordings/${id}`).toBe(true);
  return (await response.json()) as Record<string, unknown>;
}

/** Open a recorded run through the control a visitor would use. */
export async function openRecordedReport(page: Page, title: string): Promise<void> {
  await page
    .getByRole("button", { name: new RegExp(escapeRe(title), "i") })
    .click();
  await expect(page.getByTestId("report-panel")).toBeVisible({ timeout: 60_000 });
}

function escapeRe(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Reach the composer over the committed session, costing nothing. */
export async function openComposer(page: Page): Promise<void> {
  stageSession(page);
  await page
    .getByRole("button", { name: /Commerce demo warehouse/i })
    .click();
  await expect(page.getByLabel("Business question")).toBeVisible({
    timeout: 60_000,
  });
}

/** The product's own run control, named the way the product names it. */
export function runButton(page: Page) {
  return page.getByRole("button", { name: /^(Run analysis|Run with AI|Compare)/ });
}

/** Ask, and let a staged fixture answer. Nothing is started on the host. */
export async function ask(page: Page, question: string): Promise<void> {
  const field = page.getByLabel("Business question");
  await expect(field).toBeVisible({ timeout: 30_000 });
  await field.fill(question);
  // The composer is controlled, so a render landing after the fill can
  // revert it. Asserted rather than waited on.
  await expect(field).toHaveValue(question);
  const run = runButton(page);
  await expect(run).toBeEnabled({ timeout: 20_000 });
  await run.click();
}
