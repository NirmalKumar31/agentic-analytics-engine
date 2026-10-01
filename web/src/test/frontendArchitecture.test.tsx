import { readFileSync } from "node:fs";
import { join } from "node:path";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PlanningMethodDisclosure } from "../components/PlanningMethodDisclosure";
import { SessionControls } from "../components/SessionControls";
import { TerminalState } from "../components/TerminalState";
import { WorkflowIndex } from "../components/WorkflowIndex";

const sourceRoot = join(__dirname, "..");
const namedComponents = [
  "AppShell",
  "ProductHeader",
  "DatasetIdentity",
  "WorkflowIndex",
  "DatasetOnboarding",
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
      "DatasetOnboarding",
      "SchemaInspector",
      "QuestionComposer",
      "RunProgress",
      "ReportWorkspace",
    ]) {
      expect(app).toContain(boundary);
    }
  });

  it("preserves the derived workflow states", () => {
    render(<WorkflowIndex phase="executing" />);
    expect(screen.getByText("Dataset").closest(".step")).toHaveAttribute(
      "data-state",
      "done",
    );
    expect(screen.getByText("Analyse").closest(".step")).toHaveAttribute(
      "data-state",
      "active",
    );
    expect(screen.getByText("Report").closest(".step")).toHaveAttribute(
      "data-state",
      "idle",
    );
  });

  it("can actually reach the Verify stage", () => {
    // The index rendered a Verify step that no stage value could make
    // active: it went idle -> done, so the interface named a stage it never
    // showed the reader being in. The engine has a verifying phase and
    // reports it, so the index now reads that.
    render(<WorkflowIndex phase="verifying" />);
    expect(screen.getByText("Verify").closest(".step")).toHaveAttribute(
      "data-state",
      "active",
    );
    expect(screen.getByText("Analyse").closest(".step")).toHaveAttribute(
      "data-state",
      "done",
    );
  });

  it("marks the stage a stopped run stopped at", () => {
    // Showing earlier stages done and the rest idle reads as a run still
    // in progress. A refusal happens while the question is mapped onto the
    // dataset, so Analyse is where it stopped -- and nothing was verified.
    render(<WorkflowIndex phase="refused" />);
    expect(screen.getByText("Analyse").closest(".step")).toHaveAttribute(
      "data-state",
      "stopped",
    );
    expect(screen.getByText("Verify").closest(".step")).toHaveAttribute(
      "data-state",
      "idle",
    );
    expect(screen.getByText("Report").closest(".step")).toHaveAttribute(
      "data-state",
      "idle",
    );
  });

  it("keeps terminal reasons visible and planning policy explicit", () => {
    render(
      <>
        <TerminalState
          configError={null}
          error="The analysis failed safely."
          stoppedReason="quota reached"
        />
        <PlanningMethodDisclosure mode="auto" />
      </>,
    );
    expect(screen.getByText("The analysis failed safely.")).toBeVisible();
    expect(screen.getByText(/quota reached/)).toBeVisible();
    expect(screen.getByText(/resolved by rules/)).toBeVisible();
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
