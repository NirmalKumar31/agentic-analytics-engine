/**
 * The landing surface: one question, and two ways to answer it.
 *
 * This replaces `DatasetOnboarding`, which was a `<section class="panel">`
 * headed **DATASET** containing two equal-weight cards, two paragraphs of
 * small print and a list of recordings. Three things were wrong with it.
 *
 * It opened with a noun. "DATASET" tells a visitor what the software is
 * thinking about, not what they can do. The first thing on the page is now
 * the question the product exists to answer.
 *
 * It gave upload and the prepared data equal weight, side by side, which
 * made the visitor choose between two things before understanding either.
 * Bringing your own data is the primary path and is now the primary
 * affordance -- a drop target you can also click. The prepared datasets are
 * an alternative offered underneath it, in a list, which is what "or" means.
 *
 * And its small print was styled as small print. What may leave the server is
 * not a disclaimer; it is part of the choice a visitor is making, and is now
 * stated at body weight inside the drop zone where the decision is made.
 */

import { useState } from "react";

import { AnalyticalField } from "./AnalyticalField";
import type {
  RecordingSummary,
  ServerConfig,
  SessionPayload,
} from "../lib/types";

export function LandingView({
  config,
  session,
  replay,
  busy,
  onDemo,
  onUploadClick,
  onFile,
  onRecording,
}: {
  config: ServerConfig;
  session: SessionPayload | null;
  replay: RecordingSummary | null;
  busy: boolean;
  onDemo: () => void;
  onUploadClick: () => void;
  onFile: (file: File) => void;
  onRecording: (recording: RecordingSummary) => void;
}) {
  const [dragging, setDragging] = useState(false);

  return (
    <section className="landing" data-testid="landing">
      <AnalyticalField />

      <div className="landing-lede">
        <h1 className="display" data-testid="landing-headline">
          What would you like to understand?
        </h1>
        <p className="landing-sub">
          {config.uploads_enabled
            ? "Bring a CSV or Parquet file, or start from a dataset we have prepared."
            : "Start from a dataset we have prepared."}
        </p>
      </div>

      <div className="landing-body">
        {config.uploads_enabled && (
          <div
            className={`dropzone${dragging ? " dropzone--active" : ""}`}
            data-testid="dropzone"
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              const file = event.dataTransfer.files?.[0];
              if (file) onFile(file);
            }}
          >
            <h2 className="dropzone-title">Drop a file here, or choose one</h2>
            <p className="dropzone-limits">
              CSV or Parquet · up to {config.max_upload_mb} MB ·{" "}
              {config.max_upload_columns.toLocaleString()} columns
            </p>
            {/* Stated at body weight, not as small print. What happens to a
                reader's file, and what does or does not leave the machine,
                is the decision being made at this control. */}
            <p className="dropzone-terms">
              Your file stays for this session only and is deleted after{" "}
              {config.session_ttl_minutes} minutes of inactivity.
            </p>
            <p className="dropzone-terms">
              Deterministic Analytics sends nothing to a model. Governed
              Analysis may use a model when local rules cannot resolve the
              question; AI Analytics and Compare use one by design.
            </p>
            <button
              type="button"
              className="btn primary"
              onClick={onUploadClick}
              disabled={busy}
              aria-pressed={session?.catalog.dataset_kind === "upload"}
            >
              Choose a file
            </button>
            <div className="dropzone-caution">
              Please don&rsquo;t upload sensitive or regulated data.
              {config.model_inference_remote && (
                <>
                  {" "}
                  <details className="disclosure">
                    <summary>What can be sent to OpenAI</summary>
                    When a run uses AI, your question, column names, inferred
                    column types such as date, measure or category, and
                    computed results go to OpenAI as part of the prompt.
                    Computed results include
                    the labels of a column you group by — a total by department
                    cannot be reported without naming the departments.
                    Individual rows do not go: row sampling is refused, a
                    profile&rsquo;s smallest and largest values are withheld,
                    and a column with a different value on almost every row is
                    never used as a grouping. Requests ask OpenAI not to store
                    the exchange; what it retains beyond that is governed by
                    that account&rsquo;s data settings, not by this
                    application. Deterministic Analytics sends nothing to any
                    provider.
                  </details>
                </>
              )}
            </div>
          </div>
        )}

        <div className="prepared" data-testid="prepared-data">
          <h2 className="section-heading">Or start from prepared data</h2>

          {!config.live_analytics_enabled && (
            <p className="prepared-note">
              Live analysis is off on this server. Recorded runs still open.
            </p>
          )}

          {/* Restored after being dropped in the first draft of this view.
              It is not filler: a reader looking at a recorded run with
              published and withheld counts needs to know those decisions
              came from the deterministic scripted provider and not from a
              language model. Losing it made the recordings look like model
              output, which is the single claim this product most needs to
              get right. */}
          {config.recordings.length > 0 && (
            <p className="prepared-note prepared-provenance">
              Each recorded run is a real run captured end to end, with its
              queries, results and verification decisions intact. These were
              produced by the deterministic scripted provider, not a language
              model.
            </p>
          )}

          <ul className="prepared-list">
            {config.demo_warehouse_ready && (
              <li>
                <button
                  type="button"
                  className="prepared-item"
                  onClick={onDemo}
                  disabled={busy}
                  aria-pressed={session?.catalog.dataset_kind === "demo"}
                >
                  <span className="prepared-title">
                    Commerce demo warehouse
                  </span>
                  <span className="prepared-meta">
                    seven related tables · two years · fixed seed
                  </span>
                  <span className="prepared-go" aria-hidden="true">
                    →
                  </span>
                </button>
              </li>
            )}

            {config.recordings.map((recording) => (
              <li key={recording.recording_id}>
                <button
                  type="button"
                  className="prepared-item"
                  onClick={() => onRecording(recording)}
                  disabled={busy}
                  aria-pressed={replay?.recording_id === recording.recording_id}
                >
                  <span className="prepared-title">{recording.title}</span>
                  {/* The engine's own words for what this run demonstrates,
                      set in mono because it names techniques and counts, not
                      prose. The published/withheld counts stay: a recording
                      that withheld a finding is the interesting one. */}
                  <span className="prepared-meta">
                    {recording.demonstrates}
                  </span>
                  <span className="prepared-counts">
                    {recording.run_kind ?? "recorded run"} ·{" "}
                    {recording.findings} published, {recording.rejected}{" "}
                    withheld, {recording.mcp_tool_calls} MCP calls
                  </span>
                  <span className="prepared-go" aria-hidden="true">
                    →
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
