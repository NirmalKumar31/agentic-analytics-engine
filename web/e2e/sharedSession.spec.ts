import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, freshComposer, test } from "./fixtures";

/**
 * The shared session survives the tests that borrow it.
 *
 * Most of this suite runs against a worker-scoped page: one upload, one
 * server-side session, handed back once at the end of the worker. That is
 * what took the browser job's uploads per engine down by an order of
 * magnitude, and
 * it is only safe while no individual test destroys the thing it borrowed.
 *
 * It was not safe. `page.close()` is the ordinary line to write against a
 * test-scoped page, and against a shared one it fails every test that
 * comes after it, with "Target page, context or browser has been
 * closed", reported in the *victim*, naming a locator that is plainly
 * present in the screenshot. The culprit passes. The upload session it
 * took with it is reported separately, by the budget guard, as a number
 * that does not reconcile, hours later and in a different file.
 *
 * So the fixture refuses the close at the line that attempts it, and this
 * is the regression test for that refusal: the first test tries, the
 * second proves the session came through. Delete the guard in
 * `fixtures.ts` and the second test fails.
 *
 * Neither test uploads or analyses anything.
 */
/**
 * The accounting records what the page really sent.
 *
 * Every other claim about this suite's cost rests on the traffic recorder,
 * and a recorder that silently stopped working would make the budget
 * smaller and the guard happier. So the ledger is read back: by the time
 * this runs, the engine has admitted real analyses and replayed captured
 * ones, and both must be on the record with the classification they
 * actually had.
 *
 * Written against the file rather than an in-memory counter on purpose --
 * the file is what the guard reads, and a number that only exists in the
 * worker's memory proves nothing about it.
 */
test("the ledger records both real admissions and replays", async ({}, info) => {
  const path = join(
    dirname(fileURLToPath(import.meta.url)),
    "..",
    "playwright-ledger",
    `${info.project.name}.jsonl`,
  );
  expect(
    existsSync(path),
    `no ledger at ${path}: the suite is not recording what it spends`,
  ).toBe(true);

  const records = readFileSync(path, "utf8")
    .split("\n")
    .filter((line) => line.trim() !== "")
    .map((line) => JSON.parse(line) as Record<string, unknown>);

  const responses = records.filter((record) => record.event === "response");
  const sources = new Set(responses.map((record) => record.source));
  expect(
    sources.has("server"),
    "no response was recorded as the container's, so either nothing was " +
      "admitted or admissions are no longer being classified",
  ).toBe(true);
  expect(
    sources.has("capture"),
    "no response was recorded as a replay, so the capture path is not being " +
      "exercised and every rendering is costing an analysis",
  ).toBe(true);

  // Every billed request has exactly one response. The guard asserts this
  // too; asserting it here names the engine while its trace still exists.
  const requests = records.filter((record) => record.event === "request");
  expect(
    responses.length,
    "a billed request went unanswered, or was answered twice",
  ).toBe(requests.length);
});

test.describe("the shared session", () => {
  // Serial, because the second test is an assertion about what the first
  // one did, and running them in either order independently proves nothing.
  test.describe.configure({ mode: "serial" });

  test("a test cannot close the session it borrowed", async ({ profiled: page }) => {
    await expect(page.close()).rejects.toThrow(/shared by every test/);
    expect(page.isClosed(), "the shared page was closed anyway").toBe(false);

    // And by the other route, which reads just as innocently.
    await expect(page.context().close()).rejects.toThrow(/shared by every test/);
    expect(page.isClosed(), "the shared context was closed anyway").toBe(false);
  });

  test("and the next test still has a working session", async ({ profiled: page }) => {
    // This is the test that would have failed, in the old arrangement,
    // for something the previous one did.
    await freshComposer(page);
    await expect(page.getByTestId("composer")).toBeVisible();
    await expect(page.getByLabel("Business question")).toBeEnabled();
  });
});
