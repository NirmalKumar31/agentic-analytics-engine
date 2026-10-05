/**
 * The charting runtime stays off the critical path.
 *
 * Vega is 841 kB raw, 94 kB gzipped more than the rest of the application
 * put together, and a reader who never runs an analysis never needs it.
 * `Chart.tsx` loads it with a dynamic `import()` for that reason.
 *
 * Removing the `await` and importing it at the top of the file is a
 * one-line change that nothing else notices: the chart still renders, and
 * every assertion about it still passes. `scripts/check-bundle.mjs` catches
 * it in the built output; this catches it in the source, where the mistake
 * is made, and runs in `npm run test` without needing a build.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const SRC = join(__dirname, "..");

function sources(dir = SRC, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) {
      if (entry !== "test") sources(path, out);
    } else if (/\.tsx?$/.test(entry)) {
      out.push(path);
    }
  }
  return out;
}

describe("Vega is loaded on demand", () => {
  const files = sources();

  it("is imported dynamically, and from exactly one place", () => {
    const dynamic = files.filter((f) =>
      /\bimport\s*\(\s*["']vega-embed["']\s*\)/.test(readFileSync(f, "utf8")),
    );
    expect(dynamic.map((f) => f.replace(SRC + "/", ""))).toEqual([
      "components/Chart.tsx",
    ]);
  });

  it("is never imported statically by anything", () => {
    // A single static import anywhere pulls the whole runtime into the
    // entry chunk, however carefully the dynamic one is written.
    const offenders: string[] = [];
    for (const file of files) {
      const source = readFileSync(file, "utf8");
      if (/^\s*import\s[^(]*["']vega[^"']*["']/m.test(source)) {
        offenders.push(file.replace(SRC + "/", ""));
      }
    }
    expect(offenders, `static Vega imports: ${offenders.join(", ")}`).toEqual([]);
  });
});
