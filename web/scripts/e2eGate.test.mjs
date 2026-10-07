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
 * under `src/` importing it would reach outside `src/`, which the
 * build-context gate forbids, because the image's build context has no
 * `scripts/`.
 */

import { describe, expect, it } from "vitest";

import { GateError, KNOWN_BROWSERS, parseBrowsers, summarise } from "./e2eGate.mjs";

/**
 * A synthetic report.
 *
 * `attempts` is the list of per-retry statuses Playwright records, so a
 * flaky test is written the way the reporter writes one: a failed attempt
 * followed by a passing one, with `status: "flaky"` on the test itself.
 */
function testNode(project, status, attempts, expectedStatus = "passed") {
  return {
    projectName: project,
    status,
    expectedStatus,
    results: attempts.map((s) => ({ status: s })),
  };
}

function report(projects) {
  const specs = [];
  for (const [project, nodes] of Object.entries(projects)) {
    nodes.forEach((node, i) => {
      specs.push({
        title: `spec ${i}`,
        file: "e2e/x.spec.ts",
        line: 10 + i,
        tests: [node(project)],
      });
    });
  }
  return {
    config: {},
    suites: [{ title: "x.spec.ts", file: "e2e/x.spec.ts", specs }],
    errors: [],
    stats: { expected: 0, skipped: 0, unexpected: 0, flaky: 0 },
  };
}

/** Shorthands for the node kinds the guard must tell apart. */
const firstPass = (p) => testNode(p, "expected", ["passed"]);
const declaredSkip = (p) => testNode(p, "skipped", ["skipped"]);
const flakyAfterFail = (p) => testNode(p, "flaky", ["failed", "passed"]);
const flakyAfterTimeout = (p) => testNode(p, "flaky", ["timedOut", "passed"]);
const hardFail = (p) => testNode(p, "unexpected", ["failed", "failed"]);
const timedOut = (p) => testNode(p, "unexpected", ["timedOut"]);
const interrupted = (p) => testNode(p, "unexpected", ["interrupted"]);
const expectedFailure = (p) => testNode(p, "expected", ["failed"], "failed");
const weirdStatus = (p) => testNode(p, "sideways", ["passed"]);

const many = (make, n) => Array.from({ length: n }, () => make);

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
  it("counts a first-attempt pass as passed", () => {
    // 1.
    const result = summarise(report({ chromium: many(firstPass, 96) }), {
      requested: ["chromium"],
      allowedSkips: 0,
    });
    expect(result.errors).toEqual([]);
    expect(result.ok).toBe(true);
    expect(result.lines).toEqual([
      "Chromium: 96 discovered",
      "  96 passed first attempt",
      "Chromium: all discovered tests reconciled, 0 flaky",
    ]);
  });

  it("counts a passed-on-retry as flaky, not as passed", () => {
    // 2 and 3. This is the case that hid inside "94 passed" of "96
    // discovered, 1 skipped": the categories did not reconcile and the
    // guard approved it anyway.
    const result = summarise(
      report({ firefox: [...many(firstPass, 94), declaredSkip, flakyAfterFail] }),
      { requested: ["firefox"], allowedSkips: 1 },
    );
    expect(result.ok).toBe(false);
    expect(result.lines).toEqual([
      "Firefox: 96 discovered",
      "  94 passed first attempt",
      "   1 declared skip",
      "   1 flaky",
    ]);
    expect(result.errors.join(" ")).toMatch(/flaky tests are not accepted/);
    // Named, so the next reader knows which test to investigate.
    expect(result.errors.join(" ")).toMatch(/e2e\/x\.spec\.ts:105/);
  });

  it("does not inflate the discovered count with retry attempts", () => {
    // 4. Two attempts, one test.
    const result = summarise(report({ chromium: [flakyAfterFail] }), {
      requested: ["chromium"],
      allowedSkips: 0,
    });
    expect(result.lines[0]).toBe("Chromium: 1 discovered");
  });

  it("names a failure followed by a passing retry", () => {
    // 5.
    const result = summarise(report({ chromium: [...many(firstPass, 3), flakyAfterFail] }), {
      requested: ["chromium"],
      allowedSkips: 0,
    });
    expect(result.ok).toBe(false);
    expect(result.errors.join(" ")).toMatch(/flaky tests are not accepted \(1\)/);
  });

  it("names a timeout followed by a passing retry", () => {
    // 6. Exactly the Firefox shape: page.goto timed out, the retry passed.
    const result = summarise(
      report({ firefox: [...many(firstPass, 3), flakyAfterTimeout] }),
      { requested: ["firefox"], allowedSkips: 0 },
    );
    expect(result.ok).toBe(false);
    expect(result.errors.join(" ")).toMatch(/flaky tests are not accepted/);
  });

  it("fails on an unrecognised status", () => {
    // 7.
    const result = summarise(report({ chromium: [...many(firstPass, 2), weirdStatus] }), {
      requested: ["chromium"],
      allowedSkips: 0,
    });
    expect(result.ok).toBe(false);
    expect(result.errors.join(" ")).toMatch(/unrecognised status/);
  });

  it("fails when a test cannot be attributed to an engine", () => {
    // 8.
    const unattributed = {
      suites: [{ specs: [{ title: "s", tests: [{ status: "expected", results: [] }] }] }],
      stats: {},
    };
    expect(
      summarise(unattributed, { requested: ["chromium"], allowedSkips: 0 }).errors.join(" "),
    ).toMatch(/no projectName/);
  });

  it("fails when the category totals do not reconcile", () => {
    // 9. A status the classifier cannot place lands in `unknown`, which is
    // what makes the shortfall visible rather than silent.
    const result = summarise(report({ chromium: [weirdStatus] }), {
      requested: ["chromium"],
      allowedSkips: 0,
    });
    expect(result.ok).toBe(false);
    const joined = result.errors.join(" ");
    expect(joined).toMatch(/unrecognised status/);
    // And the breakdown shows it rather than omitting it.
    expect(result.lines.join("\n")).toMatch(/1 unknown status/);
  });

  it("passes a fully reconciled run with no flakes", () => {
    // 10.
    const result = summarise(
      report({
        chromium: many(firstPass, 96),
        firefox: [...many(firstPass, 95), declaredSkip],
        webkit: [...many(firstPass, 95), declaredSkip],
      }),
      { requested: ["chromium", "firefox", "webkit"], allowedSkips: 2 },
    );
    expect(result.errors).toEqual([]);
    expect(result.ok).toBe(true);
    expect(result.lines.filter((l) => l.includes("reconciled"))).toEqual([
      "Chromium: all discovered tests reconciled, 0 flaky",
      "Firefox: all discovered tests reconciled, 0 flaky",
      "WebKit: all discovered tests reconciled, 0 flaky",
    ]);
  });

  it("still holds the declared skip count exactly", () => {
    // 11, and both directions of 8/9 from the previous revision.
    const exact = summarise(report({ firefox: [...many(firstPass, 95), declaredSkip] }), {
      requested: ["firefox"],
      allowedSkips: 1,
    });
    expect(exact.ok).toBe(true);

    const tooFew = summarise(report({ firefox: many(firstPass, 96) }), {
      requested: ["firefox"],
      allowedSkips: 1,
    });
    expect(tooFew.ok).toBe(false);
    expect(tooFew.errors.join(" ")).toMatch(/0 test\(s\) skipped; 1 declared\. fewer/);

    const tooMany = summarise(
      report({ firefox: [...many(firstPass, 87), ...many(declaredSkip, 9)] }),
      { requested: ["firefox"], allowedSkips: 1 },
    );
    expect(tooMany.ok).toBe(false);
    expect(tooMany.errors.join(" ")).toMatch(/9 test\(s\) skipped; 1 declared\. more/);
  });

  it("still fails a zero-test report", () => {
    // 12. The original defect: nothing ran, nothing skipped, exit 0.
    const result = summarise(report({}), { requested: ["chromium"], allowedSkips: 0 });
    expect(result.ok).toBe(false);
    expect(result.errors.join(" ")).toMatch(/no tests were discovered/);
  });

  it("still fails when a requested engine is absent or executed nothing", () => {
    const absent = summarise(report({ chromium: many(firstPass, 5) }), {
      requested: ["firefox"],
      allowedSkips: 0,
    });
    expect(absent.errors.join(" ")).toMatch(/firefox was requested but does not appear/);

    const allSkipped = summarise(report({ chromium: many(declaredSkip, 4) }), {
      requested: ["chromium"],
      allowedSkips: 4,
    });
    expect(allSkipped.errors.join(" ")).toMatch(/no tests were executed/);
    expect(allSkipped.errors.join(" ")).toMatch(/chromium executed no tests/);
  });

  it("still fails when an undeclared engine appears", () => {
    const result = summarise(
      report({ chromium: many(firstPass, 3), webkit: many(firstPass, 3) }),
      { requested: ["chromium"], allowedSkips: 0 },
    );
    expect(result.errors.join(" ")).toMatch(/webkit appears in the report but was not requested/);
  });

  it("names hard failures, timeouts and interruptions separately", () => {
    // A failure already fails Playwright; the guard must not pass it by,
    // and the three are distinguished because they point at different
    // causes.
    for (const [make, pattern] of [
      [hardFail, /1 test\(s\) failed/],
      [timedOut, /1 test\(s\) timed out/],
      [interrupted, /1 test\(s\) interrupted/],
    ]) {
      const result = summarise(report({ chromium: [...many(firstPass, 2), make] }), {
        requested: ["chromium"],
        allowedSkips: 0,
      });
      expect(result.ok).toBe(false);
      expect(result.errors.join(" ")).toMatch(pattern);
    }
  });

  it("accounts for a declared expected failure without calling it a pass", () => {
    const result = summarise(report({ chromium: [...many(firstPass, 2), expectedFailure] }), {
      requested: ["chromium"],
      allowedSkips: 0,
    });
    expect(result.ok).toBe(true);
    expect(result.lines.join("\n")).toMatch(/1 expected failure/);
    expect(result.lines.join("\n")).toMatch(/2 passed first attempt/);
  });

  it("fails on a malformed report", () => {
    for (const malformed of [null, undefined, 7, "a string", [], {}, { suites: {} }]) {
      expect(summarise(malformed, { requested: ["chromium"], allowedSkips: 0 }).ok).toBe(false);
    }
    expect(
      summarise({ suites: [] }, { requested: ["chromium"], allowedSkips: 0 }).errors.join(" "),
    ).toMatch(/no `stats` object/);
  });

  it("rejects a non-integer declared allowance", () => {
    const result = summarise(report({ chromium: many(firstPass, 5) }), {
      requested: ["chromium"],
      allowedSkips: Number.NaN,
    });
    expect(result.ok).toBe(false);
    expect(result.errors.join(" ")).toMatch(/non-negative integer/);
  });
});
