/**
 * What a hosted acceptance run must cover, in one place.
 *
 * The spec reads this to build its cases and the report checker reads it to
 * decide whether a run was complete. Keeping both on one list is what makes
 * "fail if any expected viewport or state is absent" mean something: a
 * widened matrix is a wider run, and a narrowed one is a visible edit to
 * this file rather than a quiet gap in the evidence.
 */

/** Six widths: two phones, a tablet, a laptop and two desktops. */
export const WIDTHS = [360, 390, 768, 1024, 1440, 1920] as const;

export const THEMES = ["light", "dark"] as const;

/**
 * Every state the sweep has to reach, named exactly as the spec titles it.
 *
 * The checker matches test titles against these, so a renamed test that is
 * not renamed here fails the run rather than silently dropping a state.
 */
export const STATES = [
  "landing",
  "composer",
  "report",
  "execution-graph",
  "evidence-drawer",
  "focus-restoration",
  "compare",
  "terminal-refused",
  "reduced-motion",
  "print",
] as const;

export type Width = (typeof WIDTHS)[number];
export type Theme = (typeof THEMES)[number];
export type State = (typeof STATES)[number];

/** `w1024-dark`. Parsed back by the checker, so the shape is fixed. */
export function projectName(width: Width, theme: Theme): string {
  return `w${width}-${theme}`;
}

export const PROJECTS = WIDTHS.flatMap((width) =>
  THEMES.map((theme) => ({ width, theme, name: projectName(width, theme) })),
);

/** How many (project, state) cells a complete run has to report. */
export const EXPECTED_CELLS = PROJECTS.length * STATES.length;
