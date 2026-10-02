/**
 * Nothing under `src/` may import from outside `src/`.
 *
 * The production image builds the frontend from a narrower context than a
 * checkout: the Dockerfile copies `web/src`, the three tsconfigs,
 * `vite.config.ts` and `index.html` -- and nothing else. `npm run build`
 * runs `tsc -b`, which type-checks all of `src`, `src/test` included.
 *
 * So a file under `src/` that reaches outside it compiles locally, where
 * the sibling directory happens to exist, and fails only when the image is
 * built. That is exactly what happened: the e2e provider preflight was
 * written in `e2e/preflight.ts` and imported from `src/test`, every local
 * gate passed, and CI failed at `RUN npm run build` with TS2307 -- after
 * the branch was pushed and the PR opened.
 *
 * This turns that into a fast local failure. It is a build-context
 * invariant, not a style preference: the fix is to move the shared module
 * under `src/`, not to widen the Dockerfile, because the image has no use
 * for the specs themselves.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";

import { describe, expect, it } from "vitest";

const SRC = resolve(__dirname, "..");

function sourceFiles(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) {
      sourceFiles(path, found);
    } else if (/\.(ts|tsx)$/.test(entry)) {
      found.push(path);
    }
  }
  return found;
}

/** Every `from "..."` and `import("...")` specifier in a file. */
function specifiers(source: string): string[] {
  const out: string[] = [];
  const patterns = [
    /\bfrom\s+["']([^"']+)["']/g,
    /\bimport\s*\(\s*["']([^"']+)["']\s*\)/g,
    /\brequire\s*\(\s*["']([^"']+)["']\s*\)/g,
  ];
  for (const pattern of patterns) {
    for (const match of source.matchAll(pattern)) {
      if (match[1]) out.push(match[1]);
    }
  }
  return out;
}

describe("the frontend build context", () => {
  it("has no file under src/ importing from outside src/", () => {
    const escapes: string[] = [];

    for (const file of sourceFiles(SRC)) {
      for (const specifier of specifiers(readFileSync(file, "utf8"))) {
        // Bare specifiers are packages, resolved from node_modules, which
        // the image installs. Only relative paths can leave the tree.
        if (!specifier.startsWith(".")) continue;
        const target = resolve(file, "..", specifier);
        const rel = relative(SRC, target);
        if (rel.startsWith("..")) {
          escapes.push(`${relative(SRC, file)} -> ${specifier}`);
        }
      }
    }

    expect(
      escapes,
      "these resolve outside src/, so `tsc -b` fails when the image is " +
        "built from the Dockerfile's narrower context:\n  " +
        escapes.join("\n  "),
    ).toEqual([]);
  });

  it("sees the imports it is meant to police", () => {
    // Guards the guard: a specifier parser that matched nothing would make
    // the test above pass by finding no imports at all.
    const sample = readFileSync(join(SRC, "test", "e2ePreflight.test.ts"), "utf8");
    const found = specifiers(sample);
    expect(found).toContain("./preflight");
    expect(found.length).toBeGreaterThan(1);
    expect(sourceFiles(SRC).length).toBeGreaterThan(30);
  });
});
