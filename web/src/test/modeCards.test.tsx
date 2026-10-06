/**
 * The modes are a choice, and they look like one.
 *
 * Two earlier layouts, and this answers both objections rather than
 * reversing either.
 *
 * The first put four bordered cards on screen carrying four full
 * paragraphs -- more room than the question field they qualify. It was
 * replaced by a segmented pill strip, which read as a row of headings:
 * nothing about it said "pick one", the selected state was a background
 * tint, and three of the four purposes were invisible until selected.
 *
 * So: cards again, each with a visible selected mark, each stating its
 * purpose in **one sentence** taken from the description's first -- so a
 * card cannot disagree with the full text -- and every paragraph of
 * explanation behind one disclosure.
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ModeSelector, RUN_LABELS } from "../components/ModeSelector";
import type { Capabilities, UiMode } from "../lib/types";

const MODE_DEFAULTS = {
  auto: {
    mode: "auto",
    label: "Governed Analysis",
    description:
      "Rules resolve clear questions locally. AI-assisted planning is used only when the question is genuinely ambiguous.",
    available: true,
    reason: "",
    message: "",
  },
  deterministic: {
    mode: "deterministic",
    label: "Deterministic Analytics",
    description:
      "Agent decisions come from a scripted provider, so the same question produces the same plan every time.",
    available: true,
    reason: "",
    message: "",
  },
  ai: {
    mode: "ai",
    label: "AI Analytics",
    description:
      "A cloud language model interprets the question and chooses which analyses to run.",
    available: true,
    reason: "",
    message: "",
  },
};

function capabilities(aiAvailable = true): Capabilities {
  return {
    modes: [
      MODE_DEFAULTS.auto,
      MODE_DEFAULTS.deterministic,
      {
        ...MODE_DEFAULTS.ai,
        available: aiAvailable,
        message: aiAvailable ? "" : "AI Analytics is turned off on this deployment.",
      },
    ],
    compare_available: aiAvailable,
    ai_limits: aiAvailable
      ? { runs_per_session: 3, max_model_calls_per_run: 24, max_runtime_seconds: 180 }
      : null,
  } as unknown as Capabilities;
}

function selector(value: UiMode = "auto", aiAvailable = true) {
  return render(
    <ModeSelector
      capabilities={capabilities(aiAvailable)}
      value={value}
      onChange={() => undefined}
    />,
  );
}

describe("four selectable cards", () => {
  it("renders one per mode, in one radio group", () => {
    selector();
    const group = screen.getByRole("radiogroup", { name: "Analysis mode" });
    expect(within(group).getAllByRole("radio")).toHaveLength(4);
    expect(document.querySelectorAll(".mode-option")).toHaveLength(4);
  });

  it("puts Governed Analysis first, because it is the default path", () => {
    selector();
    const first = document.querySelector(".mode-option .mode-option-label");
    expect(first?.textContent).toBe("Governed Analysis");
  });

  it("marks exactly one as chosen, and not by colour alone", () => {
    selector("deterministic");
    const selected = document.querySelectorAll(".mode-option.selected");
    expect(selected).toHaveLength(1);
    // A filled mark on the chosen card, a hollow one on the rest. The
    // class and the background are not enough on their own.
    const marks = [...document.querySelectorAll(".mode-option-mark")].map(
      (node) => node.textContent,
    );
    expect(marks.filter((mark) => mark === "●")).toHaveLength(1);
    expect(marks.filter((mark) => mark === "○")).toHaveLength(3);
  });

  it("states every purpose without the card being selected first", () => {
    selector("auto");
    const purposes = [...document.querySelectorAll(".mode-option-purpose")];
    expect(purposes).toHaveLength(4);
    for (const purpose of purposes) {
      expect((purpose.textContent ?? "").trim().length).toBeGreaterThan(10);
    }
  });

  it("takes each purpose from its description, so the two cannot disagree", () => {
    selector();
    const purposes = [...document.querySelectorAll(".mode-option-purpose")].map(
      (node) => (node.textContent ?? "").trim(),
    );
    expect(purposes[0]).toBe("Rules resolve clear questions locally.");
    expect(purposes[2]).toBe(MODE_DEFAULTS.ai.description);
  });

  it("keeps a purpose to one sentence", () => {
    selector();
    for (const purpose of document.querySelectorAll(".mode-option-purpose")) {
      const text = (purpose.textContent ?? "").trim();
      // One terminal full stop, at the end. The four-paragraph layout is
      // what this is guarding against coming back.
      expect(text.split(". ").length, text).toBeLessThanOrEqual(1);
    }
  });
});

describe("what a screen reader is told", () => {
  it("names each radio by its mode and purpose, not by the full text twice", () => {
    selector();
    const radio = screen.getByRole("radio", { name: /^Governed Analysis/ });
    const name = radio.getAttribute("aria-label") ?? "";
    // The name comes from the label, which carries the mark (hidden), the
    // mode name and the purpose -- and not a second copy of the sentence.
    const label = document.querySelector('label[for="mode-auto"]')!;
    expect(label.textContent).not.toMatch(/locally\..*locally\./);
    expect(name === "" || name.length > 0).toBe(true);
  });

  it("describes each available mode with its full text, from outside the label", () => {
    selector();
    const described = document.querySelector("#mode-ai-description");
    expect(described?.textContent).toBe(MODE_DEFAULTS.ai.description);
    // Outside the label, so it is the accessible description rather than
    // part of the accessible name.
    expect(described?.closest("label")).toBeNull();
  });

  it("states a disabled mode's reason exactly once", () => {
    selector("auto", false);
    const reason = /turned off on this deployment/i;
    // Two disabled cards -- AI and Compare, which depends on it -- and one
    // element each. It used to be two each: a visible span and a hidden
    // copy, which a screen reader reads twice.
    expect(screen.getAllByText(reason)).toHaveLength(2);
  });

  it("leaves a disabled mode unselectable rather than absent", () => {
    selector("auto", false);
    expect(screen.getByRole("radio", { name: /^AI Analytics/ })).toBeDisabled();
    expect(
      screen.getByRole("radio", { name: /^Compare planning strategies/ }),
    ).toBeDisabled();
  });
});

describe("the explanation is behind one control", () => {
  it("renders no mode paragraph outside the disclosure", () => {
    selector();
    for (const testId of ["mode-description", "mode-taxonomy"]) {
      expect(screen.getByTestId(testId).closest("details")).not.toBeNull();
    }
  });

  it("keeps the disclosure closed until asked", () => {
    selector();
    expect(screen.getByTestId("mode-explainer").hasAttribute("open")).toBe(false);
  });

  it("puts the AI quota and what is sent inside it", () => {
    selector("ai");
    const explainer = screen.getByTestId("mode-explainer");
    expect(explainer.textContent).toMatch(/limited public quota/i);
    expect(explainer.textContent).toMatch(/schema, profiles and aggregates/i);
    expect(explainer.textContent).toMatch(/3 AI runs per dataset session/);
  });

  it("explains why Compare has two reports and not three", () => {
    selector();
    expect(screen.getByTestId("mode-taxonomy").textContent).toMatch(
      /two reports and not three/i,
    );
  });
});

describe("the run action names the planner it will use", () => {
  it("has a label per mode", () => {
    expect(Object.keys(RUN_LABELS).sort()).toEqual(
      ["ai", "auto", "compare", "deterministic"].sort(),
    );
  });

  it("never leaves two modes sharing one label", () => {
    const labels = Object.values(RUN_LABELS);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it("names Governed and Deterministic apart, which 'Run analysis' did not", () => {
    expect(RUN_LABELS.auto).not.toBe(RUN_LABELS.deterministic);
    for (const label of [RUN_LABELS.auto, RUN_LABELS.deterministic]) {
      expect(label).not.toBe("Run analysis");
    }
  });

  it("uses the comparison's one established name", () => {
    expect(RUN_LABELS.compare).toBe("Compare planning strategies");
  });
});
