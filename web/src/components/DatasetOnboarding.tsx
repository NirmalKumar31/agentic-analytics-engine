import { useState } from "react";
import type {
  RecordingSummary,
  ServerConfig,
  SessionPayload,
} from "../lib/types";

export function DatasetOnboarding({
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
    <section className="panel">
      <div className="panel-head">
        <h2>Dataset</h2>
        <span className="spacer" />
        {!config.live_analytics_enabled && (
          <span className="small dim">live analysis is off on this server</span>
        )}
      </div>
      <div className="panel-body stack">
        <div className="dataset-grid">
          <button
            className="choice"
            onClick={onDemo}
            disabled={busy || !config.demo_warehouse_ready}
            aria-pressed={Boolean(session?.catalog.dataset_kind === "demo")}
          >
            <strong>Commerce demo warehouse</strong>
            <span>
              Seven related tables, two years, deterministic. Generated locally
              with a fixed seed.
            </span>
          </button>
          <button
            className={`choice ${dragging ? "dropping" : ""}`}
            onClick={onUploadClick}
            disabled={busy || !config.uploads_enabled}
            aria-pressed={Boolean(session?.catalog.dataset_kind === "upload")}
            onDragOver={(event) => {
              if (!config.uploads_enabled) return;
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              const file = event.dataTransfer.files?.[0];
              if (file && config.uploads_enabled) onFile(file);
            }}
          >
            <strong>Upload your data</strong>
            <span>
              {config.uploads_enabled
                ? `Drop a CSV or Parquet file, or click to choose one. Up to ${config.max_upload_mb} MB and ${config.max_upload_columns} columns.`
                : "Disabled on this server."}
            </span>
          </button>
        </div>

        {config.uploads_enabled && (
          <p className="small dim" style={{ margin: 0 }}>
            Your file stays for this session only and is deleted after 15
            minutes of inactivity. Please don't upload sensitive or regulated
            data.
            {config.model_inference_remote && (
              <>
                {" "}
                <details className="disclosure">
                  <summary>What is sent to OpenAI in AI mode</summary>
                  Column names, inferred column roles and computed results go to
                  OpenAI as part of the prompt. Computed results include the
                  labels of a column you group by — a total by department cannot
                  be reported without naming the departments. Individual rows do
                  not go: row sampling is refused, a profile's smallest and
                  largest values are withheld, and a column with a different
                  value on almost every row is never used as a grouping.
                  Requests ask OpenAI not to store the exchange; what it retains
                  beyond that is governed by that account's data settings, not
                  by this application. Deterministic Analytics sends nothing to
                  any provider.
                </details>
              </>
            )}
          </p>
        )}

        {config.recordings.length > 0 && (
          <>
            <p className="small dim" style={{ margin: "4px 0 0" }}>
              Or open a recorded run. Each is a real run captured end to end,
              with its queries, results and verification decisions intact. These
              were produced by the deterministic scripted provider, not a
              language model.
            </p>
            <div className="example-list">
              {config.recordings.map((recording) => (
                <button
                  className="example"
                  key={recording.recording_id}
                  onClick={() => onRecording(recording)}
                  disabled={busy}
                  aria-pressed={replay?.recording_id === recording.recording_id}
                >
                  {recording.title}
                  <small>
                    {recording.demonstrates}
                    <br />
                    {recording.run_kind ?? "recorded run"} · {recording.findings}{" "}
                    published, {recording.rejected} withheld,{" "}
                    {recording.mcp_tool_calls} MCP calls
                  </small>
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    </section>
  );
}
