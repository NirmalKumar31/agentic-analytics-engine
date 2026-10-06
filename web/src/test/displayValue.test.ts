/**
 * The display contract, enforced on the browser side.
 *
 * The same hand-written cases `tests/unit/test_display_value_contract.py`
 * reads. Two implementations of one rule, and neither is the authority:
 * when they disagree about how a cell reads, one of the two suites fails
 * and names the case.
 *
 * The defect this guards against shipped. The backend resolved a period
 * through its grain and the headline said "Oct 2025"; the table formatted
 * the same cell with no field metadata and said "2025-01-01T00:00:00",
 * beside "1,050,312.91" with no currency. Each surface was consistent with
 * itself. There was no shared term between them.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { displayValue } from "../lib/displayValue";
import type { DisplayField } from "../lib/types";

interface Case {
  name: string;
  field: Partial<DisplayField> | null;
  raw: unknown;
  expected: string;
}

const contract = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "displayValueContract.json"), "utf8"),
) as {
  field_defaults: DisplayField;
  cases: Case[];
};

const fieldOf = (entry: Case): DisplayField | null =>
  entry.field === null ? null : { ...contract.field_defaults, ...entry.field };

describe("the display contract", () => {
  it("is committed, and thick enough to be a real join", () => {
    expect(contract.cases.length).toBeGreaterThanOrEqual(20);
  });

  for (const entry of contract.cases) {
    it(entry.name, () => {
      expect(displayValue(entry.raw, fieldOf(entry))).toBe(entry.expected);
    });
  }

  it("publishes no identifier and no serialisation in any expectation", () => {
    for (const entry of contract.cases) {
      if (entry.name.includes("as the engine stored it")) continue;
      expect(entry.expected, entry.name).not.toMatch(/\bNone\b/);
      expect(entry.expected, entry.name).not.toMatch(/[[\]]/);
      expect(entry.expected, entry.name).not.toMatch(/\d{4}-\d{2}-\d{2}T/);
      expect(entry.expected, entry.name).not.toMatch(/\bnull\b/);
    }
  });
});
