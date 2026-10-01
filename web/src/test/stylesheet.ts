/**
 * The stylesheet as the browser assembles it.
 *
 * `styles.css` used to be one 2,234-line file, so a test could read it and
 * see every rule in cascade order. It is now twelve modules, and the order
 * they are imported in is load-bearing: `states.css` and `motion.css` beat
 * the baseline rules by coming later, and `responsive.css` beats everything
 * by coming last.
 *
 * So these helpers derive the order from `main.tsx` rather than hard-coding
 * it. A test that hard-coded the list would keep passing after someone
 * reordered the imports -- which is the one change most likely to break the
 * cascade -- and `cssArchitecture.test.ts` is what pins the order itself.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";

const SRC = join(__dirname, "..");

/** The `styles/*.css` specifiers in `main.tsx`, in source order. */
export function importOrder(): string[] {
  const main = readFileSync(join(SRC, "main.tsx"), "utf8");
  return [...main.matchAll(/^import\s+["']\.\/(styles\/[\w.-]+\.css)["']/gm)].map(
    (m) => m[1]!,
  );
}

/** One module's source. */
export function moduleSource(specifier: string): string {
  return readFileSync(join(SRC, specifier), "utf8");
}

/**
 * Every module concatenated in import order — what the bundler emits, and
 * therefore what the cascade actually is.
 */
export function stylesheet(): string {
  return importOrder().map(moduleSource).join("");
}

/** Module specifier → source, in import order. */
export function modules(): Array<[string, string]> {
  return importOrder().map((s) => [s, moduleSource(s)]);
}

/**
 * The body of the nth `@media <query>` block, brace-matched.
 *
 * Returns null when there is no nth block, so callers can assert on the
 * count instead of silently testing nothing.
 */
export function atRuleBlock(
  css: string,
  prelude: string,
  nth = 0,
): string | null {
  let from = 0;
  for (let seen = 0; ; seen += 1) {
    const start = css.indexOf(prelude, from);
    if (start === -1) return null;
    const open = css.indexOf("{", start);
    if (open === -1) return null;
    let depth = 0;
    for (let i = open; i < css.length; i += 1) {
      if (css[i] === "{") depth += 1;
      else if (css[i] === "}") {
        depth -= 1;
        if (depth === 0) {
          if (seen === nth) return css.slice(start, i + 1);
          from = i + 1;
          break;
        }
      }
    }
    if (depth !== 0) throw new Error(`unterminated block after ${prelude}`);
  }
}

/** How many times an at-rule prelude appears. */
export function countAtRule(css: string, prelude: string): number {
  return css.split(prelude).length - 1;
}

/** Strip comments, so a selector inside prose is not mistaken for a rule. */
export function withoutComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

export const stylesDir = dirname(join(SRC, "styles/tokens.css"));
