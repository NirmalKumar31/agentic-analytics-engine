import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PlanningMethodDisclosure } from "../components/PlanningMethodDisclosure";
import { SessionControls } from "../components/SessionControls";
import { RunStateCard } from "../components/RunStateCard";
import { TerminalState } from "../components/TerminalState";
import { runState } from "../lib/runState";
import type { RunPayload } from "../lib/types";

const sourceRoot = join(__dirname, "..");
const namedComponents = [
  "AppShell",
  "ProductHeader",
  "DatasetContextBar",
  "LandingView",
  "SchemaInspector",
  "QuestionComposer",
  "PlanningMethodDisclosure",
  "RunProgress",
  "ReportWorkspace",
  "TechnicalInspector",
  "TerminalState",
  "SessionControls",
];

describe("frontend component architecture", () => {
  it("keeps App as an orchestrator and owns every named boundary in a component", () => {
    const app = readFileSync(join(sourceRoot, "App.tsx"), "utf8");
    expect(app.split("\n").length).toBeLessThan(400);
    for (const component of namedComponents) {
      const source = readFileSync(
        join(sourceRoot, "components", `${component}.tsx`),
        "utf8",
      );
      expect(source).toContain(`function ${component}`);
    }
    for (const boundary of [
      "AppShell",
      "ProductHeader",
      "LandingView",
      "SchemaInspector",
      "QuestionComposer",
      "RunProgress",
      "ReportWorkspace",
    ]) {
      expect(app).toContain(boundary);
    }
  });

  /*
   * The three stepper tests that stood here are deleted with the stepper.
   *
   * They asserted that `WorkflowIndex` mapped a phase onto a step state --
   * that `executing` showed Analyse active, that `verifying` was reachable
   * at all, and that a refusal showed Analyse **stopped** rather than
   * leaving the later steps idle, which reads as a run still in progress.
   *
   * The component is gone: a five-step pipeline diagram resident above every
   * screen is the thing the redesign is removing, and the run's progress now
   * belongs to the active-run timeline.
   *
   * The last of those claims is the one that mattered and it is **owed by
   * step D**: a stopped run must be shown as stopped at the point it
   * stopped, never as still running. The timeline must assert it against
   * real backend events rather than a derived phase string.
   */

  it("keeps terminal reasons visible and planning policy explicit", () => {
    // The stop reason moved out of TerminalState and into RunStateCard,
    // which names the state as well as the reason: "Quota limit reached."
    // rather than "The run stopped early: ...". TerminalState kept the two
    // problems that have no run behind them. The claim is unchanged: a
    // reader still sees why, so both halves are asserted here rather than
    // one of them being dropped with the prop.
    render(
      <>
        <TerminalState configError={null} error="The analysis failed safely." />
        <RunStateCard
          state={runState({
            status: "budget_exhausted",
            stopped_reason: "quota reached",
            findings: [],
            rejected: [],
          } as unknown as RunPayload)}
        />
        <PlanningMethodDisclosure mode="deterministic" />
      </>,
    );
    expect(screen.getByText("The analysis failed safely.")).toBeVisible();
    expect(screen.getByText(/quota reached/)).toBeVisible();
    expect(screen.getByTestId("run-state-card")).toHaveTextContent(/quota/i);
    // Deterministic, not auto. The `auto` notice is gone: it restated the
    // server's own description of Governed Analysis in different words,
    // directly underneath it. The deterministic one stays because it makes
    // a claim nothing else on the screen makes: that the *interpretation*
    // is scripted in this public demo while the SQL, statistics, tool
    // execution, verification and provenance are real.
    expect(screen.getByText(/interpretation is rule-based/)).toBeVisible();
  });

  it("does not say the stop reason twice", () => {
    // A refused run had its reason in the presentation headline, again in
    // the Notes section beneath it, and a third time as "The run stopped
    // early: ...". Three phrasings of one sentence read as three problems.
    render(
      <RunStateCard
        state={runState({
          status: "refused",
          stopped_reason: "the question could not be mapped safely",
          findings: [],
          rejected: [],
        } as unknown as RunPayload)}
      />,
    );
    expect(
      screen.getAllByText(/could not be mapped safely/),
    ).toHaveLength(1);
    expect(screen.queryByText(/stopped early/i)).toBeNull();
  });

  it("keeps both session exits wired to explicit intent callbacks", async () => {
    const end = vi.fn();
    const reset = vi.fn();
    render(
      <SessionControls
        hasSession
        hasRun
        onEndSession={end}
        onReset={reset}
      />,
    );
    screen.getByRole("button", { name: "End session" }).click();
    screen.getByRole("button", { name: "Start over" }).click();
    expect(end).toHaveBeenCalledOnce();
    expect(reset).toHaveBeenCalledOnce();
  });
});
