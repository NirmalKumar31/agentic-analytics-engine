/**
 * The touch-target floor and its exemption, pinned across both copies.
 *
 * One rule, "a control a thumb hits is at least 44px on its shorter
 * side, unless it is a disclosure that lives inside a sentence", is
 * stated in two languages that cannot read each other. `responsive.css`
 * states it as a selector. `e2e/golden.spec.ts` states it as a sweep over
 * every visible control. They were written separately and they disagreed,
 * and the disagreement was invisible because the single element they
 * disagree about is a control CI never rendered: the privacy disclosure
 * appears only when the server reports `model_inference_remote`, which
 * needs a provider key, which CI has not got and production has.
 *
 * The class name now lives in `touchTargets.ts` beside this file and the
 * spec reads it from there. The stylesheet still cannot, so this is the seam: it reads
 * the constant and the stylesheets and fails when they stop naming the
 * same thing.
 *
 * **Why this is not circular.** It does not check that the exemption is
 * correct. `golden.spec.ts` measures real boxes in a real browser for
 * that. It checks that the two statements of it are about the same class,
 * and that the stylesheet still gives that class the inline `display` the
 * spec's exemption requires. Either one changing alone is the failure
 * this exists to catch.
 */

import { describe, expect, it } from "vitest";

import {
  INLINE_DISCLOSURE_CLASS,
  MIN_TOUCH_PX,
  STANDALONE_DISCLOSURE_SELECTOR,
} from "./touchTargets";
import { moduleSource, withoutComments } from "./stylesheet";

/** `golden.spec.ts` is the other half of the rule, read as text. */
import { readFileSync } from "node:fs";
import { join } from "node:path";

function goldenSource(): string {
  return readFileSync(join(__dirname, "..", "..", "e2e", "golden.spec.ts"), "utf8");
}

describe("the shared declaration", () => {
  it("names a floor WCAG asks for", () => {
    expect(MIN_TOUCH_PX).toBe(44);
  });

  it("builds the standalone selector from the one class name", () => {
    expect(STANDALONE_DISCLOSURE_SELECTOR).toBe(
      `details:not(.${INLINE_DISCLOSURE_CLASS}) > summary`,
    );
  });
});

describe("the stylesheet exempts what the spec exempts", () => {
  const responsive = withoutComments(moduleSource("styles/responsive.css"));

  it("applies the floor to every disclosure that is not the exempt class", () => {
    // The whole point: the stylesheet's `:not()` and the spec's exemption
    // have to name the same class, or one of them is protecting a
    // control the other is not.
    expect(responsive).toContain(STANDALONE_DISCLOSURE_SELECTOR);
  });

  it("gives that floor the pinned height", () => {
    const rule = responsive.slice(
      responsive.indexOf(STANDALONE_DISCLOSURE_SELECTOR),
    );
    const body = rule.slice(rule.indexOf("{"), rule.indexOf("}") + 1);
    expect(body).toContain(`min-height: ${MIN_TOUCH_PX}px`);
  });

  it("exempts no other disclosure class", () => {
    // A second exemption would be a second rule with nothing reading it.
    // `details:not(.something-else) > summary` must not exist.
    const exemptions = [
      ...responsive.matchAll(/details:not\(\.([\w-]+)\)\s*>\s*summary/g),
    ].map((match) => match[1]);
    expect(exemptions.length).toBeGreaterThan(0);
    expect([...new Set(exemptions)]).toEqual([INLINE_DISCLOSURE_CLASS]);
  });

  it("keeps the exempt summary inline, which is what makes it exempt", () => {
    /*
      The spec's exemption requires a computed `display` of `inline` or
      `inline-block` *as well as* the class. That is deliberate -- the
      class alone must not buy an exemption for a block control, but it
      means the exemption silently stops applying if the stylesheet ever
      stops making the summary inline, and the symptom would be a
      failing touch-target sweep with no obvious cause.

      `motion.css` is where that display is set. The `[open]` rule
      deliberately turns it back into a block, and that case is not
      exempt: an open disclosure's summary is no longer in a line of
      text, so the floor is right to apply to it.
    */
    const motion = withoutComments(moduleSource("styles/motion.css"));
    const closed = motion.slice(
      motion.indexOf(`.${INLINE_DISCLOSURE_CLASS} > summary`),
    );
    expect(closed.slice(0, closed.indexOf("}"))).toContain("display: inline");
  });
});

describe("the browser spec reads the declaration rather than restating it", () => {
  const golden = goldenSource();

  it("imports the shared names", () => {
    expect(golden).toContain('from "../src/test/touchTargets"');
    expect(golden).toContain("INLINE_DISCLOSURE_CLASS");
    expect(golden).toContain("STANDALONE_DISCLOSURE_SELECTOR");
  });

  it("spells the exempt class nowhere else", () => {
    /*
      The copy that drifted was a literal. Two tests in that file now
      reach the class through the import, and a third literal would be a
      third copy free to disagree again, so the only occurrences
      allowed are inside a template that interpolates the constant.
      `details.${exemptClass}` is such a template; `details.disclosure`
      would not be.
    */
    const literal = new RegExp(`\\.${INLINE_DISCLOSURE_CLASS}\\b`, "g");
    const occurrences = [...golden.matchAll(literal)];
    expect(
      occurrences,
      `golden.spec.ts spells ".${INLINE_DISCLOSURE_CLASS}" literally; import it instead`,
    ).toEqual([]);
  });

  it("renders the keyed-only control before sweeping for small targets", () => {
    /*
      The sweep is a measurement over whatever is on the page, so an
      element that fails to render makes it pass. It was passing that
      way: the disclosure needs `model_inference_remote`, CI has no
      provider key, and the control the exemption exists for was never
      on the page CI measured.

      `advertiseAi` supplies the configuration and the spec asserts the
      control is visible before measuring. Both have to stay.
    */
    expect(golden).toContain("advertiseAi(page)");
    expect(golden).toMatch(/toBeVisible\(\)/);
  });
});
