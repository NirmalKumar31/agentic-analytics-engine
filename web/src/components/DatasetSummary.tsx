import { RoleConfirmation } from "./RoleConfirmation";
import type { DatasetSummary as Summary, RoleChange } from "../lib/types";

const ROLE_ORDER = [
  "time",
  "measure",
  "dimension",
  "identifier",
  "ignored",
] as const;

const ROLE_LABEL: Record<string, string> = {
  time: "time",
  measure: "measure",
  dimension: "dimension",
  identifier: "identifier",
  ignored: "not used",
};

/**
 * What the engine worked out about an uploaded file, before any question.
 *
 * Every role here is inferred from column types and cardinality, which the
 * panel says plainly — an inferred measure is not a governed metric, and
 * conflating the two would be inventing business semantics.
 */
export function DatasetSummary({
  summary,
  onAsk,
  showHead = true,
  onConfirmRoles,
}: {
  summary: Summary;
  onAsk?: (question: string) => void;
  /** False when a disclosure already names this panel. */
  showHead?: boolean;
  /** Present only where roles may be confirmed. */
  onConfirmRoles?: (changes: RoleChange[]) => Promise<void>;
}) {
  const shown = [...summary.fields].sort(
    (a, b) => ROLE_ORDER.indexOf(a.role) - ROLE_ORDER.indexOf(b.role),
  );

  return (
    <section className="panel">
      {showHead && (
        <div className="panel-head">
          <h2>Dataset understanding</h2>
          <span className="spacer" />
          <span className="tag">inferred</span>
        </div>
      )}
      <div className="panel-body stack">
        <p className="muted" style={{ margin: 0 }}>
          {summary.headline}
        </p>

        {/* Focusable, because it scrolls.
            WCAG 2.1.1: a region a pointer can scroll must be reachable by
            keyboard, or its content is unreachable without a mouse. This
            went unflagged while the inspector sat on a wide canvas and the
            table did not actually overflow; inside a 560px side sheet it
            does, and axe caught it immediately. `tabIndex={0}` with a
            label is the whole fix -- the browser supplies arrow-key
            scrolling once the region can take focus. */}
        <div
          className="table-wrap"
          style={{ maxHeight: 280 }}
          tabIndex={0}
          role="group"
          aria-label="Inferred field roles"
        >
          <table className="data">
            <thead>
              <tr>
                <th scope="col">column</th>
                <th scope="col">type</th>
                <th scope="col">role</th>
                <th scope="col">distinct</th>
                <th scope="col">null %</th>
                <th scope="col">why</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((field) => (
                <tr
                  key={field.name}
                  // Marked on the row, not only on the role, because the
                  // whole inference is the close call -- the type and
                  // cardinality beside it are the evidence for it.
                  data-ambiguous={field.ambiguous ? "true" : undefined}
                >
                  <td>{field.name}</td>
                  <td className="dim">{field.data_type}</td>
                  <td>
                    <span className={`tag role-${field.role}`}>
                      {ROLE_LABEL[field.role]}
                    </span>
                    {field.ambiguous && (
                      <span className="tag ambiguous" data-testid="ambiguous-field">
                        {field.role_source === "user_confirmed"
                          ? "confirmed"
                          : "close call"}
                      </span>
                    )}
                    {onConfirmRoles &&
                    field.ambiguous &&
                    (field.allowed_confirmed_roles?.length ?? 0) > 0 ? (
                      <RoleConfirmation field={field} onApply={onConfirmRoles} />
                    ) : null}
                  </td>
                  <td>{field.distinct_count.toLocaleString()}</td>
                  <td>{field.null_pct.toFixed(1)}</td>
                  <td className="dim">{field.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="small dim" style={{ margin: 0 }}>
          Roles are inferred from column types and cardinality. They are not
          governed metric definitions — the engine does not know what your
          columns mean.
        </p>

        {shown.some((field) => field.ambiguous) && (
          <p className="small dim" style={{ margin: 0 }} data-testid="ambiguity-note">
            A <strong>close call</strong> means the data cannot settle the
            role. A column of small whole numbers can be a quantity worth
            averaging or a code that identifies something, and nothing in
            the values separates the two. The engine has picked the more
            likely reading and marked it rather than hiding the choice.{" "}
            {onConfirmRoles ? (
              <>
                <strong>You can settle it for this session.</strong> The
                engine validates what you choose, uses it when it plans the
                query, and names it in the planning audit. It is your
                statement about this upload — not a governed definition, not
                saved anywhere, and gone when the session ends.
              </>
            ) : (
              <>
                <strong>You cannot confirm it on this dataset</strong> —
                settling a role needs an uploaded file of your own, because
                the choice belongs to whoever knows what the column means. If
                a close call matters for your question, say which column you
                mean in the question itself.
              </>
            )}
          </p>
        )}

        {summary.ambiguities.length > 0 && (
          <div className="notice warn">
            <strong>One thing to confirm</strong>
            <ul className="list small" style={{ marginTop: 6 }}>
              {summary.ambiguities.map((item) => (
                <li key={item.concept}>{item.question}</li>
              ))}
            </ul>
          </div>
        )}

        {onAsk && summary.measures.length > 0 && (
          <div className="example-list">
            {suggestions(summary).map((question) => (
              <button
                className="example"
                key={question}
                onClick={() => onAsk(question)}
              >
                {question}
              </button>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

/**
 * Questions this dataset can actually answer, and that are worth asking.
 *
 * The first version took `measures[0]` and proposed totalling it. On a
 * dataset whose only classified measure is `age`, because the real
 * measure is near-unique and reads as an identifier -- that produced
 * "What is total age by team_size?" and "Which team_size contributes most
 * to age?". Both are reproducible arithmetic and neither is a question
 * anyone wants answered.
 *
 * So a sum is suggested only for a column whose name reads as a quantity,
 * an average is offered for one that reads as an attribute, and a
 * contribution question, which only makes sense over an additive total
 * -- is offered for neither unless the engine is confident.
 *
 * A column the reader confirmed as a quantity counts as an attribute here.
 * Confirming clears the additivity guess, because asserting "this is a
 * quantity" says nothing about whether totalling it means anything, so
 * without this the confirmed column matched no branch and nothing on the
 * page mentioned it again. An average is what the control offered in so
 * many words ("can be averaged or totalled"); a sum is a further claim
 * nobody made.
 *
 * Confirmed fields come first. `dimensions.find(usable)` took whatever the
 * schema listed first, so confirming a column as a category changed
 * nothing on screen whenever an ordinary dimension happened to precede it
 * -- the reader answered the one question the engine could not and got no
 * acknowledgement anywhere they were looking. Candidates are therefore
 * ordered confirmed-first, independently for measures and dimensions, and
 * in schema order inside each group so the result is deterministic rather
 * than dependent on which column someone settled first.
 */
export function suggestions(summary: Summary): string[] {
  const fields = summary.fields ?? [];
  const field = (name: string) => fields.find((f) => f.name === name);
  const confidence = (name: string) => field(name)?.additive ?? "unknown";
  const confirmed = (name: string) => field(name)?.role_source === "user_confirmed";
  const usable = (name: string) => !/^(noise|random|dummy|unused)_/i.test(name);

  /**
   * One role's usable candidates: confirmed first, schema order within
   * each group.
   *
   * Driven off `fields` rather than the role list, because `fields` is the
   * schema order. Sorting the role list would be ordering by whatever the
   * API happened to emit, which is not a stable thing to depend on.
   */
  const candidates = (names: string[]): string[] => {
    const wanted = new Set(names);
    const inSchemaOrder = fields
      .map((entry) => entry.name)
      .filter((name) => wanted.has(name) && usable(name));
    return [
      ...inSchemaOrder.filter(confirmed),
      ...inSchemaOrder.filter((name) => !confirmed(name)),
    ];
  };

  const measures = candidates(summary.measures);
  const dimensions = candidates(summary.dimensions);

  const additive = measures.find((name) => confidence(name) === "strong");
  const attribute = measures.find(
    (name) => confidence(name) === "weak" || confirmed(name),
  );
  const dimension = dimensions[0];
  const time = candidates(summary.time_fields)[0];

  const out: string[] = [];
  if (additive && dimension)
    out.push(`What is total ${additive} by ${dimension}?`);
  if (attribute && dimension)
    out.push(`What is the average ${attribute} by ${dimension}?`);
  if (additive && time) out.push(`How did ${additive} change over time?`);
  if (additive && dimension) {
    out.push(`Which ${dimension} has the highest total ${additive}?`);
  }
  if (out.length === 0 && dimension) {
    // Nothing safe to total or average. Counting rows is always meaningful,
    // and it is what keeps a confirmed category from going unmentioned on a
    // dataset with no measure the engine will vouch for.
    out.push(`How many rows by ${dimension}?`);
  }
  // Deduplicated before the cap, so a repeat cannot consume a slot and
  // push the question naming a confirmed field off the end.
  return [...new Set(out)].slice(0, 3);
}


/** What a suggested question is asking for, which is how the composer groups them. */
export type Intent = "Trend" | "Compare" | "Rank" | "Relationship" | "Count";

/**
 * The same suggestions, labelled by what they ask for.
 *
 * The labels are derived from the sentence `suggestions()` built, not
 * guessed at from the text: this function and that one are the same
 * knowledge, and reading the intent back out of English would be a parser
 * that drifts the first time the wording changes.
 *
 * `Relationship` is deliberately absent. The generator does not produce a
 * two-variable question, because proposing one would invite a causal
 * reading the verifier would then withhold, and a suggestion that
 * reliably produces a withheld finding is a worse suggestion than none.
 * The wireframe showed four intent groups; three are honest.
 */
export function groupedSuggestions(
  summary: Summary,
): Array<{ intent: Intent; question: string }> {
  return suggestions(summary).map((question) => ({
    intent: intentOf(question),
    question,
  }));
}

function intentOf(question: string): Intent {
  if (question.startsWith("How did")) return "Trend";
  if (question.startsWith("Which")) return "Rank";
  if (question.startsWith("How many")) return "Count";
  return "Compare";
}
