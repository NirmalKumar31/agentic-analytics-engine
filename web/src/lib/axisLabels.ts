/**
 * How far to turn the category labels under a chart.
 *
 * Vega rotates a nominal x axis to vertical by default, and it does it
 * whatever the labels are: four words at 1920px printed on their sides,
 * which is slower to read than anything else on the page and is not what
 * the approved mockup shows. The engine's own specifications for the demo
 * warehouse set `-30` and look as intended, so the product was rotating
 * labels two different ways depending on which path produced the chart.
 *
 * The angle is decided from the labels themselves rather than being fixed,
 * because the two failures are opposite. Flat labels on 33 categories
 * collide, and Vega resolves a collision by *dropping* labels -- a chart
 * that silently loses most of its axis is worse than one that is hard to
 * read. Angled labels on four short words waste the reader's time.
 *
 * This is applied through `config.axisX`, which a specification's own
 * `axis.labelAngle` still overrides: the specifications that already made
 * this decision keep it.
 */

/** Flat. Nothing collides and nothing is turned. */
export const FLAT = 0;
/** Angled. Every label is kept, and none of them is read sideways. */
export const ANGLED = -30;

/** Beyond this many categories, flat labels start to collide. */
const CROWDED = 8;
/** Beyond this many characters, so do shorter lists of them. */
const LONG = 12;

interface Encoded {
  field?: unknown;
  type?: unknown;
}

/**
 * `null` where the decision is not ours to make: a quantitative or temporal
 * axis has its own conventions, and a specification that named an angle has
 * already decided.
 */
export function categoryLabelAngle(spec: unknown): number | null {
  if (!spec || typeof spec !== "object") return null;
  const record = spec as Record<string, unknown>;

  const encoding = record.encoding as Record<string, unknown> | undefined;
  const x = encoding?.x as (Encoded & { axis?: unknown }) | undefined;
  if (!x || typeof x.field !== "string") return null;
  if (x.type !== "nominal" && x.type !== "ordinal") return null;

  const axis = x.axis as Record<string, unknown> | undefined;
  if (axis && axis.labelAngle !== undefined) return null;

  const data = record.data as { values?: unknown } | undefined;
  const values = Array.isArray(data?.values) ? data.values : [];
  const labels = new Set<string>();
  for (const row of values) {
    if (!row || typeof row !== "object") continue;
    const value = (row as Record<string, unknown>)[x.field];
    if (value === null || value === undefined) continue;
    labels.add(String(value));
  }

  // No rows to measure: leave it alone rather than guess.
  if (labels.size === 0) return null;

  const longest = Math.max(...[...labels].map((label) => label.length));
  return labels.size <= CROWDED && longest <= LONG ? FLAT : ANGLED;
}
