/**
 * The gate's own tests.
 *
 * Driven with synthetic reports rather than real runs, because the cases
 * that matter are the ones a real run will not produce on demand: no
 * tests discovered, a requested engine missing, an undeclared engine
 * present. Those are precisely the shapes the old guard approved.
 *
 * `.mjs` beside the module it tests: CI runs the gate with bare `node`
 * after the frontend build, so the gate cannot be TypeScript, and a test
 * under `src/` importing it would reach outside `src/` -- which the
 * build-context gate forbids, because the image's build context has no
 * `scripts/`.
 */

import { describe, expect, it } from "vitest";

import { GateError, KNOWN_BROWSERS, parseBrowsers, summarise } from "./e2eGate.mjs";

/** A report with `counts` tests per project, `skips` of them skipped. */
function report(counts) {
  const specs = [];
  for (const [project, { total, skipped = 0, failed = 0 }] of Object.entries(counts)) {
    for (let i = 0; i < total; i += 1) {
      const status = i < skipped ? "skipped" : i < skipped + failed ? "unexpected" : "expected";
      specs.push({
        title: `${project} spec ${i}`,
        tests: [{ projectName: project, status }],
      });
    }
  }
  return {
    config: {},
    suites: [{ title: "file.spec.ts", specs }],
    errors: [],
    stats: { expected: 0, skipped: 0, unexpected: 0, flaky: 0 },
  };
}

describe("the browser selection", () => {
  it("defaults to every known engine when unset", () => {
    expect(parseBrowsers(undefined)).toEqual(KNOWN_BROWSERS);
  });

  it("rejects an invalid value before Playwright could start", () => {
    // 4. The whitespace-corrupted case that ran zero tests and passed.
    expect(() => parseBrowsers("chromium 0")).toThrow(GateError);
    expect(() => parseBrowsers("chromium 0")).toThrow(/contains whitespace/);
    expect(() => parseBrowsers("safari")).toThrow(/unknown engine/);
    expect(() => parseBrowsers("")).toThrow(/set but empty/);
    expect(() => parseBrowsers("chromium,")).toThrow(/empty entry/);
    expect(() => parseBrowsers(5)).toThrow(/must be a string/);
  });

  it("rejects a duplicate selection", () => {
    // 5. Two copies of a project double the declared skip allowance.
    expect(() => parseBrowsers("chromium,chromium")).toThrow(/repeats/);
    expect(() => parseBrowsers("webkit,firefox,webkit")).toThrow(/repeats/);
  });

  it("accepts a trimmed subset in any order", () => {
    expect(parseBrowsers(" webkit , chromium ")).toEqual(["webkit", "chromium"]);
  });
});

describe("the report guard", () => {
  it("fails when no tests were discovered, even with zero skips declared", () => {
    // 1. The exact shape the old guard approved: nothing ran, no skips, so
    // "0 skipped, as declared" and exit 0.
    const result = summarise(report({}), { requested: ["chromium"], allowedSkips: 0 });
    expect(result.ok).toBe(false);
    expect(result.errors.join(" ")).toMatch(/no tests were discovered/);
  });

  it("fails when a requested engine is absent from the report", () => {
    // 2. Asserted on the report's own project identity, not on the request.
    const result = summarise(report({ chromium: { total: 5 } }), {
      requested: ["firefox"],
      allowedSkips: 0,
    });
    expect(result.ok).toBe(false);
    expect(result.errors.join(" ")).toMatch(/firefox was requested but does not appear/);
  });

  it("fails when a selected engine executed nothing", () => {
    // 3. Discovered but entirely skipped: a pass by summary line only.
    const result = summarise(report({ chromium: { total: 4, skipped: 4 } }), {
      requested: ["chromium"],
      allowedSkips: 4,
    });
    expect(result.ok).toBe(false);
    expect(result.errors.join(" ")).toMatch(/no tests were executed/);
    expect(result.errors.join(" ")).toMatch(/chromium executed no tests/);
  });

  it("fails when an undeclared engine appears", () => {
    const result = summarise(
      report({ chromium: { total: 3 }, webkit: { total: 3 } }),
      { requested: ["chromium"], allowedSkips: 0 },
    );
    expect(result.ok).toBe(false);
    expect(result.errors.join(" ")).toMatch(/webkit appears in the report but was not requested/);
  });

  it("passes a real run with zero declared skips", () => {
    // 6.
    const result = summarise(report({ chromium: { total: 96 } }), {
      requested: ["chromium"],
      allowedSkips: 0,
    });
    expect(result.errors).toEqual([]);
    expect(result.ok).toBe(true);
    expect(result.lines).toEqual(["Chromium: 96 executed, 96 passed, 0 skipped"]);
  });

  it("passes a real run with exactly one declared skip", () => {
    // 7. Firefox and WebKit legitimately skip the Chromium-only page.pdf()
    // test, and that one is gated in the spec.
    const firefox = summarise(report({ firefox: { total: 96, skipped: 1 } }), {
      requested: ["firefox"],
      allowedSkips: 1,
    });
    expect(firefox.ok).toBe(true);
    expect(firefox.lines).toEqual([
      "Firefox: 96 discovered, 95 passed, 1 declared skip",
    ]);

    const webkit = summarise(report({ webkit: { total: 96, skipped: 1 } }), {
      requested: ["webkit"],
      allowedSkips: 1,
    });
    expect(webkit.ok).toBe(true);
    expect(webkit.lines).toEqual([
      "WebKit: 96 discovered, 95 passed, 1 declared skip",
    ]);
  });

  it("fails on fewer skips than declared", () => {
    // 8. Fewer means the gating changed and the declaration is now wrong.
    const result = summarise(report({ firefox: { total: 96 } }), {
      requested: ["firefox"],
      allowedSkips: 1,
    });
    expect(result.ok).toBe(false);
    expect(result.errors.join(" ")).toMatch(/0 test\(s\) skipped; 1 declared\. fewer/);
  });

  it("fails on more skips than declared", () => {
    // 9.
    const result = summarise(report({ firefox: { total: 96, skipped: 9 } }), {
      requested: ["firefox"],
      allowedSkips: 1,
    });
    expect(result.ok).toBe(false);
    expect(result.errors.join(" ")).toMatch(/9 test\(s\) skipped; 1 declared\. more/);
  });

  it("fails on a malformed report", () => {
    // 10. Every missing-structure case refuses rather than assuming zero.
    for (const malformed of [null, undefined, 7, "a string", [], {}, { suites: {} }]) {
      expect(summarise(malformed, { requested: ["chromium"], allowedSkips: 0 }).ok).toBe(false);
    }
    expect(summarise({ suites: [] }, { requested: ["chromium"], allowedSkips: 0 }).errors.join(" "))
      .toMatch(/no `stats` object/);
    // A test the report cannot attribute to an engine is not countable.
    const unattributed = {
      suites: [{ specs: [{ title: "s", tests: [{ status: "expected" }] }] }],
      stats: {},
    };
    expect(summarise(unattributed, { requested: ["chromium"], allowedSkips: 0 }).errors.join(" "))
      .toMatch(/no projectName/);
  });

  it("rejects a non-integer declared allowance", () => {
    const result = summarise(report({ chromium: { total: 5 } }), {
      requested: ["chromium"],
      allowedSkips: Number.NaN,
    });
    expect(result.ok).toBe(false);
    expect(result.errors.join(" ")).toMatch(/non-negative integer/);
  });

  it("still fails a run with failures, and says so per engine", () => {
    // Playwright's own exit status already fails such a run; the gate must
    // not quietly approve it on the way past.
    const result = summarise(report({ chromium: { total: 10, failed: 2 } }), {
      requested: ["chromium"],
      allowedSkips: 0,
    });
    expect(result.ok).toBe(true); // skips and execution are what this gate owns
    expect(result.lines[0]).toBe("Chromium: 10 executed, 8 passed, 0 skipped");
  });

  it("reads several engines in one report", () => {
    const result = summarise(
      report({
        chromium: { total: 96 },
        firefox: { total: 96, skipped: 1 },
        webkit: { total: 96, skipped: 1 },
      }),
      { requested: ["chromium", "firefox", "webkit"], allowedSkips: 2 },
    );
    expect(result.ok).toBe(true);
    expect(result.lines).toEqual([
      "Chromium: 96 executed, 96 passed, 0 skipped",
      "Firefox: 96 discovered, 95 passed, 1 declared skip",
      "WebKit: 96 discovered, 95 passed, 1 declared skip",
    ]);
  });
});
