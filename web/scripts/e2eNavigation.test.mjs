/**
 * No spec navigates on its own.
 *
 * `page.goto("/")` defaults to `waitUntil: "load"`, and on Firefox that
 * intermittently never resolves: twice in consecutive CI runs a test spent
 * its whole 120s budget inside `page.goto`, on two different tests, while
 * the trace showed the document, script, stylesheet and `/api/config` all
 * returned 200 and the application rendered. 63 call sites were each one
 * stalled request away from that.
 *
 * They all go through `openApp`, which navigates in-page and waits for the
 * app shell rather than browser navigation bookkeeping. This keeps it that
 * way: a new spec written with the obvious `page.goto("/")` fails here
 * rather than in CI a week later.
 */

import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { describe, expect, it } from "vitest";

// Resolved from the vitest root (`web/`) rather than from
// `import.meta.url`, which the transform rewrites to something that does
// not resolve as a file URL here. Asserted rather than assumed: a path
// that silently pointed nowhere would make the scan below vacuous.
const E2E = resolve(process.cwd(), "e2e");

function specFiles() {
  if (!existsSync(E2E)) throw new Error(`no e2e directory at ${E2E}`);
  return readdirSync(E2E).filter((name) => name.endsWith(".spec.ts"));
}

const helperSource = () => readFileSync(join(E2E, "helpers.ts"), "utf8");

describe("end-to-end navigation", () => {
  it("has no spec navigating to the app directly", () => {
    // Any receiver, not just a variable literally called `page`. The first
    // version of this guard matched `page.goto(` case-sensitively and so
    // missed `alicePage.goto("/")` and `bobPage.goto("/")` in the
    // two-visitor isolation test, which the sweep had also missed, for
    // the same reason.
    const offenders = [];
    for (const name of specFiles()) {
      readFileSync(join(E2E, name), "utf8")
        .split("\n")
        .forEach((line, i) => {
          if (/\.\s*goto\s*\(/.test(line)) {
            offenders.push(`${name}:${i + 1} ${line.trim()}`);
          }
        });
    }
    expect(
      offenders,
      "navigate with `openApp(page)` from ./helpers instead. A bare `goto` " +
        "waits for `load`, which hangs intermittently on Firefox:\n  " +
        offenders.join("\n  "),
    ).toEqual([]);
  });

  it("finds specs to scan, and specs that use the helper", () => {
    // Guards the guard: a scan that found no files, or files that never
    // navigate, would make the test above pass by looking at nothing.
    const names = specFiles();
    expect(names.length).toBeGreaterThan(5);

    const users = names.filter((name) =>
      /\bopenApp\s*\(/.test(readFileSync(join(E2E, name), "utf8")),
    );
    expect(users.length).toBeGreaterThan(5);
  });

  it("navigates in-page, without making a browser lifecycle event the readiness gate", () => {
    const openApp = helperSource().split("export async function openApp")[1] ?? "";
    expect(openApp).toMatch(/window\.location\.assign\(url\)/);
    expect(openApp).toMatch(/new URL\(['"]\/['"], baseUrl\)\.href/);
    expect(openApp).not.toMatch(/\.goto\(/);
    // `load`, `domcontentloaded`, `commit`, and `networkidle` were all
    // lifecycle waits the app never needed to prove readiness.
    expect(openApp).not.toMatch(/waitUntil:/);
    expect(openApp).not.toMatch(/networkidle/);
  });

  it("bounds the navigation so a dead server fails rather than hangs", () => {
    const openApp = helperSource().split("export async function openApp")[1] ?? "";
    const timeout = /timeout:\s*([0-9_]+)/.exec(openApp);
    expect(timeout, "openApp has no bounded navigation timeout").not.toBeNull();
    const ms = Number((timeout?.[1] ?? "0").replace(/_/g, ""));
    expect(ms).toBeGreaterThan(0);
    expect(ms).toBeLessThanOrEqual(30_000);
  });

  it("waits for the application-owned readiness marker", () => {
    const openApp = helperSource().split("export async function openApp")[1] ?? "";
    expect(openApp).toMatch(/getByTestId\(['"]app-shell['"]\)/);
    expect(openApp).toMatch(/toBeVisible\(\{\s*timeout:\s*20_000\s*\}\)/);

    // The marker must come from React, not from the served HTML, because a
    // marker present before mount would make the wait meaningless.
    const html = readFileSync(resolve(process.cwd(), "index.html"), "utf8");
    expect(html).not.toMatch(/app-shell/);
    const shell = readFileSync(
      resolve(process.cwd(), "src/components/AppShell.tsx"),
      "utf8",
    );
    expect(shell).toMatch(/data-testid="app-shell"/);
  });
});
