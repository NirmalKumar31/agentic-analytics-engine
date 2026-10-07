/**
 * Every colour that carries words, measured against every surface it can
 * land on, in both themes.
 *
 * The palette's contrast was documented and not enforced. `tokens.css`
 * records the measurements in a comment, and axe checks whatever the browser
 * suite happens to render, which is six states out of a much larger set.
 * A token that fails on a surface no scanned state uses passes both.
 *
 * That gap has cost real time. `--ink-muted` took three attempts: `#79828a`
 * failed all three surfaces at 3.79, `#6b747b` cleared paper at 4.62 and
 * failed the canvas at 4.16, and only a script that checked every colour
 * against every surface settled it. Three of PR F's five accessibility
 * defects were the same mistake: a colour that cleared the surface it was
 * checked against and failed a darker one.
 *
 * So this computes the ratios rather than trusting the comment. It is the
 * cheap half of the discipline: a token failing here is caught before it is
 * ever rendered, while axe stays the check on what a real page composites.
 * Neither replaces the other: axe sees `opacity` and overlap, this sees
 * combinations no test renders.
 *
 * The thresholds are WCAG 2.2: 4.5:1 for body text (1.4.3) and 3:1 for user
 * interface components and graphical objects (1.4.11). Asserting them is not
 * a conformance claim; it is one requirement, checked.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { modules } from "./stylesheet";

const TOKENS = readFileSync(
  join(__dirname, "..", "styles", "tokens.css"),
  "utf8",
);

// ----------------------------------------------------------------- parsing

/** Strip comments, so a hex quoted in prose is not read as a declaration. */
function withoutComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/**
 * The declarations inside one brace-matched block, starting at `from`.
 *
 * Brace matching rather than a line range: the dark theme lives inside a
 * media query, and a regex over the whole file would mix the two palettes
 * into one and measure colours against surfaces they never share.
 */
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
  throw new Error("unterminated block in tokens.css");
}

function hexTokens(block: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const match of block.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{3,8})\s*;/g)) {
    out[match[1]!] = match[2]!;
  }
  return out;
}

/** `--a: var(--b);` aliases, which a palette uses to say "the same colour". */
function aliasTokens(block: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const match of block.matchAll(/--([\w-]+):\s*var\(--([\w-]+)\)\s*;/g)) {
    out[match[1]!] = match[2]!;
  }
  return out;
}

/**
 * Follow aliases to the colour they end at.
 *
 * A palette is allowed to say "this token is that token"; the dark theme
 * does exactly that, because the text-safe variants and the vivid colours
 * are the same colour there. Measuring the alias rather than its target
 * would report the token as missing and skip it, which is the failure mode
 * this whole file exists to prevent.
 */
function resolve(
  hexes: Record<string, string>,
  aliases: Record<string, string>,
): Record<string, string> {
  const out = { ...hexes };
  for (const [name, target] of Object.entries(aliases)) {
    let at: string | undefined = target;
    for (let hops = 0; at && hops < 8; hops += 1) {
      if (out[at]) {
        out[name] = out[at]!;
        break;
      }
      at = aliases[at];
    }
  }
  return out;
}

const clean = withoutComments(TOKENS);

/** Light: the first `:root` block. */
const LIGHT_BLOCK = blockAt(clean, clean.indexOf(":root"));
const LIGHT = resolve(hexTokens(LIGHT_BLOCK), aliasTokens(LIGHT_BLOCK));

/**
 * Dark: the overrides, composed onto the light palette.
 *
 * The dark block redefines a subset. Everything it does not mention keeps
 * its `:root` value, because that is how the cascade works, so the palette
 * a reader in dark mode actually gets is light overlaid with the overrides.
 * Reading the block alone reports the rest as absent, which hides exactly
 * the failure this is looking for: a colour tuned for a light surface that
 * nobody remembered to redefine, still sitting there on a dark one.
 *
 * **Hexes and aliases compose differently, and conflating them is a bug.**
 *
 * A hex the dark block omits really does inherit the light value, and that
 * is the defect this file was written to catch: light-surface ochre left
 * sitting on a near-black panel.
 *
 * An *alias* omitted by the dark block does not. `--action: var(--signal)`
 * declared on `:root` is a substitution performed where the token is used,
 * against whatever `--signal` holds on the element then. Under
 * `[data-theme="dark"]` that is the dark signal, so the alias follows the
 * override without being restated, and restating it is how a palette
 * drifts, because a later edit that forgets one alias leaves a single
 * component wearing the old hue.
 *
 * Composing the light block's aliases onto dark's values models that. The
 * earlier version resolved dark using only the aliases the dark block
 * itself declared, so every alias held its *light* target. It reported the
 * light signal green as dark mode's `--action` and failed it at 2.72:1
 * against a surface no reader will ever see it on.
 */
const DARK_BLOCK = blockAt(clean, clean.indexOf(':root[data-theme="dark"]'));
const DARK_HEXES = { ...hexTokens(LIGHT_BLOCK), ...hexTokens(DARK_BLOCK) };
const DARK_ALIASES = { ...aliasTokens(LIGHT_BLOCK), ...aliasTokens(DARK_BLOCK) };
// A token the dark block pins to a literal colour is no longer an alias
// there, whatever `:root` said. Without this, the inherited alias would be
// applied after the hex and quietly win.
for (const name of Object.keys(hexTokens(DARK_BLOCK))) {
  if (!(name in aliasTokens(DARK_BLOCK))) delete DARK_ALIASES[name];
}
const DARK = resolve(DARK_HEXES, DARK_ALIASES);

// ------------------------------------------------------------ contrast maths

function channel(value: number): number {
  const c = value / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function luminance(hex: string): number {
  let value = hex.replace("#", "");
  if (value.length === 3) {
    value = value
      .split("")
      .map((c) => c + c)
      .join("");
  }
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}

// --------------------------------------------------------------- the claims

/** Every surface a token can land on. A colour has to clear the worst one. */
const SURFACES = [
  "surface-canvas",
  "surface-paper",
  "surface-raised",
  "surface-inset",
] as const;

/** Tokens used for words. WCAG 1.4.3 asks 4.5:1. */
const TEXT_TOKENS = [
  "ink-primary",
  "ink-secondary",
  "ink-muted",
  "action-text",
  "warning-text",
  "supported",
  "failure",
  "signal",
];

/**
 * Tokens used for rings, borders and chart marks: things a reader has to
 * perceive to use the interface. WCAG 1.4.11 asks 3:1. These deliberately do
 * *not* have to clear the text threshold: the vivid `--action` is right for
 * a focus ring and wrong for a sentence, which is why the text-safe variants
 * exist beside it.
 *
 * `--rule-hairline` and `--rule-strong` are deliberately absent. They divide
 * rows and regions and carry no state: the grouping they express is also
 * expressed by position and spacing, so they are decorative under 1.4.11 and
 * exempt. Measured, they sit at 1.26-2.85:1. Holding a divider to the
 * component threshold would mean darkening every hairline in the interface
 * to satisfy a rule that does not apply to it, and the honest reason they
 * are listed here at all is so that nobody re-adds them believing they were
 * overlooked.
 */
const UI_TOKENS = ["action", "warning"];

const THEMES: Array<[string, Record<string, string>]> = [
  ["light", LIGHT],
  ["dark", DARK],
];

describe("the palette parses", () => {
  it.each(THEMES)("%s defines every surface", (_name, palette) => {
    for (const surface of SURFACES) {
      expect(palette[surface], `${surface} is missing`).toMatch(/^#[0-9a-fA-F]{3,8}$/);
    }
  });

  it.each(THEMES)("%s defines every colour that carries words", (_name, palette) => {
    for (const token of TEXT_TOKENS) {
      expect(palette[token], `${token} is missing`).toMatch(/^#[0-9a-fA-F]{3,8}$/);
    }
  });

  it("reads the two themes as different palettes", () => {
    // A parser that mixed them would measure dark ink against a light
    // surface and pass everything.
    expect(LIGHT["surface-canvas"]).not.toBe(DARK["surface-canvas"]);
    expect(LIGHT["ink-primary"]).not.toBe(DARK["ink-primary"]);
    // The dark block must actually restate a substantial palette, not a
    // token or two: a near-empty override block is the failure that leaves
    // light-surface colours on dark surfaces.
    expect(Object.keys(hexTokens(DARK_BLOCK)).length).toBeGreaterThan(10);
  });
});

describe("text clears 4.5:1 on every surface it can land on", () => {
  for (const [theme, palette] of THEMES) {
    for (const token of TEXT_TOKENS) {
      for (const surface of SURFACES) {
        it(`${theme}: --${token} on --${surface}`, () => {
          const ratio = contrast(palette[token]!, palette[surface]!);
          expect(
            Number(ratio.toFixed(2)),
            `--${token} (${palette[token]}) on --${surface} (${palette[surface]})`,
          ).toBeGreaterThanOrEqual(4.5);
        });
      }
    }
  }
});

describe("interface colours clear 3:1 on every surface", () => {
  for (const [theme, palette] of THEMES) {
    for (const token of UI_TOKENS) {
      for (const surface of SURFACES) {
        it(`${theme}: --${token} on --${surface}`, () => {
          const ratio = contrast(palette[token]!, palette[surface]!);
          expect(
            Number(ratio.toFixed(2)),
            `--${token} (${palette[token]}) on --${surface} (${palette[surface]})`,
          ).toBeGreaterThanOrEqual(3);
        });
      }
    }
  }
});

/**
 * Foreground-on-fill pairs, which the surface matrix cannot see.
 *
 * Every test above measures a text colour against one of the four
 * *surfaces*. A filled control is neither: `.btn.primary` paints
 * `--signal` and sets a foreground on top of it, and that pair appears in
 * no surface combination.
 *
 * It went wrong exactly there. `.btn.primary` carried `color: #1a1000`, a
 * near-black left from the palette where `--signal` was a light orange.
 * Against the mineral green it fell below 3:1, and axe failed three
 * browser states on it, while all 87 assertions here passed, because
 * none of them was looking at that pair.
 *
 * Each entry is (foreground token, fill token). Adding a filled control
 * means adding its pair here.
 */
const FILL_PAIRS: Array<[string, string]> = [
  ["ink-inverse", "signal"],
  ["ink-inverse", "signal-strong"],
  ["ink-inverse", "warning"],
  ["signal", "signal-weak"],
  ["warning-text", "warning-weak"],
  ["ink-primary", "surface-inset"],
];

describe("text on a filled control clears 4.5:1", () => {
  for (const [theme, palette] of THEMES) {
    for (const [fg, fill] of FILL_PAIRS) {
      it(`${theme}: --${fg} on --${fill}`, () => {
        const ratio = contrast(palette[fg]!, palette[fill]!);
        expect(
          Number(ratio.toFixed(2)),
          `--${fg} (${palette[fg]}) on --${fill} (${palette[fill]})`,
        ).toBeGreaterThanOrEqual(4.5);
      });
    }
  }
});

/**
 * Every (background, colour) pair the stylesheet actually declares.
 *
 * `FILL_PAIRS` above is a hand-kept list, and a hand-kept list is exactly
 * what missed `.btn.primary` in the first place. This derives the pairs
 * from the CSS instead: any rule that sets both `background: var(--x)` and
 * `color: var(--y)` is a filled element with a foreground on it, and both
 * themes are measured. Adding a new filled control covers itself.
 *
 * Only token-to-token pairs are checked. A literal hex in a rule is a
 * separate problem and `cssArchitecture.test.ts` is where it is caught.
 */
function declaredFillPairs(): Array<[string, string, string]> {
  const out: Array<[string, string, string]> = [];
  const seen = new Set<string>();
  for (const [module, source] of modules()) {
    const css = withoutComments(source);
    // Brace-matched rule bodies, with the selector that introduced them.
    for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const selector = match[1]!.trim().replace(/\s+/g, " ");
      const body = match[2]!;
      const bg = /background(?:-color)?:\s*var\(\s*--([\w-]+)\s*\)/.exec(body);
      const fg = /(?<!-)color:\s*var\(\s*--([\w-]+)\s*\)/.exec(body);
      if (!bg || !fg) continue;
      const key = `${fg[1]}|${bg[1]}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push([fg[1]!, bg[1]!, `${module} ${selector}`]);
    }
  }
  return out;
}

describe("every filled element declared in the stylesheet", () => {
  const pairs = declaredFillPairs();

  it("finds pairs to measure", () => {
    // A derivation that silently matched nothing would pass every
    // assertion below by having none to make.
    expect(pairs.length).toBeGreaterThan(4);
  });

  for (const [theme, palette] of THEMES) {
    for (const [fg, bg, where] of pairs) {
      it(`${theme}: --${fg} on --${bg} (${where})`, () => {
        const foreground = palette[fg];
        const background = palette[bg];
        // A pair naming a token the palette does not define is itself a
        // defect: the browser drops the declaration and the element
        // inherits something nobody chose.
        expect(foreground, `--${fg} is not defined`).toBeTruthy();
        expect(background, `--${bg} is not defined`).toBeTruthy();

        const ratio = contrast(foreground!, background!);
        expect(
          Number(ratio.toFixed(2)),
          `${where}: --${fg} (${foreground}) on --${bg} (${background})`,
        ).toBeGreaterThanOrEqual(4.5);
      });
    }
  }
});

describe("the measurement itself", () => {
  it("computes the ratios WCAG defines", () => {
    // Anchors, so a broken luminance function cannot quietly pass the suite
    // above by returning something plausible for everything.
    expect(Number(contrast("#000000", "#ffffff").toFixed(2))).toBe(21);
    expect(Number(contrast("#ffffff", "#ffffff").toFixed(2))).toBe(1);
    expect(Number(contrast("#767676", "#ffffff").toFixed(2))).toBe(4.54);
  });

  it("is symmetric", () => {
    expect(contrast("#12161a", "#eef0ed")).toBeCloseTo(
      contrast("#eef0ed", "#12161a"),
      10,
    );
  });
});
