/**
 * What holds the CSS architecture together.
 *
 * `styles.css` was one file of 2,234 lines. It is now twelve modules, cut
 * at boundaries that already existed in it. The cut was mechanical:
 * concatenating the modules in `main.tsx`'s import order reproduced the
 * original file byte for byte, and the only subsequent change was renaming
 * 199 legacy palette aliases to the tokens they were already defined as.
 *
 * None of that is self-enforcing. Three things can silently undo it:
 *
 *   1. Reordering the imports. The cascade depends on `states.css` and
 *      `motion.css` coming after the baseline modules and `responsive.css`
 *      coming last. Nothing about the file contents says so, and a
 *      reordering produces no error -- just different rendering.
 *   2. Reintroducing a bridge alias. `--text` and friends no longer exist,
 *      so `var(--text)` now resolves to nothing and the declaration is
 *      dropped. That reads as a missing style, not as a typo.
 *   3. Moving a rule to the module where it "belongs" by topic rather than
 *      where it sits in the cascade. The clearest trap is print: there are
 *      two `@media print` blocks, and the second is last in `states.css`
 *      on purpose. Merging them into `print.css` would let state rules
 *      override print treatment.
 *
 * These tests fail on each of those. They deliberately do not test that
 * files exist -- that is what the imports already do, and a build error is
 * a better signal than a test.
 */

import { describe, expect, it } from "vitest";

import {
  atRuleBlock,
  countAtRule,
  importOrder,
  moduleSource,
  modules,
  stylesheet,
  withoutComments,
} from "./stylesheet";

/**
 * The order the cascade was built in. Asserted literally, because the
 * point is to make a reordering fail.
 */
const EXPECTED_ORDER = [
  "styles/tokens.css",
  "styles/reset.css",
  "styles/shell.css",
  "styles/controls.css",
  "styles/workflow.css",
  "styles/findings.css",
  "styles/drawer.css",
  "styles/audit.css",
  "styles/report.css",
  "styles/print.css",
  "styles/motion.css",
  "styles/states.css",
  "styles/responsive.css",
];

/** The aliases the bridge used to define. None of them resolves any more. */
const RETIRED_ALIASES = [
  "bg",
  "bg-raised",
  "bg-panel",
  "bg-inset",
  "border",
  "border-strong",
  "line",
  "text",
  "text-muted",
  "text-dim",
  "accent",
  "accent-dim",
  "accent-rgb",
  "rejected",
  "rejected-rgb",
  "statistical",
  "statistical-rgb",
  "interpretation",
  "interpretation-rgb",
  "radius",
  "mono",
  "sans",
  "ease",
  "fast",
  "med",
  "shadow",
  "glow",
];

describe("stylesheet module order", () => {
  it("is exactly the order the cascade was built in", () => {
    expect(importOrder()).toEqual(EXPECTED_ORDER);
  });

  it("puts tokens first, because every other module reads them", () => {
    expect(importOrder()[0]).toBe("styles/tokens.css");
  });

  it("puts responsive.css last, because its containment must win", () => {
    expect(importOrder().at(-1)).toBe("styles/responsive.css");
  });

  it("keeps the override modules after every baseline module", () => {
    const order = importOrder();
    const baseline = ["styles/shell.css", "styles/report.css", "styles/controls.css"];
    for (const override of ["styles/motion.css", "styles/states.css"]) {
      for (const base of baseline) {
        expect(
          order.indexOf(override),
          `${override} must come after ${base}`,
        ).toBeGreaterThan(order.indexOf(base));
      }
    }
  });

  it("imports no stylesheet that the order does not account for", () => {
    // A thirteenth module added to main.tsx without being added here would
    // otherwise sit in the cascade untested.
    expect(importOrder().length).toBe(EXPECTED_ORDER.length);
  });
});

describe("the compatibility bridge is gone", () => {
  it("defines none of the retired aliases in tokens.css", () => {
    const tokens = withoutComments(moduleSource("styles/tokens.css"));
    for (const alias of RETIRED_ALIASES) {
      expect(
        new RegExp(`--${alias}\\s*:`).test(tokens),
        `tokens.css still defines --${alias}`,
      ).toBe(false);
    }
  });

  it("references none of the retired aliases from any module", () => {
    // An unresolvable var() is dropped by the browser, so this reads as a
    // missing style rather than as an error. Checked per module so the
    // failure names the file.
    const offenders: string[] = [];
    for (const [name, src] of modules()) {
      const css = withoutComments(src);
      for (const alias of RETIRED_ALIASES) {
        const uses = css.match(new RegExp(`var\\(\\s*--${alias}\\s*[,)]`, "g"));
        if (uses) offenders.push(`${name}: var(--${alias}) x${uses.length}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("references none of them from component source either", () => {
    // ProductHeader and ExecutionFlow set inline custom properties.
    const offenders: string[] = [];
    for (const file of ["components/ProductHeader.tsx", "components/ExecutionFlow.tsx", "App.tsx"]) {
      const src = moduleSource(file);
      for (const alias of RETIRED_ALIASES) {
        if (new RegExp(`var\\(\\s*--${alias}\\s*[,)]`).test(src)) {
          offenders.push(`${file}: var(--${alias})`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("leaves no var() fallback carrying a stale hardcoded colour", () => {
    // Five call sites read `var(--line, #2a2f3a)` and `var(--accent,
    // #6ea8fe)`. The fallbacks were unreachable, being old-palette dark
    // values that would have surfaced only if the token vanished -- which
    // is exactly what retiring the alias would have caused.
    const hex = withoutComments(stylesheet()).match(/var\(\s*--[\w-]+\s*,\s*#[0-9a-f]{3,8}/gi);
    expect(hex ?? []).toEqual([]);
  });
});

describe("rules stay in the module that owns their place in the cascade", () => {
  const css = withoutComments(stylesheet());

  it("defines focus-visible once, in states.css, including summary", () => {
    const states = withoutComments(moduleSource("styles/states.css"));
    // The one rule that gives every interactive element its focus ring.
    // `summary` is in the list because a disclosure is reachable by
    // keyboard and was previously left without a visible focus state.
    expect(states).toMatch(
      /:where\([^)]*\bsummary\b[^)]*\):focus-visible\s*\{/,
    );
  });

  it("keeps both print blocks, the second one last in states.css", () => {
    expect(countAtRule(css, "@media print")).toBe(2);
    expect(countAtRule(withoutComments(moduleSource("styles/print.css")), "@media print")).toBe(1);

    const states = withoutComments(moduleSource("styles/states.css"));
    expect(countAtRule(states, "@media print")).toBe(1);

    // What matters is that no ordinary state rule follows it, which would
    // override print treatment. Only the reduced-motion block may.
    const block = atRuleBlock(states, "@media print");
    expect(block).not.toBeNull();
    const tail = states.slice(states.indexOf(block!) + block!.length);
    const followingRules = [...tail.matchAll(/(^|\})\s*([^@{}]+)\{/g)].map((m) =>
      m[2]!.trim(),
    );
    expect(followingRules, "a plain rule follows @media print in states.css").toEqual(
      [],
    );
    expect(tail).toContain("@media (prefers-reduced-motion: reduce)");
  });

  it("keeps every reduced-motion block, in all four modules that animate", () => {
    for (const mod of [
      "styles/workflow.css",
      "styles/drawer.css",
      "styles/motion.css",
      "styles/states.css",
    ]) {
      expect(
        countAtRule(withoutComments(moduleSource(mod)), "@media (prefers-reduced-motion: reduce)"),
        `${mod} lost its reduced-motion block`,
      ).toBe(1);
    }
    // Plus the token overrides, which zero the motion budgets themselves.
    expect(
      countAtRule(
        withoutComments(moduleSource("styles/tokens.css")),
        "@media (prefers-reduced-motion: reduce)",
      ),
    ).toBe(1);
  });

  it("sizes the chart host in report.css and constrains it in states.css", () => {
    expect(withoutComments(moduleSource("styles/report.css"))).toMatch(/\.chart-host\s*\{/);
    // The rule that stops Vega's SVG overflowing its card.
    expect(withoutComments(moduleSource("styles/states.css"))).toMatch(
      /\.chart-host\s*>\s*div,\s*\.chart-host\s+svg\s*\{/,
    );
  });

  it("overrides the runtime Vega tooltip with semantic text and surface tokens", () => {
    // vega-embed injects a default tooltip sheet after application CSS.
    // Its #808080 key text is not sufficiently distinct from its translucent
    // white panel, and its dark theme is unrelated to our explicit theme.
    // Repeating the id is intentional specificity, not a selector accident:
    // it must outrank the dependency despite being loaded first.
    const report = withoutComments(moduleSource("styles/report.css"));
    expect(report).toMatch(
      /#vg-tooltip-element#vg-tooltip-element\s*\{[\s\S]*background:\s*var\(--surface-raised\)[\s\S]*color:\s*var\(--ink-primary\)/,
    );
    expect(report).toMatch(
      /#vg-tooltip-element#vg-tooltip-element\s+table\s+tr\s+td\.key\s*\{[\s\S]*color:\s*var\(--ink-secondary\)/,
    );
  });

  it("keeps narrow-viewport containment in responsive.css", () => {
    const responsive = withoutComments(moduleSource("styles/responsive.css"));
    // `min-width: 0` on the three elements that declare overflow-x. Without
    // it they expand to fit their content and take the page sideways.
    expect(responsive).toMatch(/\.steps,\s*\.flow,\s*\.table-wrap\s*\{\s*min-width:\s*0/);
  });

  it("wraps the topbar in responsive.css", () => {
    expect(withoutComments(moduleSource("styles/responsive.css"))).toMatch(
      /\.topbar\s*\{\s*flex-wrap:\s*wrap/,
    );
  });

  it("keeps the scrollable regions scrollable", () => {
    // `.flow` and `.steps` scroll horizontally; both are focusable in the
    // markup so a keyboard user can reach the scroll. The CSS half of that
    // is the overflow declaration.
    expect(css).toMatch(/\.steps\s*\{[^}]*overflow-x:\s*auto/);
    expect(css).toMatch(/\.flow\s*\{[^}]*overflow-x:\s*auto/);
  });

  it("keeps the WCAG 2.5.8 inline-disclosure exception", () => {
    const responsive = withoutComments(moduleSource("styles/responsive.css"));
    // The 44px touch-target floor applies to `details` summaries generally
    // but must not apply to `.disclosure`, which is an inline link inside a
    // paragraph and is exempt under SC 2.5.8.
    expect(responsive).toMatch(/details:not\(\.disclosure\)\s*>\s*summary/);
  });

  it("adds no z-index beyond the five this stylesheet already had", () => {
    // The `--z-*` ladder in tokens.css is declared and, as of this change,
    // entirely unused: every stacking context sets a raw number instead.
    // Adopting the ladder would alter computed values (1,1,40,60,61 ->
    // 10,10,100,200,300), which preserves all five stacking relationships
    // but is a behaviour change, and PR H's contract is that the rendered
    // output does not change. So the five are pinned here rather than
    // migrated, and a sixth cannot be added without a decision.
    const raw: string[] = [];
    for (const [name, src] of modules()) {
      if (name === "styles/tokens.css") continue;
      for (const m of withoutComments(src).matchAll(/z-index:\s*([^;]+);/g)) {
        if (!m[1]!.includes("var(--z-")) raw.push(`${name}: ${m[1]!.trim()}`);
      }
    }
    expect(raw.sort()).toEqual([
      "styles/drawer.css: 60",
      "styles/drawer.css: 61",
      "styles/findings.css: 1",
      "styles/motion.css: 1",
      "styles/shell.css: 40",
    ]);
    // The ladder itself stays, because PR I needs somewhere to put these.
    expect(withoutComments(moduleSource("styles/tokens.css"))).toMatch(/--z-[\w-]+\s*:/);
  });

  it("keeps the state card with the report it explains", () => {
    // `.report-workspace` is pulled to the top of the column with
    // `order: -1`. The state card is a sibling, so without its own order it
    // stayed in document order -- below the execution panels, a long scroll
    // from the outcome it describes. A reader met "Not answered" at the top
    // and found "Refused." somewhere further down.
    const states = withoutComments(moduleSource("styles/states.css"));
    expect(states).toMatch(/\.column\s*>\s*\.report-workspace\s*\{[^}]*order:\s*-1/);
    expect(states).toMatch(
      /\.column\s*>\s*\[data-testid="run-state-card"\]\s*\{[^}]*order:\s*-2/,
    );
  });

  it("adds no !important beyond the thirteen already justified", () => {
    // Not forbidden outright: all thirteen predate this change and each has
    // a reason that `!important` is the correct tool for.
    //
    //   motion.css x5  -- the `prefers-reduced-motion` idiom, which has to
    //                     beat every animation declared anywhere.
    //   print.css  x8  -- overriding Vega's *inline* SVG fills, which carry
    //                     higher precedence than any stylesheet rule.
    //
    // What this pins is that `!important` was not used as a shortcut to
    // make the token rename or the module split appear to work.
    const counts: Record<string, number> = {};
    for (const [name, src] of modules()) {
      const hits = withoutComments(src).match(/!\s*important/g);
      if (hits) counts[name] = hits.length;
    }
    expect(counts).toEqual({ "styles/print.css": 8, "styles/motion.css": 5 });
  });
});

describe("the split preserved the stylesheet", () => {
  it("keeps every module substantially intact", () => {
    // This asserted the exact total, 2,241 lines, which was the right check
    // for the change that created these modules: the claim then was that the
    // split reproduced one file byte for byte, and an exact count proved no
    // module had been truncated or duplicated.
    //
    // That claim is now historical, and modules legitimately grow. An exact
    // total would be "fixed" by bumping the number on every change, which
    // protects nothing. A per-module floor still catches the failure the
    // count was there for -- a module emptied or half-written by a bad merge
    // -- without pretending the stylesheet is frozen.
    const floors: Record<string, number> = {
      "styles/reset.css": 50,
      "styles/shell.css": 150,
      "styles/controls.css": 120,
      "styles/workflow.css": 120,
      "styles/findings.css": 120,
      "styles/drawer.css": 150,
      "styles/audit.css": 150,
      "styles/report.css": 300,
      "styles/print.css": 200,
      "styles/motion.css": 250,
      "styles/states.css": 250,
      "styles/responsive.css": 60,
    };
    for (const [name, floor] of Object.entries(floors)) {
      const lines = moduleSource(name).split("\n").length;
      expect(lines, `${name} is far smaller than expected`).toBeGreaterThanOrEqual(
        floor,
      );
    }
    // And every module in the import order has a floor, so a new one cannot
    // be added without being accounted for here.
    const covered = new Set(Object.keys(floors));
    const uncovered = importOrder().filter(
      (m) => m !== "styles/tokens.css" && !covered.has(m),
    );
    expect(uncovered).toEqual([]);
  });

  it("leaves no module empty", () => {
    for (const [name, src] of modules()) {
      expect(withoutComments(src).trim().length, `${name} is empty`).toBeGreaterThan(0);
    }
  });
});
