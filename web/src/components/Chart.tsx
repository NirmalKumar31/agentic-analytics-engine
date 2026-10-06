import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { categoryLabelAngle } from "../lib/axisLabels";
import { hydrateChartSpec } from "../lib/chartHydration";
import { checkChartSpec } from "../lib/chartSafety";
import type { ChartSpec, ResultSnapshot } from "../lib/types";

interface Props {
  chart: ChartSpec;
  snapshot: ResultSnapshot | undefined;
  onOpenProvenance?: (findingId: string) => void;
}

/**
 * Renders one validated Vega-Lite chart.
 *
 * Two steps, in this order. The published specification cites the result it
 * draws rather than carrying a second copy of the rows, so it is first
 * resolved against that snapshot in memory; then the resolved specification
 * is re-checked before it reaches vega-embed, because the browser is where
 * a hostile spec would actually run. Checking only what arrived from the
 * wire would validate something other than what renders.
 */
/** The part of Vega's view API this component uses. */
interface VegaView {
  finalize: () => void;
  width: (value: number) => { run: () => unknown };
}

/**
 * The plot width for a host element, measured now.
 *
 * The 32px is the card's horizontal padding, and the 240 floor keeps a
 * plot legible inside a very narrow column rather than collapsing it.
 */
function plotWidthOf(element: HTMLElement): number {
  return Math.max(240, Math.floor(element.getBoundingClientRect().width) - 32);
}

export function Chart({ chart, snapshot, onOpenProvenance }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [themeVersion, setThemeVersion] = useState(0);
  //: The live Vega view, so a container resize can resize the plot
  //: instead of tearing it down and embedding a new one.
  const viewRef = useRef<VegaView | null>(null);

  /**
   * Keep the plot the width of its container.
   *
   * The previous version stored the measured width in React state and
   * listed it as a dependency of the embed effect. That looked equivalent
   * and was not: the first embed ran with the initial state of `0`, so the
   * specification was built with `Math.max(240, 0 - 32)` and the chart
   * rendered 240px wide inside a 976px card -- the "tiny chart in a large
   * empty card" this redesign exists to fix, reintroduced by the fix for
   * it. Re-embedding on the later state change raced with the first
   * embed's promise and did not reliably win.
   *
   * So the width is measured synchronously where it is used, and a
   * container change resizes the existing view through Vega's own API,
   * which is what the view API is for and cannot race with an embed.
   */
  useLayoutEffect(() => {
    const element = host.current;
    if (!element) return;
    const resize = () => {
      const current = viewRef.current;
      if (!current) return;
      const next = plotWidthOf(element);
      if (next > 0) current.width(next).run();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    const observer = new MutationObserver(() =>
      setThemeVersion((value) => value + 1),
    );
    observer.observe(root, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!host.current) return;
    const hydrated = hydrateChartSpec(chart, snapshot);
    if (!hydrated.ok) {
      setError(hydrated.reason);
      return;
    }
    const columns = snapshot?.columns ?? [];
    const check = checkChartSpec(hydrated.spec, columns);
    if (!check.ok) {
      setError(check.reason ?? "the chart specification was rejected");
      return;
    }

    let disposed = false;
    let view: { finalize: () => void } | null = null;
    const element = host.current;
    const css = getComputedStyle(document.documentElement);
    const token = (name: string, fallback: string) =>
      css.getPropertyValue(name).trim() || fallback;
    const plotWidth = plotWidthOf(element);

    // Flat where the labels fit, angled where they would collide. Vega's
    // own default turns every nominal label to vertical, whatever it says.
    const labelAngle = categoryLabelAngle(hydrated.spec);

    void import("vega-embed")
      .then(({ default: embed }) =>
        // The card renders the title above the plot, so the spec's own title
        // is suppressed rather than drawn twice.
        embed(
          element,
          {
            ...hydrated.spec,
            title: undefined,
            width: plotWidth,
            autosize: { type: "fit", contains: "padding" },
          } as never,
          {
            actions: false,
            // SVG, not canvas: a printed report is the artefact people keep,
            // and a canvas goes onto the page as a screen-resolution raster
            // that print styles cannot reach. The axis labels are pale
            // because the screen is dark; on white paper they have to be
            // restated in ink, and only SVG text can be.
            renderer: "svg",
            config: {
              background: "transparent",
              /*
               * The frame Vega draws around the plot.
               *
               * `view.stroke` defaults to `#ddd`, a light grey chosen for a
               * white page and never revisited. In dark mode it rendered as
               * a near-white rectangle on a #0e1113 canvas -- the brightest
               * thing on the report, outlining the chart like a selection
               * box, in a colour from neither palette. In light mode the
               * same value is almost invisible against #f2f4f1, which is
               * why it survived: the defect only existed in the theme
               * nobody screenshotted.
               *
               * Found by looking at a dark-mode screenshot, confirmed by
               * reading `stroke` off the rendered `path.background`.
               */
              view: { stroke: token("--rule-hairline", "#d9d2c7") },
              axis: {
                labelColor: token("--ink-secondary", "#5d544b"),
                titleColor: token("--ink-secondary", "#5d544b"),
                gridColor: token("--rule-hairline", "#d9d2c7"),
                // Same argument as `view.stroke`: Vega's defaults for these
                // are greys picked for a white page.
                domainColor: token("--rule-hairline", "#d9d2c7"),
                tickColor: token("--rule-hairline", "#d9d2c7"),
              },
              /*
               * `axisX` rather than `axis`, and config rather than the
               * spec: a specification that set its own `axis.labelAngle`
               * -- which the demo warehouse's do -- overrides config and
               * keeps the decision it made.
               */
              ...(labelAngle === null
                ? {}
                : { axisX: { labelAngle, labelOverlap: false } }),
              legend: {
                labelColor: token("--ink-secondary", "#5d544b"),
                titleColor: token("--ink-secondary", "#5d544b"),
              },
              // The default mark colour.
              //
              // `range.category` below only applies where a *colour
              // encoding* exists. A single-series bar or line has none, so
              // every such chart in the product was drawn in Vega's own
              // default `#4c78a8` -- a blue from neither palette, unchanged
              // between light and dark, and never measured for contrast
              // against the plot surface. Observed on a real report, not
              // inferred: the token range was right and simply never
              // reached.
              mark: { color: token("--series-4", "#1d5860") },
              // The series ramp, from tokens, so it follows the theme.
              //
              // Three of these five used to be hardcoded hexes chosen for a
              // palette that no longer exists, and they stayed the same in
              // dark mode. Neither their contrast against the plot surface nor
              // their separation for a colour-blind reader had been measured.
              // tokens.css explains how the five were chosen; the test
              // chartSeries.test.ts re-measures both properties.
              range: {
                category: [
                  token("--series-1", "#cc7682"),
                  token("--series-2", "#7d69b5"),
                  token("--series-3", "#7a5b1d"),
                  token("--series-4", "#1d5860"),
                  token("--series-5", "#244824"),
                ],
              },
            },
          },
        ),
      )
      .then((result) => {
        if (disposed) {
          result.view.finalize();
          return;
        }
        view = result.view;
        viewRef.current = result.view;
        setError(null);
      })
      .catch(() => setError("the chart could not be rendered"));

    return () => {
      disposed = true;
      view?.finalize();
      viewRef.current = null;
      element.replaceChildren();
    };
  }, [chart, snapshot, themeVersion]);

  return (
    <figure className="chart-card" style={{ margin: 0 }}>
      {/*
        A caption, not a heading.

        It was an `<h4>` in a document whose headings run h1, h2 -- a skipped
        level, and a heading for something that is a figure rather than a
        section. `<figcaption>` says what it is and keeps it out of the
        heading outline, where "total revenue by region" was competing with
        "What the numbers show" and "Result" for a reader navigating by
        heading.
      */}
      <figcaption className="chart-title">{chart.title}</figcaption>
      {error ? (
        <div className="notice warn">{error}</div>
      ) : (
        <div
          className="chart-host"
          ref={host}
          role="img"
          aria-label={chart.title}
        />
      )}
      {chart.finding_ids.length > 0 && onOpenProvenance && (
        <div className="row" style={{ marginTop: 8 }}>
          <button
            className="btn ghost small"
            onClick={() => onOpenProvenance(chart.finding_ids[0]!)}
          >
            Inspect evidence
          </button>
        </div>
      )}
    </figure>
  );
}
