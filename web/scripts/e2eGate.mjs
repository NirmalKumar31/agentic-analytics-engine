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

/** Per-project tallies, walking the report the way Playwright nests it. */
function tally(suites, into = new Map(), depth = 0) {
  if (depth > 50) throw new GateError("the report nests implausibly deeply");
  if (!Array.isArray(suites)) return into;

  for (const suite of suites) {
    if (!suite || typeof suite !== "object") continue;
    for (const spec of suite.specs ?? []) {
      for (const test of spec.tests ?? []) {
        const project = typeof test.projectName === "string" ? test.projectName : "";
        if (!project) {
          throw new GateError(
            `a test in "${spec.title ?? "?"}" has no projectName; the report ` +
              "cannot be attributed to an engine",
          );
        }
        const row =
          into.get(project) ??
          { discovered: 0, passed: 0, skipped: 0, failed: 0, other: 0 };
        row.discovered += 1;
        if (test.status === "skipped") row.skipped += 1;
        else if (test.status === "expected") row.passed += 1;
        else if (test.status === "unexpected") row.failed += 1;
        else row.other += 1;
        into.set(project, row);
      }
    }
    tally(suite.suites, into, depth + 1);
  }
  return into;
}

/**
 * Decide whether a report proves the requested engines ran.
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

  const discovered = [...rows.values()].reduce((sum, row) => sum + row.discovered, 0);
  const skipped = [...rows.values()].reduce((sum, row) => sum + row.skipped, 0);
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

  for (const name of requested) {
    const row = rows.get(name);
    if (!row) continue;
    const label = DISPLAY[name] ?? name;
    lines.push(
      row.skipped === 0
        ? `${label}: ${row.discovered - row.skipped} executed, ${row.passed} passed, 0 skipped`
        : `${label}: ${row.discovered} discovered, ${row.passed} passed, ` +
          `${row.skipped} declared skip${row.skipped === 1 ? "" : "s"}`,
    );
  }

  return { ok: errors.length === 0, lines, errors };
}
