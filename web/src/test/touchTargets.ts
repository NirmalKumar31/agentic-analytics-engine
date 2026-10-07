/**
 * The touch-target floor, and the one exemption from it, declared once.
 *
 * The rule is "a control a thumb has to hit is at least 44px on its
 * shorter side", and it was written down three times: twice as a CSS
 * selector in `src/styles/responsive.css`, and once more as a heuristic
 * inside `golden.spec.ts`'s sweep of every visible control. The two
 * selectors agreed. The heuristic did not, and nothing could notice,
 * because the one element they disagree about is a control CI had never
 * rendered -- it appears only on a deployment with a provider key.
 *
 * So the class name lives here, both specs read it from here, and
 * `touchTargetExemption.test.ts` beside it reads this file *and* the two
 * stylesheets and fails when they stop naming the same thing.
 *
 * **Why it lives under `src/test/` rather than beside the spec.** The
 * production image builds the frontend from a context holding `web/src`
 * and nothing else, and `tsc -b` type-checks all of `src`, so a file
 * under `src/` importing `../e2e/...` compiles locally and fails only at
 * `RUN npm run build`. `src/test/buildContext.test.ts` enforces that, and
 * `src/test/preflight.ts` is the same arrangement: shared with the
 * browser suite, which imports it across the boundary in the one
 * direction that is safe.
 *
 * **Why an exemption exists at all.** WCAG 2.2 SC 2.5.8 exempts a target
 * whose size is constrained by the line of text it sits in: the privacy
 * disclosure on the landing page is a link inside a sentence, and giving
 * its summary a 44px block height would break the paragraph around it.
 * The exemption is for that shape of control and no other.
 *
 * **Why the exemption is a declared class and not a shape test.** The
 * sweep used to infer "inside a sentence" from `closest("p, li,
 * figcaption")`. That can never be true of this control: `<details>` is
 * not permitted content for `<p>`, so an inline disclosure in running
 * prose is always in some other block element, and the heuristic
 * structurally cannot see the single case the stylesheet was written
 * for. The class is the author saying which controls are meant to be
 * inline; the computed `display` is the browser confirming they really
 * are. The sweep requires **both**, so adding the class to a block-level
 * button buys nothing.
 */

/** A thumb needs this on the shorter side. WCAG 2.2 SC 2.5.5. */
export const MIN_TOUCH_PX = 44;

/**
 * The class an author puts on a `<details>` that belongs in a sentence.
 *
 * `responsive.css` reads it as `details:not(.disclosure) > summary` when
 * applying the floor, and `motion.css` is what actually makes it inline.
 */
export const INLINE_DISCLOSURE_CLASS = "disclosure";

/** Disclosures the floor applies to: every one that is not inline. */
export const STANDALONE_DISCLOSURE_SELECTOR = `details:not(.${INLINE_DISCLOSURE_CLASS}) > summary`;
