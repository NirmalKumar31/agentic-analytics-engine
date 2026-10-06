/**
 * Production evidence: an AI run with status `refused` wore a COMPLETE
 * badge and rendered an empty pane, beside a deterministic answer that had
 * worked. Two components each derived the state for themselves and both
 * were wrong -- one read "a run object exists" as success, the other
 * rendered a report only for the literal string `completed`.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CompareWorkspace } from "../components/CompareWorkspace";
import { runState } from "../lib/runState";
import type { Finding, RunPayload } from "../lib/types";

const finding = { finding_id: "f1", text: "An answer." } as Finding;

const run = (over: Partial<RunPayload> = {}): RunPayload =>
  ({
    question: "q",
    findings: [],
    rejected: [],
    charts: [],
    tasks: [],
    results: {},
    mcp_trace: [],
    events: [],
    stopped_reason: "",
    ...over,
  }) as RunPayload;

describe("runState", () => {
  it("does not call a refused run complete", () => {
    const state = runState(
      run({ status: "refused", stopped_reason: "no date column" }),
    );
    expect(state.state).toBe("refused");
    expect(state.label).toBe("Refused");
    expect(state.label).not.toBe("Complete");
    expect(state.reason).toBe("no date column");
    // A refusal still has a report worth rendering.
    expect(state.showsReport).toBe(true);
  });

  it("separates a verified answer from a run that published nothing", () => {
    expect(
      runState(run({ status: "completed", findings: [finding] })).state,
    ).toBe("completed_verified");
    expect(runState(run({ status: "completed", findings: [] })).state).not.toBe(
      "completed_verified",
    );
  });

  it("separates a withheld run from one that found nothing to claim", () => {
    // Both published no findings, and conflating them blamed a verifier
    // that never ran. `rejected` is the difference: verification looked at
    // a claim and declined it, versus the analysis producing no claim at
    // all. A reader told "withheld" can go and read what was withheld.
    const withheld = runState(
      run({
        status: "completed",
        findings: [],
        rejected: [
          {
            finding_id: "f1",
            status: "unsupported" as const,
            reason: "not supported by the cited cells",
          },
        ],
      }),
    );
    expect(withheld.state).toBe("verification_withheld");
    expect(withheld.label).not.toBe("Complete");

    const nothing = runState(run({ status: "completed", findings: [], rejected: [] }));
    expect(nothing.state).toBe("no_findings");
    expect(nothing.label).not.toBe("Complete");

    expect(withheld.state).not.toBe(nothing.state);
    expect(withheld.label).not.toBe(nothing.label);
  });

  it.each(["failed", "timeout"])(
    "reports %s as an execution failure",
    (status) => {
      expect(runState(run({ status })).state).toBe("execution_failed");
    },
  );

  it("calls a budget stop a quota limit rather than an execution failure", () => {
    const state = runState(run({ status: "budget_exhausted" }));
    expect(state.state).toBe("quota_stopped");
    expect(state.label).toBe("Quota limit reached");
  });

  it("keeps cancelled, running and unavailable distinct", () => {
    expect(runState(run({ status: "cancelled" })).state).toBe("cancelled");
    expect(runState(run({ status: "running" })).state).toBe("running");
    expect(runState(null, { pending: true }).state).toBe("running");
    expect(runState(null, { unavailable: true }).state).toBe("unavailable");
    expect(runState(null).state).toBe("not_started");
  });

  it("treats an unavailable mode as unavailable, not as a failure", () => {
    const state = runState(null, {
      unavailable: true,
      error: "AI Analytics is off on this deployment",
    });
    expect(state.state).toBe("unavailable");
    expect(state.label).toBe("Unavailable");
  });
});

describe("Compare Both terminal states", () => {
  const side = (over: Record<string, unknown> = {}) => ({
    title: "T",
    subtitle: "S",
    run: null as RunPayload | null,
    error: null as string | null,
    pending: false,
    children: null,
    ...over,
  });

  const paneStates = () =>
    screen
      .getAllByTestId("pane-status")
      .map((n) => n.getAttribute("data-state"));

  /*
   * The full reports are behind a strategy switcher now -- one at a time,
   * at full width, rather than two in half a laptop's width each. So a
   * state card that belongs to a strategy is read by selecting it.
   *
   * `pane-status` is unaffected: the per-strategy status sits in the
   * verdict block above the switcher, where both are always visible. The
   * card is the *explanation*, and it lives with the report it explains.
   */
  const strategy = (role: "deterministic" | "ai") => {
    fireEvent.click(screen.getByTestId(`compare-tab-${role}`));
    return screen.getByTestId("compare-report-panel");
  };

  it("deterministic completes and AI refuses", () => {
    render(
      <CompareWorkspace
        question="q"
        deterministic={side({
          run: run({ status: "completed", findings: [finding] }),
        })}
        ai={side({
          run: run({
            status: "refused",
            stopped_reason: "could not be mapped",
          }),
          children: <p>refusal report</p>,
        })}
      />,
    );
    expect(paneStates()).toEqual(["completed_verified", "refused"]);
    // The reason is on screen, not only in the activity log -- and it is
    // attributed to the strategy that refused.
    expect(strategy("ai")).toHaveTextContent(/could not be mapped/i);
    expect(screen.queryByTestId("pane-placeholder")).toBeNull();
  });

  it("deterministic refuses and AI completes", () => {
    render(
      <CompareWorkspace
        question="q"
        deterministic={side({ run: run({ status: "refused" }) })}
        ai={side({ run: run({ status: "completed", findings: [finding] }) })}
      />,
    );
    expect(paneStates()).toEqual(["refused", "completed_verified"]);
  });

  it("one side fails", () => {
    render(
      <CompareWorkspace
        question="q"
        deterministic={side({
          run: run({ status: "completed", findings: [finding] }),
        })}
        ai={side({ error: "the analysis could not be started" })}
      />,
    );
    expect(paneStates()).toEqual(["completed_verified", "execution_failed"]);
    expect(strategy("ai")).toHaveTextContent(/could not be started/i);
  });

  it("both refuse", () => {
    render(
      <CompareWorkspace
        question="q"
        deterministic={side({ run: run({ status: "refused" }) })}
        ai={side({ run: run({ status: "refused" }) })}
      />,
    );
    expect(paneStates()).toEqual(["refused", "refused"]);
    // One card on screen at a time, and a card on each strategy: neither
    // refusal is dropped because the other one happened too.
    for (const role of ["deterministic", "ai"] as const) {
      expect(
        within(strategy(role)).getByTestId("run-state-card"),
        `the ${role} refusal has no card of its own`,
      ).toBeInTheDocument();
    }
  });

  it("verification publishes zero findings", () => {
    render(
      <CompareWorkspace
        question="q"
        deterministic={side({
          run: run({ status: "completed", findings: [] }),
        })}
        ai={side({ run: run({ status: "completed", findings: [] }) })}
      />,
    );
    // Neither side withheld anything, so both found nothing to claim.
    expect(paneStates()).toEqual(["no_findings", "no_findings"]);
    // The copy must say nothing was withheld, not blame a verifier.
    expect(strategy("deterministic")).toHaveTextContent(
      /found nothing it could claim/i,
    );
    expect(screen.getAllByTestId("run-state-card")[0]).not.toHaveTextContent(
      /withheld findings below/i,
    );
  });

  it("both complete, and then says nothing extra", () => {
    render(
      <CompareWorkspace
        question="q"
        deterministic={side({
          run: run({ status: "completed", findings: [finding] }),
        })}
        ai={side({ run: run({ status: "completed", findings: [finding] }) })}
      />,
    );
    expect(paneStates()).toEqual(["completed_verified", "completed_verified"]);
    expect(screen.queryByTestId("run-state-card")).toBeNull();
  });

  it("never labels a non-completed run Complete", () => {
    for (const status of [
      "refused",
      "failed",
      "timeout",
      "budget_exhausted",
      "cancelled",
    ]) {
      const { container, unmount } = render(
        <CompareWorkspace
          question="q"
          deterministic={side({ run: run({ status }) })}
          ai={side({ run: run({ status }) })}
        />,
      );
      expect(container.textContent).not.toMatch(/\bComplete\b/);
      unmount();
    }
  });
});

describe("whether a pane renders a report", () => {
  it("renders one for every state that has a run, including a refusal", () => {
    // `App` asks `runState` this rather than testing the status string
    // itself. Rendering the AI report only for `status === "completed"` is
    // what left a refused pane blank in production, and the decision was
    // in a component no test renders.
    for (const status of [
      "completed",
      "refused",
      "failed",
      "timeout",
      "budget_exhausted",
      "cancelled",
    ]) {
      expect(runState(run({ status })).showsReport).toBe(true);
    }
  });

  it("renders none when there is nothing to render", () => {
    expect(runState(null).showsReport).toBe(false);
    expect(runState(null, { pending: true }).showsReport).toBe(false);
    expect(runState(run({ status: "running" })).showsReport).toBe(false);
  });
});
