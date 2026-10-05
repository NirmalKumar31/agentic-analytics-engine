/**
 * Where two governed interpretations of one question differ.
 *
 * Compare Both could already say *whether* the two panes agreed, by
 * comparing contract hashes. A reader told only "different governed
 * interpretations" cannot act on it: the divergence might be the measure,
 * the grouping, a dropped row restriction or a time period, and those are
 * very different things to have happened. So the canonical contracts are
 * compared field by field and the differing fields are named.
 *
 * The canonical contract is what is compared, not the full one:
 * `explanation` and `interpretation` record who read the wording, which
 * differs between the two modes by construction.
 */

import type { CanonicalContract, QueryContract, QueryFilter } from "./types";

export interface ContractDifference {
  /** The reader-facing name of what differed, e.g. "Grouping". */
  label: string;
  deterministic: string;
  ai: string;
}

const NONE = "none";

function describeFilter(filter: QueryFilter): string {
  const value =
    filter.value === null || filter.value === undefined
      ? ""
      : String(filter.value);
  return `${filter.column} ${filter.operator} ${value}`.trim();
}

/** Filters are a set, not a sequence: order carries no meaning. */
function describeFilters(filters: QueryFilter[] | undefined): string {
  if (!filters || filters.length === 0) return NONE;
  return [...filters].map(describeFilter).sort().join("; ");
}

function describePeriod(period: [string, string] | null | undefined): string {
  return period ? `${period[0]} to ${period[1]}` : NONE;
}

function describeColumn(column: string | null | undefined): string {
  return column ? column : NONE;
}

/** Ordered, because "by region then channel" is not "by channel then region". */
function describeGroupings(dimensions: string[] | undefined): string {
  return dimensions && dimensions.length > 0 ? dimensions.join(" then ") : NONE;
}

/** The canonical view of a contract, tolerating an older payload. */
export function canonicalOf(
  contract: QueryContract | null | undefined,
): CanonicalContract | null {
  if (!contract) return null;
  return contract.canonical_contract ?? contract;
}

/**
 * Every canonical field that is compared, in the order it is reported.
 *
 * At module scope rather than inside the function, so the caption over the
 * diff table can say "n of m fields differ" with both numbers coming from
 * the same place as the rows underneath it. A caption written beside a
 * table is a caption that drifts from it.
 */
const CANONICAL_FIELDS: Array<[string, (c: CanonicalContract) => string]> = [
  ["Calculation", (c) => c.operation],
  ["Table", (c) => c.table],
  ["Measure", (c) => describeColumn(c.measure)],
  // Every cut, in order. Reading the singular projection here made a
  // two-cut contract look ungrouped, so two panes that grouped
  // differently compared as identical.
  ["Grouping", (c) => describeGroupings(c.dimensions)],
  ["Time grain", (c) => c.time_grain ?? NONE],
  ["Row filters", (c) => describeFilters(c.filters)],
  ["Time period", (c) => describePeriod(c.period)],
  ["Period column", (c) => describeColumn(c.period_field)],
  ["Time axis", (c) => describeColumn(c.time_field)],
  ["Sort order", (c) => (c.ascending ? "ascending" : "descending")],
];

/** How many canonical fields are compared at all — the `m` in "n of m". */
export const COMPARED_FIELD_COUNT = CANONICAL_FIELDS.length;

/**
 * What can honestly be said about two contracts.
 *
 * `identical` and `different` are claims about two things that exist.
 * `not_recorded` is the absence of one or both, and it is a separate state
 * because an absence is not an agreement -- a report that said
 * `contracts: identical` over two panes whose own appendices each read "no
 * contract was accepted" contradicted itself on the same page. That is the
 * worst kind of defect in a product whose argument is that it does not
 * overclaim.
 *
 * `not_comparable` is for a missing *run*: there is no second thing to
 * compare at all.
 */
export type ComparisonState =
  | "identical"
  | "different"
  | "not_recorded"
  | "not_comparable";

/** The reader-facing word for each state. */
export const COMPARISON_LABEL: Record<ComparisonState, string> = {
  identical: "identical",
  different: "differ",
  not_recorded: "not recorded",
  not_comparable: "not comparable",
};

/**
 * Whether two contracts agree, differ, or were never recorded.
 *
 * Separate from `contractDifferences` because the caller needs to know
 * *why* a difference list is empty. An empty list used to mean both
 * "these agree" and "there was nothing to compare", and the strip read the
 * first meaning into both.
 */
export function contractComparison(
  deterministic: QueryContract | null | undefined,
  ai: QueryContract | null | undefined,
  options: { bothRan: boolean } = { bothRan: true },
): ComparisonState {
  if (!options.bothRan) return "not_comparable";
  const left = canonicalOf(deterministic);
  const right = canonicalOf(ai);
  if (!left || !right) return "not_recorded";
  return contractDifferences(deterministic, ai).length > 0 ? "different" : "identical";
}

export function contractDifferences(
  deterministic: QueryContract | null | undefined,
  ai: QueryContract | null | undefined,
): ContractDifference[] {
  const left = canonicalOf(deterministic);
  const right = canonicalOf(ai);
  // An absent contract yields no differences, and the caller must not read
  // that as agreement -- `contractComparison` is what distinguishes them.
  if (!left || !right) return [];

  const out: ContractDifference[] = [];
  for (const [label, read] of CANONICAL_FIELDS) {
    const a = read(left);
    const b = read(right);
    if (a !== b) out.push({ label, deterministic: a, ai: b });
  }
  return out;
}
