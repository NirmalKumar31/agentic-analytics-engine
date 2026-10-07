/**
 * The five data-series colours, re-measured.
 *
 * A chart's marks carry meaning, so two things have to hold and neither is
 * obvious by eye:
 *
 *   1. Each mark is legible against the surface a plot sits on. WCAG 1.4.11
 *      asks 3:1 of a graphical object.
 *   2. The five stay apart for a reader with a colour vision deficiency.
 *
 * The second is the one that gets assumed. Hue alone cannot deliver it:
 * red-green deficiency affects roughly one man in twelve and collapses the
 * very distinction most categorical palettes rely on. What survives is
 * lightness, so the ramp climbs a deliberate contrast ladder and the hue
 * order was chosen by searching every assignment for the best worst case.
 *
 * Three versions of this ramp were measured before one passed:
 *
 *   - every colour solved to the same 3:1 target: tritanopia fell to dE 6.9,
 *     because identical contrast meant identical lightness and hue was doing
 *     all the work;
 *   - a naive ladder: teal and red landed one rung apart, where deuteranopia
 *     merges them, and the worst pair fell to dE 10.6;
 *   - the searched assignment: dE 15.2 across both themes and all three
 *     deficiencies.
 *
 * The dichromacy simulation is the Viénot/Brettel LMS reduction, and the
 * distance is CIE76 in Lab. Neither is the last word on perception; CIE76
 * over-weights saturated hues, and real dichromats are not uniform, which
 * is why the threshold here is well clear of the usual "just noticeable"
 * figure rather than sitting on it.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const TOKENS = readFileSync(join(__dirname, "..", "styles", "tokens.css"), "utf8");
const clean = TOKENS.replace(/\/\*[\s\S]*?\*\//g, "");

function blockAt(css: string, from: number): string {
  const open = css.indexOf("{", from);
  let depth = 0;
  for (let i = open; i < css.length; i += 1) {
    if (css[i] === "{") depth += 1;
    else if (css[i] === "}") {
      depth -= 1;
      if (depth === 0) return css.slice(open + 1, i);
    }
  }
  throw new Error("unterminated block");
}

function hexes(block: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of block.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) out[m[1]!] = m[2]!;
  return out;
}

const LIGHT = hexes(blockAt(clean, clean.indexOf(":root")));
const DARK = {
  ...LIGHT,
  ...hexes(blockAt(clean, clean.indexOf(':root[data-theme="dark"]'))),
};

type RGB = [number, number, number];
const parse = (h: string): RGB =>
  [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)) as RGB;

const channel = (v: number) => {
  const c = v / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
};
const luminance = ([r, g, b]: RGB) =>
  0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
function contrast(a: RGB, b: RGB): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}

// Viénot/Brettel LMS reduction.
const toLMS = ([r, g, b]: RGB): RGB => [
  0.31399 * r + 0.63951 * g + 0.04649 * b,
  0.15537 * r + 0.75789 * g + 0.0867 * b,
  0.01775 * r + 0.10944 * g + 0.87247 * b,
];
const fromLMS = ([l, m, s]: RGB): RGB => [
  5.47221 * l - 4.6419 * m + 0.16963 * s,
  -1.1252 * l + 2.29317 * m - 0.1678 * s,
  0.0298 * l - 0.19318 * m + 1.16364 * s,
];
const REDUCE: Record<string, (lms: RGB) => RGB> = {
  // Each reduction discards one cone response and reconstructs it from the
  // other two, which is why one of the three inputs is unused in each.
  deuteranopia: ([l, , s]) => [l, 0.49421 * l + 1.24827 * s, s],
  protanopia: ([, m, s]) => [2.02344 * m - 2.52581 * s, m, s],
  tritanopia: ([l, m]) => [l, m, -0.395913 * l + 0.801109 * m],
};
const simulate = (rgb: RGB, kind: string): RGB => fromLMS(REDUCE[kind]!(toLMS(rgb)));

function lab([r, g, b]: RGB): RGB {
  const f = (t: number) => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
  const [R, G, B] = [channel(r), channel(g), channel(b)];
  const X = (0.4124 * R + 0.3576 * G + 0.1805 * B) / 0.95047;
  const Y = 0.2126 * R + 0.7152 * G + 0.0722 * B;
  const Z = (0.0193 * R + 0.1192 * G + 0.9505 * B) / 1.08883;
  return [116 * f(Y) - 16, 500 * (f(X) - f(Y)), 200 * (f(Y) - f(Z))];
}
function deltaE(a: RGB, b: RGB): number {
  const [l1, a1, b1] = lab(a);
  const [l2, a2, b2] = lab(b);
  return Math.hypot(l1 - l2, a1 - a2, b1 - b2);
}

const SERIES = ["series-1", "series-2", "series-3", "series-4", "series-5"];
/** The surfaces a plot is drawn on. */
const PLOT_SURFACES = ["surface-paper", "surface-raised"];

const THEMES: Array<[string, Record<string, string>]> = [
  ["light", LIGHT],
  ["dark", DARK],
];

describe("the series ramp exists in both themes", () => {
  it.each(THEMES)("%s defines all five", (_name, palette) => {
    for (const token of SERIES) {
      expect(palette[token], `--${token} is missing`).toMatch(/^#[0-9a-fA-F]{6}$/);
    }
  });

  it("gives the two themes different ramps", () => {
    // A ramp tuned for a pale surface, left unchanged on a near-black one,
    // is the defect that made three of these hardcoded for years.
    expect(SERIES.map((t) => LIGHT[t])).not.toEqual(SERIES.map((t) => DARK[t]));
  });
});

describe("every series mark is legible against the plot surface", () => {
  for (const [theme, palette] of THEMES) {
    for (const token of SERIES) {
      it(`${theme}: --${token} clears 3:1`, () => {
        const worst = Math.min(
          ...PLOT_SURFACES.map((s) => contrast(parse(palette[token]!), parse(palette[s]!))),
        );
        expect(
          Number(worst.toFixed(2)),
          `--${token} (${palette[token]}) against the plot surface`,
        ).toBeGreaterThanOrEqual(3);
      });
    }
  }
});

describe("the five stay apart for a colour-blind reader", () => {
  // Comfortably above the "just noticeable" figure, because the simulation
  // and the distance metric are both approximations.
  const FLOOR = 12;

  for (const [theme, palette] of THEMES) {
    for (const kind of ["normal", "deuteranopia", "protanopia", "tritanopia"]) {
      it(`${theme}: ${kind}`, () => {
        const ramp = SERIES.map((t) => parse(palette[t]!));
        const seen = kind === "normal" ? ramp : ramp.map((c) => simulate(c, kind));
        let worst = Infinity;
        let pair = "";
        for (let i = 0; i < seen.length; i += 1) {
          for (let j = i + 1; j < seen.length; j += 1) {
            const d = deltaE(seen[i]!, seen[j]!);
            if (d < worst) {
              worst = d;
              pair = `${SERIES[i]} / ${SERIES[j]}`;
            }
          }
        }
        expect(
          Number(worst.toFixed(1)),
          `closest pair under ${kind} is ${pair}`,
        ).toBeGreaterThanOrEqual(FLOOR);
      });
    }
  }

  it("climbs a lightness ladder rather than relying on hue", () => {
    // The property that makes the above hold. Solving every colour to the
    // same contrast target put them all at one lightness, and tritanopia
    // fell to 6.9 because hue was carrying the whole distinction.
    for (const [, palette] of THEMES) {
      const steps = SERIES.map((t) =>
        contrast(parse(palette[t]!), parse(palette["surface-paper"]!)),
      );
      const gaps = steps.slice(1).map((v, i) => v - steps[i]!);
      for (const gap of gaps) expect(Math.abs(gap)).toBeGreaterThan(0.6);
    }
  });
});

describe("the chart uses the tokens", () => {
  it("names every series token in the Vega config", () => {
    // Three of the five were hardcoded hexes, so they ignored the theme and
    // survived a whole palette replacement unchanged.
    const chart = readFileSync(join(__dirname, "..", "components", "Chart.tsx"), "utf8");
    const range = chart.slice(chart.indexOf("range: {"), chart.indexOf("range: {") + 600);
    for (const token of SERIES) {
      expect(range, `${token} is not read from a token`).toContain(`--${token}`);
    }
    // And no raw hex survives in the category ramp.
    const categoryBlock = range.slice(range.indexOf("category: ["), range.indexOf("],"));
    expect(categoryBlock).not.toMatch(/#[0-9a-fA-F]{6}"\s*,?\s*$/m);
  });
});
