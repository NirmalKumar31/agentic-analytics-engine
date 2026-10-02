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

        <div className="table-wrap" style={{ maxHeight: 280 }}>
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
 * dataset whose only classified measure is `age` -- because the real
 * measure is near-unique and reads as an identifier -- that produced
 * "What is total age by team_size?" and "Which team_size contributes most
 * to age?". Both are reproducible arithmetic and neither is a question
 * anyone wants answered.
 *
 * So a sum is suggested only for a column whose name reads as a quantity,
 * an average is offered for one that reads as an attribute, and a
 * contribution question -- which only makes sense over an additive total
 * -- is offered for neither unless the engine is confident.
 *
 * A column the reader confirmed as a quantity counts as an attribute here.
 * Confirming clears the additivity guess, because asserting "this is a
 * quantity" says nothing about whether totalling it means anything -- so
 * without this the confirmed column matched no branch and nothing on the
 * page mentioned it again. An average is what the control offered in so
 * many words ("can be averaged or totalled"); a sum is a further claim
 * nobody made.
 */
export function suggestions(summary: Summary): string[] {
  const fields = summary.fields ?? [];
  const field = (name: string) => fields.find((f) => f.name === name);
  const confidence = (name: string) => field(name)?.additive ?? "unknown";
  const confirmed = (name: string) => field(name)?.role_source === "user_confirmed";
  const usable = (name: string) => !/^(noise|random|dummy|unused)_/i.test(name);

  const additive = summary.measures.find(
    (name) => usable(name) && confidence(name) === "strong",
  );
  const attribute = summary.measures.find(
    (name) => usable(name) && (confidence(name) === "weak" || confirmed(name)),
  );
  const dimension = summary.dimensions.find(usable);
  const time = summary.time_fields.find(usable);

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
    // Nothing safe to total. Counting rows is always meaningful.
    out.push(`How many rows by ${dimension}?`);
  }
  return out.slice(0, 3);
}
