/**
 * The gate that decides whether a browser run proved anything.
 *
 * It replaces a guard that counted skips alone. That guard was vacuous in
 * the one case most likely to happen by accident: a shell bug set
 * `AAE_E2E_BROWSERS` to the literal string "chromium 0", the project
 * filter matched nothing, **zero tests executed**, and the guard printed
 * "0 skipped, as declared" and exited 0. Playwright's own non-zero exit
 * was the only thing that gave it away, and an exit status is easy to
 * swallow in a pipeline. A gate that passes when nothing ran is worse than
 * no gate, because it is trusted.
 *
 * So there are two halves, and both must hold:
 *
 *   1. The selection is validated *before* Playwright starts, against a
 *      closed set. An unparseable selection is an error, never silently
 *      zero projects.
 *   2. The report is read for what actually executed, per project, using
 *      the report's own project identity rather than the environment
 *      variable that asked for it. The variable records a request; the
 *      report records what happened.
 *
 * Written as `.mjs` because CI runs it with bare `node`, after the
 * frontend build, with no transpile step available. `playwright.config.ts`
 * imports the same module, so the selection is parsed once in one place.
 */

/** The only engines this suite can drive. A closed set on purpose. */
export const KNOWN_BROWSERS = ["chromium", "firefox", "webkit"];

/** How a reader sees each one, for the gate's own output. */
const DISPLAY = { chromium: "Chromium", firefox: "Firefox", webkit: "WebKit" };

export class GateError extends Error {
  constructor(message) {
    super(message);
    this.name = "GateError";
  }
}

/**
 * The browsers a run is asking for.
 *
 * `undefined` means "every known engine", which is the config's default.
 * Everything else must parse to a non-empty, duplicate-free subset of the
 * closed set. The separator is a comma, as the workflow and the config
 * have always used.
 */
export function parseBrowsers(raw) {
  if (raw === undefined || raw === null) return [...KNOWN_BROWSERS];

  if (typeof raw !== "string") {
    throw new GateError(`AAE_E2E_BROWSERS must be a string, got ${typeof raw}.`);
  }
  if (raw.trim() === "") {
    throw new GateError(
      "AAE_E2E_BROWSERS is set but empty. Unset it to run every engine, or " +
        `name them: ${KNOWN_BROWSERS.join(",")}.`,
    );
  }

  const parts = raw.split(",").map((part) => part.trim());

  const blank = parts.filter((part) => part === "");
  if (blank.length > 0) {
    throw new GateError(
      `AAE_E2E_BROWSERS has an empty entry: "${raw}". Separate names with a ` +
        "single comma and nothing else.",
    );
  }

  // The failure that prompted all of this: a value carrying a second word.
  // Left to the project filter it becomes an unmatched name and therefore
  // zero projects, which is indistinguishable from a passing run.
  const corrupted = parts.filter((part) => /\s/.test(part));
  if (corrupted.length > 0) {
    throw new GateError(
      `AAE_E2E_BROWSERS entry contains whitespace: ${corrupted
        .map((part) => `"${part}"`)
        .join(", ")}. This is usually a shell quoting bug -- zsh does not ` +
        "word-split an unquoted expansion, so a pair like \"chromium 0\" " +
        "arrives as one value and silently matches no project.",
    );
  }

  const unknown = parts.filter((part) => !KNOWN_BROWSERS.includes(part));
  if (unknown.length > 0) {
    throw new GateError(
      `AAE_E2E_BROWSERS names unknown engine(s): ${unknown
        .map((part) => `"${part}"`)
        .join(", ")}. Known: ${KNOWN_BROWSERS.join(", ")}.`,
    );
  }

  const seen = new Set();
  const duplicates = parts.filter((part) => seen.size === seen.add(part).size);
  if (duplicates.length > 0) {
    throw new GateError(
      `AAE_E2E_BROWSERS repeats ${[...new Set(duplicates)]
        .map((part) => `"${part}"`)
        .join(", ")}. A repeated project would run twice and double the ` +
        "declared skip allowance.",
    );
  }

  return parts;
}

/**
 * The terminal buckets a test can land in.
 *
 * Playwright records one `test` per project-and-title and one entry in its
 * `results` array per attempt. Retries therefore multiply attempts, never
 * discovered tests, and this counts tests: an accounting that summed
 * `results` would inflate the total every time CI retried something.
 *
 * The invariant, asserted rather than assumed:
 *
 *   discovered = passed_first_attempt + skipped + expected_failure
 *              + failed + timed_out + interrupted + flaky + unknown
 *
 * and `unknown` must be zero. A test that cannot be placed in exactly one
 * bucket is a hole in the accounting, so the gate fails and names it --
 * the previous version printed a passing summary whose categories did not
 * reconcile, which is how a flaky Firefox test hid inside "94 passed" of
 * "96 discovered, 1 skipped".
 */
export const BUCKETS = [
  "passed_first_attempt",
  "skipped",
  "expected_failure",
  "failed",
  "timed_out",
  "interrupted",
  "flaky",
  "unknown",
];

function emptyRow() {
  const row = { discovered: 0, attempts: 0, names: { flaky: [], failed: [], timed_out: [], interrupted: [], unknown: [] } };
  for (const bucket of BUCKETS) row[bucket] = 0;
  return row;
}

/** Which single bucket this test belongs in. */
function classify(test) {
  const attempts = Array.isArray(test.results) ? test.results : [];
  const last = attempts.length > 0 ? attempts[attempts.length - 1] : undefined;

  switch (test.status) {
    case "skipped":
      return "skipped";
    case "flaky":
      // Passed, but only after a retry. Nondeterminism, not proof.
      return "flaky";
    case "expected":
      // `test.fail()` marks a test whose expected outcome is a failure.
      return test.expectedStatus === "failed" ? "expected_failure" : "passed_first_attempt";
    case "unexpected":
      if (last?.status === "timedOut") return "timed_out";
      if (last?.status === "interrupted") return "interrupted";
      return "failed";
    default:
      return "unknown";
  }
}

/** Per-project tallies, walking the report the way Playwright nests it. */
function tally(suites, into = new Map(), trail = [], depth = 0) {
  if (depth > 50) throw new GateError("the report nests implausibly deeply");
  if (!Array.isArray(suites)) return into;

  for (const suite of suites) {
    if (!suite || typeof suite !== "object") continue;
    const here = suite.title ? [...trail, suite.title] : trail;

    for (const spec of suite.specs ?? []) {
      for (const test of spec.tests ?? []) {
        const project = typeof test.projectName === "string" ? test.projectName : "";
        if (!project) {
          throw new GateError(
            `a test in "${spec.title ?? "?"}" has no projectName; the report ` +
              "cannot be attributed to an engine",
          );
        }
        const row = into.get(project) ?? emptyRow();
        row.discovered += 1;
        row.attempts += Array.isArray(test.results) ? test.results.length : 0;

        const bucket = classify(test);
        row[bucket] += 1;
        if (row.names[bucket]) {
          const where = spec.file ?? suite.file ?? "?";
          const line = spec.line ? `:${spec.line}` : "";
          row.names[bucket].push(`${where}${line} \u203a ${[...here, spec.title].join(" \u203a ")}`);
        }
        into.set(project, row);
      }
    }
    tally(suite.suites, into, here, depth + 1);
  }
  return into;
}

/**
 * Decide whether a report proves the requested engines ran cleanly.
 *
 * Returns `{ ok, lines, errors }` rather than throwing, so a caller can
 * print every problem at once instead of one per invocation.
 */
export function summarise(report, { requested, allowedSkips }) {
  const errors = [];
  const lines = [];

  if (!report || typeof report !== "object" || Array.isArray(report)) {
    return { ok: false, lines, errors: ["the report is not a JSON object"] };
  }
  if (!Array.isArray(report.suites)) {
    return { ok: false, lines, errors: ["the report has no `suites` array"] };
  }
  if (!report.stats || typeof report.stats !== "object") {
    return { ok: false, lines, errors: ["the report has no `stats` object"] };
  }
  if (!Number.isInteger(allowedSkips) || allowedSkips < 0) {
    return {
      ok: false,
      lines,
      errors: [`AAE_E2E_ALLOWED_SKIPS must be a non-negative integer, got ${allowedSkips}`],
    };
  }

  let rows;
  try {
    rows = tally(report.suites);
  } catch (reason) {
    return { ok: false, lines, errors: [reason.message] };
  }

  const total = (bucket) => [...rows.values()].reduce((sum, row) => sum + row[bucket], 0);
  const discovered = [...rows.values()].reduce((sum, row) => sum + row.discovered, 0);
  const skipped = total("skipped");
  const executed = discovered - skipped;

  if (discovered === 0) {
    errors.push(
      "no tests were discovered. A report with no tests is not a pass: the " +
        "usual cause is a browser selection that matched no project.",
    );
  } else if (executed === 0) {
    errors.push(
      `no tests were executed (${discovered} discovered, all skipped). ` +
        "Skipped reads as a pass in a summary line and proves nothing.",
    );
  }

  for (const name of requested) {
    const row = rows.get(name);
    if (!row) {
      errors.push(
        `${name} was requested but does not appear in the report. Either it ` +
          "never ran or the project is named differently in the config.",
      );
      continue;
    }
    if (row.discovered - row.skipped === 0) {
      errors.push(
        `${name} executed no tests (${row.discovered} discovered, ` +
          `${row.skipped} skipped).`,
      );
    }

    // The accounting invariant. Every discovered test must land in exactly
    // one terminal bucket, or the summary is not a summary of anything.
    const placed = BUCKETS.reduce((sum, bucket) => sum + row[bucket], 0);
    if (placed !== row.discovered) {
      errors.push(
        `${name}: ${row.discovered} discovered but ${placed} placed into ` +
          "terminal buckets; the categories do not reconcile.",
      );
    }
    if (row.unknown > 0) {
      errors.push(
        `${name}: ${row.unknown} test(s) with an unrecognised status:\n      ` +
          row.names.unknown.join("\n      "),
      );
    }
    // Retries add attempts, never discovered tests.
    if (row.attempts < row.discovered - row.skipped) {
      errors.push(
        `${name}: ${row.attempts} attempt(s) recorded for ` +
          `${row.discovered - row.skipped} executed test(s); the report is ` +
          "internally inconsistent.",
      );
    }
  }

  for (const name of rows.keys()) {
    if (!requested.includes(name)) {
      errors.push(
        `${name} appears in the report but was not requested. The declared ` +
          "skip allowance is per engine, so an extra project makes it wrong.",
      );
    }
  }

  if (skipped !== allowedSkips) {
    const direction = skipped > allowedSkips ? "more" : "fewer";
    errors.push(
      `${skipped} test(s) skipped; ${allowedSkips} declared. ${direction} ` +
        "skips than declared means browser coverage changed without the " +
        "declaration changing.",
    );
  }

  // Per-engine breakdown, printed whether the run passed or not: the
  // numbers are the evidence either way.
  for (const name of requested) {
    const row = rows.get(name);
    if (!row) continue;
    const label = DISPLAY[name] ?? name;
    lines.push(`${label}: ${row.discovered} discovered`);
    const shown = [
      ["passed first attempt", row.passed_first_attempt],
      ["declared skip", row.skipped],
      ["expected failure", row.expected_failure],
      ["failed", row.failed],
      ["timed out", row.timed_out],
      ["interrupted", row.interrupted],
      ["flaky", row.flaky],
      ["unknown status", row.unknown],
    ];
    for (const [what, count] of shown) {
      if (count > 0 || what === "passed first attempt") {
        lines.push(`  ${String(count).padStart(2)} ${what}${what === "declared skip" && count !== 1 ? "s" : ""}`);
      }
    }
  }

  // Flaky is refused outright. There is no allowance, because a test that
  // passes only on retry has not demonstrated the thing it asserts, and
  // a retry that rescues CI hides exactly the races a browser suite exists
  // to find.
  for (const name of requested) {
    const row = rows.get(name);
    if (!row || row.flaky === 0) continue;
    errors.push(
      `${name}: flaky tests are not accepted (${row.flaky}):\n      ` +
        row.names.flaky.join("\n      "),
    );
  }
  for (const [bucket, label] of [
    ["failed", "failed"],
    ["timed_out", "timed out"],
    ["interrupted", "interrupted"],
  ]) {
    for (const name of requested) {
      const row = rows.get(name);
      if (!row || row[bucket] === 0) continue;
      errors.push(
        `${name}: ${row[bucket]} test(s) ${label}:\n      ` +
          row.names[bucket].join("\n      "),
      );
    }
  }

  if (errors.length === 0) {
    for (const name of requested) {
      const label = DISPLAY[name] ?? name;
      lines.push(`${label}: all discovered tests reconciled, 0 flaky`);
    }
  }

  return { ok: errors.length === 0, lines, errors };
}
