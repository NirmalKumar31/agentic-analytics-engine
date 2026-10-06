#!/usr/bin/env node
/**
 * The hosted acceptance checker, checked.
 *
 * `check-hosted-acceptance.mjs` is the thing that decides whether a hosted
 * sweep counted. A guard that approves an empty run is worse than no guard,
 * because it converts "nothing was tested" into a green line in a report --
 * and that is not hypothetical here: the browser suite once ran zero tests
 * on a shell quoting bug and its skip guard approved the result.
 *
 * So the checker is driven against synthetic reports whose answer is known,
 * including the three failure modes that matter: an empty run, a missing
 * cell, and a cell that passed only on a second attempt.
 */

import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPTS = fileURLToPath(new URL(".", import.meta.url));
const CHECKER = join(SCRIPTS, "check-hosted-acceptance.mjs");
const MATRIX = join(SCRIPTS, "..", "hosted", "matrix.ts");

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
  "ai-in-progress",
  "terminal-refused",
  "reduced-motion",
  "print",
];

let failures = 0;
let checks = 0;

function check(name, condition, detail = "") {
  checks += 1;
  if (condition) return;
  failures += 1;
  console.error(`FAIL  ${name}${detail ? `\n      ${detail}` : ""}`);
}

/** A Playwright JSON report covering `cells`, in the nesting it really uses. */
function report(cells) {
  return {
    suites: [
      {
        title: "acceptance.spec.ts",
        suites: [
          {
            title: "hosted visual acceptance",
            specs: cells.map(
              ({ project, state, status = "expected", attempts = 1, attachments = 1 }) => ({
                title: `${state}: a sentence about it`,
                tests: [
                  {
                    projectName: project,
                    status,
                    results: Array.from({ length: attempts }, () => ({
                      status: status === "expected" ? "passed" : "failed",
                      attachments: Array.from({ length: attachments }, () => ({
                        name: "shot",
                      })),
                      error: status === "expected" ? undefined : { message: "boom" },
                    })),
                  },
                ],
              }),
            ),
          },
        ],
      },
    ],
  };
}

function complete(mutate = (cells) => cells) {
  const cells = [];
  for (const width of WIDTHS) {
    for (const theme of THEMES) {
      for (const state of STATES) {
        cells.push({ project: `w${width}-${theme}`, state });
      }
    }
  }
  return mutate(cells);
}

/** Run the checker over a report. Returns { code, out }. */
function run(payload) {
  const dir = mkdtempSync(join(tmpdir(), "hosted-guard-"));
  const input = join(dir, "playwright.json");
  const output = join(dir, "acceptance.json");
  writeFileSync(input, JSON.stringify(payload));
  try {
    const out = execFileSync("node", [CHECKER, input, output], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
    return { code: 0, out };
  } catch (error) {
    return {
      code: error.status ?? 1,
      out: `${error.stdout ?? ""}${error.stderr ?? ""}`,
    };
  }
}

/* ------------------------------------------------------------------ */

const full = run(report(complete()));
check("a complete sweep passes", full.code === 0, full.out);
check(
  "and says how many cells it saw",
  full.out.includes(`${WIDTHS.length * THEMES.length * STATES.length}/`),
  full.out,
);

const empty = run({ suites: [] });
check("an empty run is refused", empty.code !== 0, empty.out);
check(
  "and says that nothing executed, rather than that nothing failed",
  /no tests at all/.test(empty.out),
  empty.out,
);

const missingState = run(
  report(complete((cells) => cells.filter((cell) => cell.state !== "print"))),
);
check("a missing state is refused", missingState.code !== 0);
check(
  "and names the state and the viewport",
  /w360-light \/ print: absent/.test(missingState.out),
  missingState.out,
);

const missingViewport = run(
  report(complete((cells) => cells.filter((cell) => cell.project !== "w1920-dark"))),
);
check("a missing viewport is refused", missingViewport.code !== 0);
check(
  "and names every state it lost with it",
  (missingViewport.out.match(/w1920-dark/g) ?? []).length >= STATES.length,
  missingViewport.out,
);

const failed = run(
  report(
    complete((cells) =>
      cells.map((cell, index) =>
        index === 3 ? { ...cell, status: "unexpected" } : cell,
      ),
    ),
  ),
);
check("a failed cell is refused", failed.code !== 0, failed.out);

const retried = run(
  report(
    complete((cells) =>
      cells.map((cell, index) => (index === 5 ? { ...cell, attempts: 2 } : cell)),
    ),
  ),
);
check("a cell that needed two attempts is refused", retried.code !== 0);
check(
  "and says why a retried result is not a result",
  /attempts/.test(retried.out),
  retried.out,
);

const noScreenshot = run(
  report(
    complete((cells) =>
      cells.map((cell, index) => (index === 7 ? { ...cell, attachments: 0 } : cell)),
    ),
  ),
);
check("a cell with no screenshot is refused", noScreenshot.code !== 0);

const renamed = run(
  report(
    complete((cells) =>
      cells.map((cell, index) =>
        index === 9 ? { ...cell, state: "something-else" } : cell,
      ),
    ),
  ),
);
check("a test renamed off the matrix is refused", renamed.code !== 0);
check(
  "and is reported as unknown rather than silently dropped",
  /does not begin with a state from the matrix/.test(renamed.out),
  renamed.out,
);

/* ------------------------------------------------- the matrix, reconciled
 *
 * The matrix is declared twice: `hosted/matrix.ts` builds the projects and
 * this checker decides whether a finished run covered them. The checker is
 * plain Node and cannot import the TypeScript, so the two lists are kept in
 * step here rather than by discipline.
 *
 * The failure this prevents is specific and silent: a state added to the
 * spec but not to the checker is a state the checker stops requiring, so a
 * run that later loses it still passes.
 */
function arrayIn(source, name) {
  const match = new RegExp(`${name} = \\[([^\\]]*)\\]`, "s").exec(source);
  if (!match) return null;
  return match[1]
    .split(",")
    .map((item) => item.trim().replace(/^["']|["']$/g, ""))
    .filter(Boolean);
}

const matrixSource = readFileSync(MATRIX, "utf8");
const checkerSource = readFileSync(CHECKER, "utf8");

for (const [name, mine] of [
  ["WIDTHS", WIDTHS.map(String)],
  ["THEMES", THEMES],
  ["STATES", STATES],
]) {
  const fromMatrix = arrayIn(matrixSource, `export const ${name}`);
  const fromChecker = arrayIn(checkerSource, `const ${name}`);
  check(`${name} is declared in hosted/matrix.ts`, fromMatrix !== null);
  check(`${name} is declared in the checker`, fromChecker !== null);
  check(
    `${name} agrees between hosted/matrix.ts and the checker`,
    JSON.stringify(fromMatrix) === JSON.stringify(fromChecker),
    `matrix: ${JSON.stringify(fromMatrix)}\n      checker: ${JSON.stringify(fromChecker)}`,
  );
  check(
    `${name} agrees with this guard`,
    JSON.stringify(fromChecker) === JSON.stringify(mine),
    `checker: ${JSON.stringify(fromChecker)}\n      guard: ${JSON.stringify(mine)}`,
  );
}

check(
  "the brief's states are all in the matrix",
  [
    "execution-graph",
    "evidence-drawer",
    "focus-restoration",
    "reduced-motion",
    "print",
    "compare",
  ].every((state) => STATES.includes(state)),
  STATES.join(", "),
);

console.log(`hosted acceptance guard: ${checks - failures}/${checks} checks passed`);
if (failures > 0) process.exit(1);
