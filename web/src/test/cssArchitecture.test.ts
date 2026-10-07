/**
 * What holds the CSS architecture together.
 *
 * `styles.css` was one file of 2,234 lines. It is now seventeen modules,
 * cut at boundaries that already existed in it. The cut was mechanical:
 * concatenating the modules in `main.tsx`'s import order reproduced the
 * original file byte for byte, and the only subsequent change was renaming
 * 199 legacy palette aliases to the tokens they were already defined as.
 *
 * None of that is self-enforcing. Three things can silently undo it:
 *
 *   1. Reordering the imports. The cascade depends on `states.css` and
 *      `motion.css` coming after the baseline modules and `responsive.css`
 *      coming last. Nothing about the file contents says so, and a
 *      reordering produces no error, just different rendering.
 *   2. Reintroducing a bridge alias. `--text` and friends no longer exist,
 *      so `var(--text)` now resolves to nothing and the declaration is
 *      dropped. That reads as a missing style, not as a typo.
 *   3. Moving a rule to the module where it "belongs" by topic rather than
 *      where it sits in the cascade. The clearest trap is print: six
 *      modules declare an `@media print` block, and `print.css` has to be
 *      the last of them. A print rule added to `motion.css`, `states.css`
 *      or `responsive.css`, all of which load after it, would override
 *      the report's print treatment with no error anywhere.
 *
 * These tests fail on each of those. They deliberately do not test that
 * files exist, because that is what the imports already do, and a build error is
 * a better signal than a test.
 */

import { describe, expect, it } from "vitest";

import {
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
  "styles/foundation.css",
  "styles/shell.css",
  "styles/landing.css",
  "styles/composer.css",
  "styles/timeline.css",
  "styles/answer.css",
  "styles/controls.css",
  "styles/workflow.css",
  "styles/findings.css",
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
    // A module added to main.tsx without being added here would otherwise
    // sit in the cascade untested.
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
    // Components that set inline custom properties. `ExecutionFlow.tsx` was
    // one and went with the agent DAG; the run timeline that replaced it
    // sets no inline properties at all, so its state comes from
    // `data-state` and the stylesheet rather than from JavaScript.
    const offenders: string[] = [];
    for (const file of ["components/ProductHeader.tsx", "components/RunTimeline.tsx", "App.tsx"]) {
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
    // values that would have surfaced only if the token vanished, which
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

  it("keeps every print block, with print.css last in the cascade", () => {
    /*
     * Six, in cascade order:
     *   foundation.css  the printed *page*: size, margins, colour-adjust,
     *                   and the white palette every later block assumes.
     *   tokens.css      the motion durations, set to zero. It is first in
     *                   the cascade because tokens are inputs, and this
     *                   block declares nothing a later module could want
     *                   to override: `print.css` withdraws every
     *                   transition outright, and this is what stops an
     *                   engine starting one anyway from the screen's
     *                   duration. Firefox does, and the first frame of a
     *                   printed sheet was the midpoint between the dark
     *                   canvas and white.
     *   landing.css     the landing's own controls, which paper cannot use.
     *   composer.css    the composer and the side sheet, both screen-only.
     *   timeline.css    the run timeline, which is a record of a run in
     *                   progress and has nothing to say on paper.
     *   answer.css      the answer block's own screen-only affordances.
     *   print.css       how the report's blocks print, and last, so that
     *                   nothing overrides it.
     *
     * This used to be seven, with the last one at the end of `states.css`,
     * and the claim was the same: no later rule may override print
     * treatment. That block's only remaining rules named
     * `.presentation-report .answer-card`, a component deleted in step E,
     * so it was removed rather than refilled. The guarantee is now carried
     * structurally instead: `print.css` is the last module that declares
     * a print block at all, which is stronger than ordering within one
     * file, because it cannot be undone by appending to `states.css`.
     */
    const PRINTS = [
      "styles/tokens.css",
      "styles/foundation.css",
      "styles/landing.css",
      "styles/composer.css",
      "styles/timeline.css",
      "styles/answer.css",
      "styles/print.css",
    ];
    expect(countAtRule(css, "@media print")).toBe(PRINTS.length);
    for (const mod of PRINTS) {
      expect(
        countAtRule(withoutComments(moduleSource(mod)), "@media print"),
        `${mod} lost its print block`,
      ).toBe(1);
    }

    // foundation's must come first: it resets the palette to ink on white,
    // and a later block restating a colour has to win over that rather than
    // be undone by it.
    const order = importOrder();
    expect(
      order.indexOf("styles/foundation.css"),
      "foundation.css must print-reset before print.css restates colours",
    ).toBeLessThan(order.indexOf("styles/print.css"));

    // And print.css is the last word: every module loaded after it declares
    // no print rules at all.
    const after = order.slice(order.indexOf("styles/print.css") + 1);
    expect(after.length, "print.css is the last module in the cascade").toBeGreaterThan(0);
    for (const mod of after) {
      expect(
        countAtRule(withoutComments(moduleSource(mod)), "@media print"),
        `${mod} loads after print.css and overrides print treatment`,
      ).toBe(0);
    }
  });

  it("keeps every reduced-motion block, in all three modules that animate", () => {
    // Was four. `drawer.css` was the fourth; it is deleted, and so is the
    // drawer whose entrance animation its block withdrew.
    for (const mod of [
      "styles/workflow.css",
      "styles/motion.css",
      "styles/states.css",
    ]) {
      expect(
        countAtRule(withoutComments(moduleSource(mod)), "@media (prefers-reduced-motion: reduce)"),
        `${mod} lost its reduced-motion block`,
      ).toBe(1);
    }
    // foundation.css is deliberately absent: it declares no animation and no
    // transition, so a reduced-motion block there would be guarding nothing.
    // Asserted rather than assumed, because the day it does animate, the
    // guard has to arrive with the animation.
    expect(
      withoutComments(moduleSource("styles/foundation.css")),
      "foundation.css animates; it now needs a reduced-motion block",
    ).not.toMatch(/\b(animation|transition)\s*:/);

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
    // `min-width: 0` on the elements that declare overflow-x. Without it
    // they expand to fit their content and take the page sideways.
    //
    // `.steps` was one of three and is gone with the stepper; `.flow` was
    // another and went with the branch diagram. `.scroll-x` in
    // foundation.css carries both declarations together for everything
    // added from here on, which is the arrangement that cannot drift apart.
    expect(responsive).toMatch(/\.table-wrap\s*\{\s*min-width:\s*0/);
    expect(withoutComments(moduleSource("styles/foundation.css"))).toMatch(
      /\.scroll-x\s*\{[^}]*overflow-x:\s*auto;[^}]*min-width:\s*0/,
    );

    /*
     * And the chain above it, which is what lets `min-width: 0` matter.
     *
     * `.report-table` and `.report-visual` declare `display: grid` with no
     * columns, so the implicit track is `auto`, and an `auto` track takes
     * its content's max-content width. A five-column result table sized the
     * track to 678px inside a 358px section and the page scrolled 304px
     * sideways at 390px; the scroll container three levels down had already
     * been handed the blown-out width and could never engage.
     *
     * Pinned here rather than in the browser suite because reproducing it
     * costs a real analysis against a container with no budget left for
     * one, and because the failure is a rule going missing rather than a
     * value drifting.
     */
    expect(responsive).toMatch(
      /\.report-table,\s*\.report-visual\s*\{\s*grid-template-columns:\s*minmax\(0,\s*1fr\)/,
    );
    expect(responsive).toMatch(
      /\.result-panel,\s*\.chart-card\s*\{\s*min-width:\s*0/,
    );
  });

  it("wraps the topbar in responsive.css", () => {
    expect(withoutComments(moduleSource("styles/responsive.css"))).toMatch(
      /\.topbar\s*\{\s*flex-wrap:\s*wrap/,
    );
  });

  it("keeps the scrollable regions scrollable", () => {
    // A wide result table scrolls inside its own frame rather than taking
    // the document sideways. `.flow` and `.steps` were the other two and
    // went with the branch diagram and the stepper; `.scroll-x` is the
    // general case every surface added since uses.
    expect(css).toMatch(/\.table-wrap\s*\{[^}]*overflow:\s*auto/);
    expect(css).toMatch(/\.scroll-x\s*\{[^}]*overflow-x:\s*auto/);
  });

  it("keeps the WCAG 2.5.8 inline-disclosure exception", () => {
    const responsive = withoutComments(moduleSource("styles/responsive.css"));
    // The 44px touch-target floor applies to `details` summaries generally
    // but must not apply to `.disclosure`, which is an inline link inside a
    // paragraph and is exempt under SC 2.5.8.
    expect(responsive).toMatch(/details:not\(\.disclosure\)\s*>\s*summary/);
  });

  it("stacks through the token ladder, with three raw numbers left", () => {
    /*
     * The `--z-*` ladder in tokens.css was declared and entirely unused:
     * every stacking context set a raw number. The surfaces rebuilt in this
     * redesign adopted it: the landing field sits on `--z-behind`, the
     * scrim on `--z-drawer`, the side sheet on `--z-overlay`, and the two
     * raw 60/61 in `drawer.css` went with that module.
     *
     * Three raw numbers remain, each a local stacking decision inside one
     * component rather than a page-level layer:
     *
     *   findings.css: 1  a sticky table header above its own rows
     *   motion.css:   1  the cite highlight above the cell it marks
     *   shell.css:   40  the sticky top bar
     *
     * They are pinned so that a fourth cannot appear without a decision
     * about whether it belongs on the ladder.
     */
    const raw: string[] = [];
    for (const [name, src] of modules()) {
      if (name === "styles/tokens.css") continue;
      for (const m of withoutComments(src).matchAll(/z-index:\s*([^;]+);/g)) {
        if (!m[1]!.includes("var(--z-")) raw.push(`${name}: ${m[1]!.trim()}`);
      }
    }
    expect(raw.sort()).toEqual([
      "styles/findings.css: 1",
      "styles/motion.css: 1",
      "styles/shell.css: 40",
    ]);
    expect(withoutComments(moduleSource("styles/tokens.css"))).toMatch(/--z-[\w-]+\s*:/);
  });

  it("orders the report column by document order alone", () => {
    /*
     * Two `order` rules used to live in states.css: `.report-workspace` at
     * -1 and the state card at -2. They existed because the execution
     * panels streamed above the report, so the outcome ended up a long
     * scroll below them.
     *
     * Those panels are gone. The activity log is in the evidence drawer
     * and the timeline is not resident once a report exists, and the
     * rules then lifted the terminal state *above the dataset context
     * strip*: outside the reading column, before the reader had been told
     * which file it was about.
     *
     * Nothing in the column may reorder itself again without a reason.
     */
    const states = withoutComments(moduleSource("styles/states.css"));
    expect(states).not.toMatch(/\.column\s*>[^{]*\{[^}]*order:/);
  });

  it("adds no !important beyond the twenty-three that need one", () => {
    /*
     * Not forbidden outright. Each of these is a case where `!important` is
     * the correct tool, and the two modules are the only two that may use
     * it at all.
     *
     *   motion.css x4   the `prefers-reduced-motion` idiom, which has to
     *                   beat every animation declared anywhere.
     *
     *   print.css x19   five kinds, all of them overriding something this
     *                   stylesheet does not control:
     *                     - the outline every chart mark gets on paper,
     *                       because Vega bakes the series fills in at embed
     *                       time and a chart embedded in dark mode would
     *                       otherwise print pale shapes with no discernible
     *                       boundary (x2);
     *                     - every animation and transition in the
     *                       stylesheet, because Chromium restarts them when
     *                       it lays the page out to print and an entrance
     *                       that begins at `opacity: 0` then paints
     *                       nothing at all (x2);
     *                     - Vega writes its fills and strokes *inline* on
     *                       the SVG it renders, which outranks any rule
     *                       here, so the ink axis text, the white plot
     *                       background and the grey gridlines each need one
     *                       (x4, plus x4 sizing the SVG to the page);
     *                     - `[hidden]` and `details:not([open])` are UA
     *                       sheet rules, and the evidence appendix exists
     *                       precisely to be shown on paper when it is
     *                       hidden on screen (x4);
     *                     - the screen chrome and the appendix's controls
     *                       are hidden against later, more specific screen
     *                       rules (x3).
     *
     * Was thirteen, of which eight were print. Step H rebuilt print.css
     * from the new hierarchy, and two things grew the count: the appendix
     * has to defeat the UA stylesheet, which nothing else here has to do,
     * and the motion reset has to beat every animation declared anywhere,
     * which is the same argument `motion.css` makes for its four.
     */
    const counts: Record<string, number> = {};
    for (const [name, src] of modules()) {
      const hits = withoutComments(src).match(/!\s*important/g);
      if (hits) counts[name] = hits.length;
    }
    expect(counts).toEqual({ "styles/print.css": 19, "styles/motion.css": 4 });
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
    // count was there for (a module emptied or half-written by a bad merge
    //) without pretending the stylesheet is frozen.
    //
    // Step H lowered six of them. Every component deleted in B-G left its
    // selectors behind, and the print suite's "not styled anywhere else
    // either" assertions surfaced fifty rules that matched nothing: the
    // execution lanes, the branch diagram, the stepper, the right rail,
    // the provenance drawer's key/value list and cost block, the metric
    // tiles, the skeletons. Removing them is the point of the step, so the
    // floors move with them rather than holding the dead weight in place.
    const floors: Record<string, number> = {
      "styles/reset.css": 50,
      "styles/foundation.css": 150,
      "styles/shell.css": 150,
      "styles/landing.css": 150,
      "styles/composer.css": 180,
      "styles/timeline.css": 100,
      "styles/answer.css": 150,
      "styles/controls.css": 120,
      // 120 -> 80: the branch diagram's nodes, edges and travelling dash.
      "styles/workflow.css": 80,
      "styles/findings.css": 120,
      // 150 -> 50: the execution lanes. What is left is the notice block
      // and the contract diff, both of which the Compare view still uses.
      "styles/audit.css": 50,
      // 300 -> 210: the metric tiles and the shared-usage grid.
      "styles/report.css": 210,
      "styles/print.css": 200,
      // Lowered from 250 when the `body::before` plotting grid, its
      // drift keyframes and its three guards were deleted: the landing
      // field replaced that texture. A floor exists to catch a module
      // emptied by a bad merge, not to freeze a module against
      // deliberate removal.
      "styles/motion.css": 200,
      // 250 -> 190: the cell chips, the caveat list, the skeletons and the
      // presentation report's print block.
      "styles/states.css": 190,
      // 60 -> 45: the flow diagram's containment and overflow rules.
      "styles/responsive.css": 45,
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

  it("leaves every module brace-balanced", () => {
    // A stray `}` from a bad edit is only a *warning* from esbuild: the
    // build still succeeds, emits the sheet, and silently drops every rule
    // after the error. One slipped through exactly that way while the
    // landing was being built, and the production build said `built in
    // 2.30s` with a warning nobody had to read.
    for (const [name, src] of modules()) {
      const css = withoutComments(src);
      const open = (css.match(/\{/g) ?? []).length;
      const close = (css.match(/\}/g) ?? []).length;
      expect(close - open, `${name} has unbalanced braces`).toBe(0);
    }
  });

  it("leaves no module empty", () => {
    for (const [name, src] of modules()) {
      expect(withoutComments(src).trim().length, `${name} is empty`).toBeGreaterThan(0);
    }
  });
});
