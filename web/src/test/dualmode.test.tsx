import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CompareWorkspace } from "../components/CompareWorkspace";
import { ModeSelector } from "../components/ModeSelector";
import type { Capabilities, QueryContract, RunPayload } from "../lib/types";

const BOTH_AVAILABLE: Capabilities = {
  modes: [
    {
      mode: "auto",
      available: true,
      label: "Governed Analysis",
      description: "Rules resolve clear questions before AI planning is considered.",
      reason: "",
      message: "",
    },
    {
      mode: "deterministic",
      available: true,
      label: "Deterministic Analytics",
      description: "Agent decisions come from a scripted provider.",
      reason: "",
      message: "",
    },
    {
      mode: "ai",
      available: true,
      label: "AI Analytics",
      description: "A cloud language model interprets the question.",
      reason: "",
      message: "",
    },
  ],
  compare_available: true,
  ai_limits: {
    runs_per_session: 3,
    max_model_calls_per_run: 24,
    max_runtime_seconds: 180,
  },
};

const AI_DISABLED: Capabilities = {
  modes: [
    BOTH_AVAILABLE.modes[0]!,
    BOTH_AVAILABLE.modes[1]!,
    {
      mode: "ai",
      available: false,
      label: "AI Analytics",
      description: "A cloud language model interprets the question.",
      reason: "ai_disabled",
      message: "AI Analytics is turned off on this deployment.",
    },
  ],
  compare_available: false,
  ai_limits: null,
};

describe("ModeSelector", () => {
  it("offers Governed Analysis plus three audit choices as one accessible radio group", () => {
    render(
      <ModeSelector
        capabilities={BOTH_AVAILABLE}
        value="deterministic"
        onChange={() => {}}
      />,
    );
    const group = screen.getByRole("radiogroup", { name: /analysis mode/i });
    expect(within(group).getAllByRole("radio")).toHaveLength(4);
    expect(screen.getByRole("radio", { name: /^Governed Analysis/ })).toBeEnabled();
    expect(
      screen.getByRole("radio", { name: /^Deterministic Analytics/ }),
    ).toBeChecked();
  });

  it("disables AI and Compare, and says why, when AI is unavailable", () => {
    render(
      <ModeSelector
        capabilities={AI_DISABLED}
        value="deterministic"
        onChange={() => {}}
      />,
    );
    expect(screen.getByRole("radio", { name: /^AI Analytics/ })).toBeDisabled();
    expect(screen.getByRole("radio", { name: /^Compare planning strategies/ })).toBeDisabled();
    // The reason is text, not colour alone -- on both disabled choices.
    expect(screen.getAllByText(/turned off on this deployment/i)).toHaveLength(
      2,
    );
  });

  it("keeps Deterministic selectable when AI is unavailable", () => {
    render(
      <ModeSelector
        capabilities={AI_DISABLED}
        value="deterministic"
        onChange={() => {}}
      />,
    );
    expect(
      screen.getByRole("radio", { name: /^Deterministic Analytics/ }),
    ).toBeEnabled();
  });

  it("reports the chosen mode", async () => {
    const onChange = vi.fn();
    render(
      <ModeSelector
        capabilities={BOTH_AVAILABLE}
        value="deterministic"
        onChange={onChange}
      />,
    );
    await userEvent.click(screen.getByRole("radio", { name: /^Compare planning strategies/ }));
    expect(onChange).toHaveBeenCalledWith("compare");
  });

  it("is reachable by keyboard", async () => {
    render(
      <ModeSelector
        capabilities={BOTH_AVAILABLE}
        value="deterministic"
        onChange={() => {}}
      />,
    );
    await userEvent.tab();
    expect(
      screen.getByRole("radio", { name: /^Deterministic Analytics/ }),
    ).toHaveFocus();
  });

  it("discloses the AI quota and what is sent", () => {
    render(
      <ModeSelector
        capabilities={BOTH_AVAILABLE}
        value="ai"
        onChange={() => {}}
      />,
    );
    expect(screen.getByText(/limited public quota/i)).toBeInTheDocument();
    expect(
      screen.getByText(/schema, profiles and aggregates/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/3 AI runs per dataset session/i),
    ).toBeInTheDocument();
  });

  it("never calls deterministic mode fake or simulated", () => {
    const { container } = render(
      <ModeSelector
        capabilities={BOTH_AVAILABLE}
        value="deterministic"
        onChange={() => {}}
      />,
    );
    const text = container.textContent?.toLowerCase() ?? "";
    expect(text).not.toContain("fake");
    expect(text).not.toContain("simulated");
    expect(text).toContain("deterministic analytics");
  });
});

function side(
  overrides: Partial<Parameters<typeof CompareWorkspace>[0]["ai"]> = {},
) {
  return {
    title: "AI Analytics",
    subtitle: "A cloud model plans and interprets.",
    run: null,
    error: null,
    pending: false,
    children: null,
    ...overrides,
  };
}

describe("CompareWorkspace", () => {
  const deterministic = side({
    title: "Deterministic Analytics",
    children: <p>left result</p>,
  });

  it("shows both panes with independent labels", () => {
    render(
      <CompareWorkspace
        question="Why did margin fall?"
        deterministic={deterministic}
        ai={side({ children: <p>right result</p> })}
      />,
    );
    expect(
      screen.getByRole("region", { name: "Deterministic Analytics" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "AI Analytics" }),
    ).toBeInTheDocument();
    expect(screen.getByText("left result")).toBeInTheDocument();
    expect(screen.getByText("right result")).toBeInTheDocument();
  });

  it("keeps the deterministic result when the AI side failed", () => {
    render(
      <CompareWorkspace
        question="Why did margin fall?"
        deterministic={deterministic}
        ai={side({
          error: "AI Analytics has reached its public demo usage limit.",
        })}
      />,
    );
    expect(screen.getByText("left result")).toBeInTheDocument();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/public demo usage limit/i);
  });

  it("announces each side status politely", () => {
    render(
      <CompareWorkspace
        question="Q"
        deterministic={deterministic}
        ai={side({ pending: true })}
      />,
    );
    const statuses = document.querySelectorAll('[aria-live="polite"]');
    expect(statuses.length).toBeGreaterThanOrEqual(2);
  });

  it("shows AI usage without inventing one for the deterministic side", () => {
    render(
      <CompareWorkspace
        question="Q"
        deterministic={deterministic}
        ai={side({
          usage: {
            input_tokens: 4200,
            output_tokens: 900,
            provider_attempts: 6,
            estimated_cost_microdollars: 1234,
          },
        })}
      />,
    );
    expect(screen.getByText("4,200")).toBeInTheDocument();
    expect(screen.getByText("6")).toBeInTheDocument();
  });

  it("never declares a winner or ranks the two sides", () => {
    const { container } = render(
      <CompareWorkspace
        question="Why did margin fall?"
        deterministic={deterministic}
        ai={side({ children: <p>right result</p> })}
      />,
    );
    const text = container.textContent?.toLowerCase() ?? "";
    for (const banned of [
      "winner",
      "more accurate",
      "better",
      "best",
      "score",
    ]) {
      expect(text).not.toContain(banned);
    }
    expect(text).toContain("are not ranked");
  });

  it("explains that only the planning differs", () => {
    const { container } = render(
      <CompareWorkspace question="Q" deterministic={deterministic} ai={side()} />,
    );
    const text = container.textContent ?? "";
    expect(text).toContain("rule-based planning");
    expect(text).toContain("same analytics engine");
    expect(text).toContain("same publication checks");
  });

  const contract: QueryContract = {
    operation: "average",
    table: "uploaded_data",
    measure: "annual_revenue",
    dimension: "region",
    dimensions: ["region"],
    filters: [{ column: "age", operator: ">=", value: 30 }],
    ascending: false,
    confident: true,
    explanation: "validated",
    interpretation: "rule-based",
    contract_hash: "same-hash",
  };

  const runWith = (queryContract: QueryContract): RunPayload =>
    ({ query_contract: queryContract }) as RunPayload;

  it("confirms when both panes execute the same canonical contract", () => {
    render(
      <CompareWorkspace
        question="Q"
        deterministic={side({ run: runWith(contract) })}
        ai={side({
          run: runWith({ ...contract, interpretation: "ai-grounded" }),
        })}
      />,
    );
    expect(screen.getByTestId("contract-comparison")).toHaveTextContent(
      /both modes agreed/i,
    );
  });

  it("shows one shared result instead of rendering it twice", () => {
    // Two identical tables and two identical charts read as two independent
    // confirmations, and they pushed the planning lanes -- the part that
    // actually differed -- off the top of the screen.
    render(
      <CompareWorkspace
        question="Q"
        deterministic={side({
          title: "Deterministic Analytics",
          run: runWith(contract),
          children: <p>the result</p>,
        })}
        ai={side({
          run: runWith({ ...contract, interpretation: "ai-grounded" }),
          children: <p>the result</p>,
        })}
      />,
    );
    expect(screen.getByTestId("shared-result")).toBeInTheDocument();
    expect(screen.getAllByText("the result")).toHaveLength(1);

    // No side-by-side panes: that is what "shown once" means, and it is
    // the claim `pane-status.length === 0` used to stand for. The status
    // itself has not gone anywhere -- it is a column in the table that
    // compares the two strategies, where both sides now read "Complete",
    // which is more informative than two panes that are not rendered.
    expect(document.querySelectorAll(".compare-pane")).toHaveLength(0);
    expect(screen.getAllByTestId("pane-status")).toHaveLength(2);

    // The planning comparison stays, because that is what differed. It was
    // two `ExecutionLane` regions restating the same five stages twice;
    // it is one table with a row per strategy.
    const routes = screen.getByTestId("compare-routes");
    expect(
      within(routes).getByRole("rowheader", { name: "Deterministic Analytics" }),
    ).toBeInTheDocument();
    expect(
      within(routes).getByRole("rowheader", { name: "AI Analytics" }),
    ).toBeInTheDocument();
  });

  it("never collapses a disagreement into one result", () => {
    render(
      <CompareWorkspace
        question="Q"
        deterministic={side({
          title: "Deterministic Analytics",
          run: runWith(contract),
          children: <p>left result</p>,
        })}
        ai={side({
          run: runWith({
            ...contract,
            dimensions: ["business_type"],
            canonical_contract: { ...contract, dimensions: ["business_type"] },
          }),
          children: <p>right result</p>,
        })}
      />,
    );
    expect(screen.queryByTestId("shared-result")).not.toBeInTheDocument();
    expect(screen.getByText("left result")).toBeInTheDocument();
    expect(screen.getByText("right result")).toBeInTheDocument();
  });

  it("keeps each mode's cost visible when the result is shared", () => {
    const usage = {
      provider_attempts: 0,
      input_tokens: 0,
      output_tokens: 0,
    } as never;
    render(
      <CompareWorkspace
        question="Q"
        deterministic={side({
          title: "Deterministic Analytics",
          run: runWith(contract),
          usage,
          children: <p>the result</p>,
        })}
        ai={side({
          run: runWith({ ...contract, interpretation: "ai-grounded" }),
          usage: {
            provider_attempts: 1,
            input_tokens: 1200,
            output_tokens: 90,
          } as never,
          children: <p>the result</p>,
        })}
      />,
    );
    const shared = screen.getByTestId("shared-result");
    expect(shared).toHaveTextContent("Deterministic Analytics");
    expect(shared).toHaveTextContent("AI Analytics");
    expect(shared).toHaveTextContent("1,200");
  });

  it("warns instead of presenting unlike contracts as equivalent", () => {
    render(
      <CompareWorkspace
        question="Q"
        deterministic={side({ run: runWith(contract) })}
        ai={side({
          run: runWith({
            ...contract,
            dimensions: ["business_type"],
            canonical_contract: { ...contract, dimensions: ["business_type"] },
          }),
        })}
      />,
    );
    expect(screen.getByTestId("contract-comparison")).toHaveTextContent(
      /different governed interpretations/i,
    );
  });
});
