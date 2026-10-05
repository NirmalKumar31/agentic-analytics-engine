/**
 * Both traces, in one drawer, with a tab per strategy.
 *
 * It replaces two persistent per-strategy evidence buttons. Two buttons
 * implied two destinations and made the reader choose a side before reading
 * anything; one control with two tabs says what it is -- two records of the
 * same question.
 *
 * **Switching tabs issues no request and re-runs nothing.** Both runs have
 * already finished and both payloads are in memory; the tab chooses which
 * finished record is displayed. A drawer that fetched on tab change would
 * be a drawer that could fail, and an evidence view that can fail is not
 * evidence.
 *
 * The panel does not slide horizontally between tabs. A sliding panel
 * implies the two traces are points on a continuum; they are two
 * independent records of two independent runs, and the only thing that
 * moves is the indicator saying which one is shown.
 */

import { useState } from "react";

import { EvidenceBody } from "./EvidenceDrawer";
import { SideSheet } from "./SideSheet";
import type { RunPayload } from "../lib/types";

interface Trace {
  title: string;
  run: RunPayload | null;
}

export function CompareEvidenceDrawer({
  deterministic,
  ai,
  onClose,
}: {
  deterministic: Trace;
  ai: Trace;
  onClose: () => void;
}) {
  const tabs = [deterministic, ai];
  const [active, setActive] = useState(0);
  const current = tabs[active] ?? tabs[0]!;

  return (
    <SideSheet title="Evidence" testId="compare-evidence-drawer" onClose={onClose}>
      <p className="evidence-intro">
        Both traces are here. Switching tabs runs nothing.
      </p>

      <div
        className="evidence-tabs"
        role="tablist"
        aria-label="Which strategy's trace to show"
      >
        {tabs.map((tab, index) => (
          <button
            key={tab.title}
            type="button"
            role="tab"
            id={`evidence-tab-${index}`}
            aria-selected={index === active}
            aria-controls={`evidence-panel-${index}`}
            // Only the selected tab is a tab stop; the arrow keys move
            // between them. That is the tablist pattern, and it is what
            // stops a two-tab control costing a keyboard user two presses
            // to get past.
            tabIndex={index === active ? 0 : -1}
            className={`evidence-tab${index === active ? " is-active" : ""}`}
            onClick={() => setActive(index)}
            onKeyDown={(event) => {
              if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
              event.preventDefault();
              const next =
                event.key === "ArrowRight"
                  ? (active + 1) % tabs.length
                  : (active - 1 + tabs.length) % tabs.length;
              setActive(next);
              document.getElementById(`evidence-tab-${next}`)?.focus();
            }}
          >
            {tab.title}
          </button>
        ))}
      </div>

      <div
        role="tabpanel"
        id={`evidence-panel-${active}`}
        aria-labelledby={`evidence-tab-${active}`}
        data-testid="evidence-panel"
        data-strategy={current.title}
      >
        {!current.run ? (
          <p className="evidence-empty">
            {current.title} produced no run, so there is no trace to show.
          </p>
        ) : current.run.status === "running" ? (
          // A run still in flight has a status and nothing else. Saying so
          // is the honest answer; rendering an evidence record of blanks
          // would imply the engine had recorded nothing.
          <p className="evidence-empty">
            {current.title} has not finished, so its trace is not complete
            yet.
          </p>
        ) : (
          <EvidenceBody run={current.run} />
        )}
      </div>
    </SideSheet>
  );
}
