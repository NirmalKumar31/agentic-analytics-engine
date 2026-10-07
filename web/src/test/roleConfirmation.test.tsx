/**
 * The control that lets a reader settle a role the data cannot.
 *
 * The behaviours here are the ones that keep it honest rather than merely
 * working:
 *
 *   - selecting is not confirming, because this changes how the engine
 *     aggregates a column;
 *   - a refusal leaves the prior server state on screen, because showing
 *     the attempted role would claim something the engine did not accept;
 *   - nothing is relabelled locally; the server's response is the session.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { suggestions } from "../components/DatasetSummary";
import { PlanningAudit } from "../components/PlanningAudit";
import { RoleConfirmation } from "../components/RoleConfirmation";
import { SchemaInspector } from "../components/SchemaInspector";
import type {
  DatasetSummary as SummaryPayload,
  InferredField,
  RunPayload,
} from "../lib/types";

function closeCall(overrides: Partial<InferredField> = {}): InferredField {
  return {
    name: "reading",
    data_type: "BIGINT",
    role: "measure",
    null_pct: 0,
    distinct_count: 48,
    reason: "integer with 48 values recurring across about 8 rows",
    additive: "weak",
    ambiguous: true,
    min_value: "18",
    max_value: "65",
    inferred_role: "measure",
    role_source: "inferred",
    allowed_confirmed_roles: ["measure", "dimension"],
    ...overrides,
  } as InferredField;
}

function settled(): InferredField {
  return {
    name: "amount",
    data_type: "DOUBLE",
    role: "measure",
    null_pct: 0,
    distinct_count: 400,
    reason: "a fractional quantity",
    additive: "strong",
    ambiguous: false,
    min_value: "1",
    max_value: "9",
    inferred_role: "measure",
    role_source: "inferred",
    allowed_confirmed_roles: [],
  } as InferredField;
}

function summary(fields: InferredField[], extra: Partial<SummaryPayload> = {}) {
  const named = (role: string) =>
    fields.filter((f) => f.role === role).map((f) => f.name);
  return {
    table: "uploaded_data",
    row_count: 400,
    status: "inferred",
    headline: "400 rows, 2 columns.",
    fields,
    measures: named("measure"),
    dimensions: named("dimension"),
    time_fields: named("time"),
    identifiers: named("identifier"),
    ambiguities: [],
    schema_revision: 0,
    confirmed_role_count: 0,
    unresolved_ambiguity_count: fields.filter(
      (f) => f.ambiguous && f.role_source !== "user_confirmed",
    ).length,
    ...extra,
  } as unknown as SummaryPayload;
}

describe("the confirmation control", () => {
  it("offers the two readings in plain language", () => {
    render(<RoleConfirmation field={closeCall()} onApply={vi.fn()} />);
    expect(screen.getByText(/Quantity/)).toBeVisible();
    expect(screen.getByText(/can be averaged or totalled/)).toBeVisible();
    expect(screen.getByText(/Category/)).toBeVisible();
    expect(screen.getByText(/split results into groups/)).toBeVisible();
    // The engine's vocabulary is not a reader's.
    expect(screen.queryByText(/^dimension$/)).toBeNull();
  });

  it("does not submit when a radio is merely selected", async () => {
    // Choosing is considering; confirming is a separate, deliberate act.
    const onApply = vi.fn().mockResolvedValue(undefined);
    render(<RoleConfirmation field={closeCall()} onApply={onApply} />);
    await userEvent.click(screen.getByRole("radio", { name: /Category/ }));
    expect(onApply).not.toHaveBeenCalled();
  });

  it("sends the chosen role only when confirmed", async () => {
    const onApply = vi.fn().mockResolvedValue(undefined);
    render(<RoleConfirmation field={closeCall()} onApply={onApply} />);
    await userEvent.click(screen.getByRole("radio", { name: /Category/ }));
    await userEvent.click(screen.getByRole("button", { name: /Confirm for this session/ }));
    expect(onApply).toHaveBeenCalledWith([
      { column: "reading", action: "confirm", role: "dimension" },
    ]);
  });

  it("can confirm the role inference already chose", async () => {
    const onApply = vi.fn().mockResolvedValue(undefined);
    render(<RoleConfirmation field={closeCall()} onApply={onApply} />);
    await userEvent.click(screen.getByRole("button", { name: /Confirm for this session/ }));
    expect(onApply).toHaveBeenCalledWith([
      { column: "reading", action: "confirm", role: "measure" },
    ]);
  });

  it("shows the confirmed state with both roles, and offers a reset", async () => {
    const onApply = vi.fn().mockResolvedValue(undefined);
    render(
      <RoleConfirmation
        field={closeCall({ role: "dimension", role_source: "user_confirmed" })}
        onApply={onApply}
      />,
    );
    const card = screen.getByTestId("role-confirmed");
    expect(card).toHaveTextContent(/Confirmed for this session/);
    expect(card).toHaveTextContent(/Category/);
    expect(card).toHaveTextContent(/inferred.*Quantity/i);

    await userEvent.click(screen.getByRole("button", { name: /Reset to inferred/ }));
    expect(onApply).toHaveBeenCalledWith([{ column: "reading", action: "reset" }]);
  });

  it("announces the outcome and moves focus to what replaced the button", async () => {
    // Confirming removes the button that was pressed. Left alone that drops
    // focus to <body> and announces nothing, because a removed element
    // reports no result.
    function Harness() {
      const [field, setField] = useState(closeCall());
      return (
        <RoleConfirmation
          field={field}
          onApply={async () => {
            setField(closeCall({ role: "dimension", role_source: "user_confirmed" }));
          }}
        />
      );
    }
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: /Confirm for this session/ }));

    const reset = await screen.findByRole("button", { name: /Reset to inferred/ });
    await waitFor(() => expect(reset).toHaveFocus());
    expect(screen.getByRole("status")).toHaveTextContent(
      /reading is confirmed for this session/,
    );
  });

  it("does not seize focus from a reader who did nothing", () => {
    // Every already-confirmed column on the page would otherwise grab
    // focus on first paint.
    render(
      <RoleConfirmation
        field={closeCall({ role: "dimension", role_source: "user_confirmed" })}
        onApply={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /Reset to inferred/ })).not.toHaveFocus();
    expect(screen.getByRole("status")).toHaveTextContent("");
  });

  it("says nothing when the server refuses, because the alert already did", async () => {
    const onApply = vi.fn().mockRejectedValue(new Error("an analysis is running"));
    render(<RoleConfirmation field={closeCall()} onApply={onApply} />);
    await userEvent.click(screen.getByRole("button", { name: /Confirm for this session/ }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeVisible());
    // A status that claimed a confirmation here would contradict the alert.
    expect(screen.getByRole("status")).toHaveTextContent("");
  });

  it("keeps the prior state and shows the reason when the server refuses", async () => {
    const onApply = vi.fn().mockRejectedValue(new Error("an analysis is running"));
    render(<RoleConfirmation field={closeCall()} onApply={onApply} />);
    await userEvent.click(screen.getByRole("radio", { name: /Category/ }));
    await userEvent.click(screen.getByRole("button", { name: /Confirm for this session/ }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/an analysis is running/),
    );
    // Not relabelled: the engine did not accept it.
    expect(screen.queryByTestId("role-confirmed")).toBeNull();
    expect(screen.getByTestId("role-confirmation")).toBeVisible();
  });

  it("disables its controls while the request is in flight", async () => {
    let release: () => void = () => undefined;
    const onApply = vi.fn().mockReturnValue(
      new Promise<void>((resolve) => {
        release = resolve;
      }),
    );
    render(<RoleConfirmation field={closeCall()} onApply={onApply} />);
    const button = screen.getByRole("button", { name: /Confirm for this session/ });
    await userEvent.click(button);
    expect(screen.getByRole("button", { name: /Confirming…/ })).toBeDisabled();
    release();
    await waitFor(() => expect(onApply).toHaveBeenCalledTimes(1));
  });
});

describe("where the control appears", () => {
  it("is absent when the dataset cannot be confirmed", () => {
    // No callback means a demo warehouse or a replay: the control is hidden
    // rather than shown disabled, because there is nothing to settle.
    render(<SchemaInspector summary={summary([closeCall()])} />);
    expect(screen.queryByTestId("role-confirmation")).toBeNull();
  });

  it("is absent for a column the data already settles", () => {
    render(
      <SchemaInspector summary={summary([settled()])} onConfirmRoles={vi.fn()} />,
    );
    expect(screen.queryByTestId("role-confirmation")).toBeNull();
  });

  it("appears for an unsettled close call", () => {
    render(
      <SchemaInspector summary={summary([closeCall()])} onConfirmRoles={vi.fn()} />,
    );
    expect(screen.getByTestId("role-confirmation")).toBeTruthy();
  });

  it("counts only the close calls nobody has settled", () => {
    const confirmed = closeCall({ role: "dimension", role_source: "user_confirmed" });
    const inspector = render(
      <SchemaInspector
        summary={summary([confirmed, closeCall({ name: "other" })], {
          unresolved_ambiguity_count: 1,
        })}
        onConfirmRoles={vi.fn()}
      />,
    );
    expect(inspector.getByTestId("schema-inspector")).toHaveTextContent(
      /1 role the data cannot settle/,
    );
  });
});

describe("the planning audit", () => {
  function run(evidence: RunPayload["role_evidence"]): RunPayload {
    return {
      run_id: "r",
      events: [],
      role_evidence: evidence,
      schema_revision: 1,
    } as unknown as RunPayload;
  }

  it("names a confirmed role, both readings and the session scope", () => {
    render(
      <PlanningAudit
        run={run([
          {
            column: "reading",
            effective_role: "dimension",
            inferred_role: "measure",
            role_source: "user_confirmed",
            ambiguous: true,
            used_as: ["grouping"],
          },
        ])}
      />,
    );
    const evidence = screen.getByTestId("role-evidence");
    expect(evidence).toHaveTextContent(/reading/);

    // Scoped to the column's own entry, not the section.
    //
    // Asserting against the whole block matched the heading: "Roles
    // confirmed for this dataset session", so removing the source from
    // every entry left the test green. The claim is about what each line
    // says, so each line is what is read.
    const entry = evidence.querySelector("dd");
    expect(entry).not.toBeNull();
    expect(entry).toHaveTextContent(/Used as grouping/);
    expect(entry).toHaveTextContent(/read as category/);
    expect(entry).toHaveTextContent(/originally inferred quantity/);
    expect(entry).toHaveTextContent(/confirmed for this dataset session/);
  });

  it("does not claim the confirmation is governed or verified", () => {
    render(
      <PlanningAudit
        run={run([
          {
            column: "reading",
            effective_role: "dimension",
            inferred_role: "measure",
            role_source: "user_confirmed",
            ambiguous: true,
            used_as: ["grouping"],
          },
        ])}
      />,
    );
    const evidence = screen.getByTestId("role-evidence");
    for (const overclaim of [/governed/i, /verified/i, /correct/i, /saved preference/i]) {
      expect(evidence).not.toHaveTextContent(overclaim);
    }
  });

  it("says nothing about columns the engine classified itself", () => {
    render(
      <PlanningAudit
        run={run([
          {
            column: "place",
            effective_role: "dimension",
            inferred_role: "dimension",
            role_source: "inferred",
            ambiguous: false,
            used_as: ["grouping"],
          },
        ])}
      />,
    );
    expect(screen.queryByTestId("role-evidence")).toBeNull();
  });
});

describe("what the page offers to ask next", () => {
  const place = (): InferredField =>
    ({
      name: "place",
      data_type: "VARCHAR",
      role: "dimension",
      null_pct: 0,
      distinct_count: 4,
      reason: "four repeated values",
      additive: "unknown",
      ambiguous: false,
      min_value: "east",
      max_value: "west",
      inferred_role: "dimension",
      role_source: "inferred",
      allowed_confirmed_roles: [],
    }) as InferredField;

  it("names a column the reader confirmed as a quantity", () => {
    // Confirming clears the additivity guess, so without the confirmed
    // branch this column matched nothing and the page stopped mentioning
    // it, and a reader who answered the question got silence back.
    const reading = closeCall({
      role: "measure",
      additive: "unknown",
      role_source: "user_confirmed",
      inferred_role: "dimension",
    });
    const offered = suggestions(summary([reading, place()]));
    expect(offered.join(" ")).toMatch(/average reading by place/);
  });

  it("does not offer to total it, which nobody asserted", () => {
    // "Can be averaged or totalled" is the control's wording; additivity
    // is a separate property the confirmation does not establish.
    const reading = closeCall({
      role: "measure",
      additive: "unknown",
      role_source: "user_confirmed",
      inferred_role: "dimension",
    });
    const offered = suggestions(summary([reading, place()])).join(" ");
    expect(offered).not.toMatch(/total reading/);
    expect(offered).not.toMatch(/contributes most/);
  });

  it("offers grouping by a column confirmed as a category", () => {
    const reading = closeCall({
      role: "dimension",
      role_source: "user_confirmed",
      inferred_role: "measure",
    });
    const amount = settled();
    expect(suggestions(summary([reading, amount])).join(" ")).toMatch(
      /total amount by reading/,
    );
  });

  it("leaves an unconfirmed dataset's suggestions alone", () => {
    // The boundary: nothing about this feature may change what a reader
    // who never touches it sees.
    const offered = suggestions(summary([settled(), place()]));
    expect(offered).toEqual([
      "What is total amount by place?",
      "Which place has the highest total amount?",
    ]);
  });
});

describe("what a confirmed field does to the offered questions", () => {
  // The defect these pin: `dimensions.find(usable)` took whatever the schema
  // listed first, so confirming a column as a category changed nothing on
  // screen whenever an ordinary dimension preceded it. The reader answered
  // the one question the engine could not and got no acknowledgement.

  function col(
    name: string,
    role: string,
    extra: Partial<InferredField> = {},
  ): InferredField {
    return {
      name,
      data_type: role === "dimension" ? "VARCHAR" : "BIGINT",
      role,
      null_pct: 0,
      distinct_count: 12,
      reason: "r",
      additive: "unknown",
      ambiguous: false,
      min_value: "1",
      max_value: "9",
      inferred_role: role,
      role_source: "inferred",
      allowed_confirmed_roles: [],
      ...extra,
    } as InferredField;
  }

  const confirmedCategory = (name: string) =>
    col(name, "dimension", {
      ambiguous: true,
      role_source: "user_confirmed",
      inferred_role: "measure",
      allowed_confirmed_roles: ["measure", "dimension"],
    });

  it("puts a confirmed category ahead of a dimension that precedes it", () => {
    // `site` is first in the schema and is an ordinary dimension. Before
    // this ordering existed, it won and `reading` was never named.
    const offered = suggestions(
      summary([
        col("site", "dimension"),
        col("amount", "measure", { additive: "strong" }),
        confirmedCategory("reading"),
      ]),
    ).join(" ");
    expect(offered).toMatch(/by reading/);
    expect(offered).not.toMatch(/by site/);
  });

  it("keeps the confirmed category inside the three-question cap", () => {
    const offered = suggestions(
      summary([
        col("site", "dimension"),
        col("depot", "dimension"),
        col("amount", "measure", { additive: "strong" }),
        col("stamp", "time"),
        confirmedCategory("reading"),
      ]),
    );
    expect(offered.length).toBeLessThanOrEqual(3);
    expect(offered.some((q) => /by reading/.test(q))).toBe(true);
  });

  it("totals a strongly additive measure by the confirmed category", () => {
    const offered = suggestions(
      summary([
        col("site", "dimension"),
        col("amount", "measure", { additive: "strong" }),
        confirmedCategory("reading"),
      ]),
    ).join(" ");
    expect(offered).toMatch(/total amount by reading/);
  });

  it("falls back to a row count when no measure is safe to use", () => {
    // `dose` is a measure the engine will not vouch for either way, so
    // neither a total nor an average is honest. Counting rows always is.
    const offered = suggestions(
      summary([
        col("site", "dimension"),
        col("dose", "measure", { additive: "unknown" }),
        confirmedCategory("reading"),
      ]),
    );
    expect(offered).toEqual(["How many rows by reading?"]);
  });

  it("prefers a confirmed quantity for the average", () => {
    const offered = suggestions(
      summary([
        col("rating", "measure", { additive: "weak" }),
        col("site", "dimension"),
        col("reading", "measure", {
          ambiguous: true,
          role_source: "user_confirmed",
          inferred_role: "dimension",
          allowed_confirmed_roles: ["measure", "dimension"],
        }),
      ]),
    ).join(" ");
    expect(offered).toMatch(/average reading by site/);
    expect(offered).not.toMatch(/average rating/);
  });

  it("never totals a confirmed quantity on its own say-so", () => {
    // "Can be averaged or totalled" is what the control said; additivity is
    // a separate property the confirmation does not establish.
    const offered = suggestions(
      summary([
        col("site", "dimension"),
        col("reading", "measure", {
          ambiguous: true,
          role_source: "user_confirmed",
          inferred_role: "dimension",
          allowed_confirmed_roles: ["measure", "dimension"],
        }),
      ]),
    ).join(" ");
    expect(offered).toMatch(/average reading/);
    expect(offered).not.toMatch(/total reading/);
    expect(offered).not.toMatch(/highest total reading/);
  });

  it("keeps schema order among several confirmed categories", () => {
    // Deterministic: the result must not depend on which was settled first.
    const offered = suggestions(
      summary([
        col("site", "dimension"),
        col("batch", "dimension", {
          ambiguous: true,
          role_source: "user_confirmed",
          inferred_role: "measure",
        }),
        col("amount", "measure", { additive: "strong" }),
        confirmedCategory("reading"),
      ]),
    ).join(" ");
    // `batch` precedes `reading` in the schema, so it wins.
    expect(offered).toMatch(/by batch/);
    expect(offered).not.toMatch(/by reading/);
  });

  it("orders by the schema, not by however the API listed the roles", () => {
    // The two coincide in every other fixture here, because the helper
    // derives the role lists from `fields`. They need not coincide in a
    // real payload, and "schema order" is the claim, so this builds a
    // summary whose `dimensions` array is in the opposite order and
    // asserts the schema still decides.
    const confirmedFirst = confirmedCategory("batch");
    const confirmedSecond = confirmedCategory("reading");
    const payload = {
      table: "uploaded_data",
      row_count: 400,
      status: "inferred",
      headline: "h",
      fields: [confirmedFirst, col("amount", "measure", { additive: "strong" }), confirmedSecond],
      measures: ["amount"],
      // Reversed relative to `fields`.
      dimensions: ["reading", "batch"],
      time_fields: [],
      identifiers: [],
      ambiguities: [],
      schema_revision: 1,
      confirmed_role_count: 2,
      unresolved_ambiguity_count: 0,
    } as unknown as SummaryPayload;

    const offered = suggestions(payload).join(" ");
    expect(offered).toMatch(/by batch/);
    expect(offered).not.toMatch(/by reading/);
  });

  it("offers no question twice", () => {
    const offered = suggestions(
      summary([
        col("site", "dimension"),
        col("amount", "measure", { additive: "strong" }),
        col("stamp", "time"),
        confirmedCategory("reading"),
      ]),
    );
    expect(new Set(offered).size).toBe(offered.length);
  });

  it("restores the inferred ordering when the role is reset", () => {
    const fields = [
      col("site", "dimension"),
      col("amount", "measure", { additive: "strong" }),
      confirmedCategory("reading"),
    ];
    const whileConfirmed = suggestions(summary(fields)).join(" ");
    expect(whileConfirmed).toMatch(/by reading/);

    // Reset: `reading` goes back to the role the engine inferred, so it
    // leaves the dimension list entirely and `site` is first again.
    const afterReset = suggestions(
      summary([
        col("site", "dimension"),
        col("amount", "measure", { additive: "strong" }),
        col("reading", "measure", {
          ambiguous: true,
          role_source: "inferred",
          inferred_role: "measure",
          allowed_confirmed_roles: ["measure", "dimension"],
        }),
      ]),
    ).join(" ");
    expect(afterReset).toMatch(/by site/);
    expect(afterReset).not.toMatch(/by reading/);
  });
});
