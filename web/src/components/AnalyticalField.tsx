/**
 * The ambient analytical field behind the landing headline.
 *
 * Contour lines and a few coordinate ticks: the same geometry as the product
 * mark, at page scale. It exists so a mostly-empty first screen reads as
 * composed rather than unfinished, and it appears **only here**, never
 * behind a report, where anything decorative competes with the one thing on
 * the page that matters.
 *
 * Three constraints it is built to:
 *
 * - It never crosses type. The contours are drawn across the full canvas but
 *   the headline and the controls sit on their own stacking level above it,
 *   and the densities are chosen so nothing reads through them.
 * - It is not a chart. The tick labels are fixed decimals that encode
 *   nothing. A field that looked like real data would be a figure nobody can
 *   source, on the page that introduces a product about sourcing figures.
 * - It is inert. No animation, no parallax, no response to the pointer. See
 *   the note in `landing.css` about why the storyboard's ambient drift is
 *   not implemented here.
 *
 * `aria-hidden` and `focusable="false"`: it carries no information, so it is
 * not in the accessibility tree and not in the tab order.
 */

const CONTOURS_WIDE = [0, 1, 2, 3, 4].map((i) => {
  const y = 60 + i * 130;
  return `M0 ${y} C 354 ${y - 26}, 708 ${y + 22}, 1180 ${y - 12}`;
});

const CONTOURS_DENSE = Array.from({ length: 11 }, (_, i) => {
  const y = 136 + i * 34;
  const amp = 7 + (i % 4) * 4;
  return `M0 ${y} C 295 ${y - amp}, 649 ${y + amp}, 1180 ${y - amp * 0.5}`;
});

/** Fixed positions and fixed labels. These are a motif, not a measurement. */
const TICKS: Array<[number, number, string]> = [
  [165, 161, "0.18"],
  [460, 360, "0.42"],
  [802, 136, "0.71"],
  [979, 409, "0.86"],
  [637, 248, "0.57"],
];

export function AnalyticalField() {
  return (
    <svg
      className="analytical-field"
      viewBox="0 0 1180 620"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden="true"
      focusable="false"
      data-testid="analytical-field"
    >
      <g className="field-wide">
        {CONTOURS_WIDE.map((d) => (
          <path key={d} d={d} />
        ))}
      </g>
      <g className="field-dense">
        {CONTOURS_DENSE.map((d) => (
          <path key={d} d={d} />
        ))}
      </g>
      <g className="field-ticks">
        {TICKS.map(([x, y, label]) => (
          <g key={label}>
            <path d={`M${x - 4} ${y} L${x + 4} ${y} M${x} ${y - 4} L${x} ${y + 4}`} />
            <text x={x + 8} y={y + 3}>
              {label}
            </text>
          </g>
        ))}
      </g>
    </svg>
  );
}
