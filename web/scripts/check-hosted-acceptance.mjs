#!/usr/bin/env node
/**
 * Reconcile a hosted acceptance run against the matrix it claimed to cover.
 *
 * A green Playwright exit says "nothing that ran failed". It does not say
 * anything ran. A config typo, a filtered invocation or a renamed project
 * all produce zero tests and exit 0, and a sweep that executed nothing is
 * the most confident wrong answer a visual acceptance can give.
 *
 * So the pass condition is stated positively and checked here:
 *
 *   every width × theme × state cell reported a result
 *   every one of them passed
 *   nothing was skipped, and nothing was flaky
 *   every cell attached a screenshot
 *
 * Writes `hosted-results/acceptance.json`: the machine-readable summary,
 * which is what gets filed as the evidence.
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

const WIDTHS = [360, 390, 768, 1024, 1440, 1920];
const THEMES = ["light", "dark"];
const STATES = [
  "landing",
  "composer",
  "layout",
  "focus-visible",
  "report",
  "execution-graph",
  "evidence-drawer",
  "focus-restoration",
  "compare",
  "terminal-refused",
  "reduced-motion",
  "print",
];

const PROJECTS = WIDTHS.flatMap((width) =>
  THEMES.map((theme) => `w${width}-${theme}`),
);

const reportPath = process.argv[2] ?? "hosted-results/playwright.json";
const outPath = process.argv[3] ?? "hosted-results/acceptance.json";

/** Flatten Playwright's nested suite tree into (project, title, result) rows. */
function* rows(suite) {
  for (const spec of suite.specs ?? []) {
    for (const test of spec.tests ?? []) {
      const last = (test.results ?? []).at(-1);
      yield {
        project: test.projectName ?? test.projectId ?? "",
        title: spec.title ?? "",
        status: test.status ?? last?.status ?? "unknown",
        outcome: last?.status ?? "unknown",
        attempts: (test.results ?? []).length,
        attachments: (last?.attachments ?? []).length,
        error: last?.error?.message ?? null,
        gaps: (test.annotations ?? [])
          .concat(last?.annotations ?? [])
          .filter((note) => note.type === "known-gap")
          .map((note) => note.description ?? ""),
      };
    }
  }
  for (const child of suite.suites ?? []) yield* rows(child);
}

const report = JSON.parse(readFileSync(reportPath, "utf8"));
const observed = [...(report.suites ?? []).flatMap((suite) => [...rows(suite)])];

/** The state slug is the part of the title before the first colon. */
const stateOf = (title) => title.split(":")[0].trim();

const problems = [];

if (observed.length === 0) {
  problems.push(
    `${reportPath} reports no tests at all. A sweep that executed nothing ` +
      "cannot be evidence that anything renders.",
  );
}

const seen = new Map();
for (const row of observed) {
  const state = stateOf(row.title);
  seen.set(`${row.project}\u0000${state}`, row);
  if (!STATES.includes(state)) {
    problems.push(
      `"${row.title}" (${row.project}) does not begin with a state from the ` +
        `matrix. Known states: ${STATES.join(", ")}.`,
    );
  }
  if (row.status !== "expected") {
    problems.push(
      `${row.project} / ${state}: ${row.outcome}` +
        (row.error ? ` — ${row.error.split("\n")[0]}` : ""),
    );
  }
  if (row.attempts > 1) {
    problems.push(
      `${row.project} / ${state}: ${row.attempts} attempts. Retries are off; ` +
        "a result that needed a second attempt is not a result.",
    );
  }
  if (row.attachments === 0) {
    problems.push(`${row.project} / ${state}: no screenshot was attached.`);
  }
}

for (const project of PROJECTS) {
  for (const state of STATES) {
    if (!seen.has(`${project}\u0000${state}`)) {
      problems.push(`${project} / ${state}: absent from the run.`);
    }
  }
}

/*
 * Known gaps, collected rather than left in the Playwright log.
 *
 * A cell may pass while naming something the build does not do well --
 * a defect the sweep bounds rather than fixes. Burying that in a trace
 * makes a green run read as a clean one, so it is lifted into the
 * summary, deduplicated by what it says rather than by which cell said
 * it.
 */
const gaps = [
  ...new Set(
    observed.flatMap((row) =>
      row.gaps.map((gap) => gap.replace(/ at w\d+-(light|dark)/, "")),
    ),
  ),
].sort();

const expected = PROJECTS.length * STATES.length;
const summary = {
  report: reportPath,
  widths: WIDTHS,
  themes: THEMES,
  states: STATES,
  cells_expected: expected,
  cells_reported: seen.size,
  cells_passed: observed.filter((row) => row.status === "expected").length,
  screenshots: observed.reduce((sum, row) => sum + row.attachments, 0),
  spend: {
    uploads: 0,
    analyses: 0,
    comparisons: 0,
    provider_calls: 0,
    note:
      "Enforced, not assumed: hosted/fixtures.ts routes every /api/** request, " +
      "allows only GET of /api/health, /api/config and /api/recordings, and " +
      "aborts everything else while failing the test that tried it.",
  },
  known_gaps: gaps,
  problems,
  ok: problems.length === 0,
};

mkdirSync(dirname(outPath), { recursive: true });
writeFileSync(outPath, `${JSON.stringify(summary, null, 2)}\n`);

console.log(
  `hosted acceptance: ${summary.cells_passed}/${expected} cells passed, ` +
    `${summary.screenshots} screenshots, ${PROJECTS.length} viewports × ${STATES.length} states`,
);
if (problems.length > 0) {
  console.error(`\n${problems.length} problem(s):`);
  for (const problem of problems.slice(0, 40)) console.error(`  - ${problem}`);
  if (problems.length > 40) {
    console.error(`  … and ${problems.length - 40} more`);
  }
  process.exit(1);
}
console.log(`Wrote ${outPath}`);
