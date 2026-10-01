import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ActivityLog } from "./components/ActivityLog";
import { ComparisonView } from "./components/ComparisonView";
import { DatasetSummary } from "./components/DatasetSummary";
import { ExecutionFlow } from "./components/ExecutionFlow";
import { phaseOf } from "./lib/phase";
import { ExecutionLane } from "./components/ExecutionLanes";
import { badgeMode, ModeBadge } from "./components/ModeBadge";
import { ThemeToggle, useTheme } from "./components/ThemeToggle";
import { ModeSelector } from "./components/ModeSelector";
import { ProvenanceDrawer } from "./components/ProvenanceDrawer";
import { PlanningAudit } from "./components/PlanningAudit";
import { ReportView } from "./components/ReportView";
import { runState } from "./lib/runState";
import { RightRail } from "./components/RightRail";
import { ApiError, api } from "./lib/api";
import type {
  ComparisonStarted,
  MetricInfo,
  RecordingSummary,
  RunEvent,
  RunPayload,
  ServerConfig,
  SessionPayload,
  UiMode,
} from "./lib/types";
import { useRunEvents } from "./lib/useRunEvents";

type Stage = "dataset" | "ask" | "running" | "report";

/** Which finding's working to show, and which run it belongs to.
 *
 * Compare Both runs two independent analyses and each assigns finding ids
 * within itself, so an id alone does not identify a finding. Carrying the
 * side makes the drawer resolve against the run the user actually clicked.
 */
type ProvenanceTarget = {
  side: "deterministic" | "ai";
  findingId: string;
};

export function App() {
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [session, setSession] = useState<SessionPayload | null>(null);
  const [question, setQuestion] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<RunPayload | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showTrace, setShowTrace] = useState(false);
  // Which finding, and *whose*. A bare id was ambiguous: Compare Both
  // runs two independent analyses whose finding ids are assigned per run,
  // so the same id can exist on both sides meaning different things. The
  // drawer resolved from the deterministic run regardless, so AI "Show
  // work" opened deterministic evidence for an AI claim, or nothing.
  const [target, setTarget] = useState<ProvenanceTarget | null>(null);
  const [replay, setReplay] = useState<RecordingSummary | null>(null);
  // Deterministic by default. The server decides what else is on offer.
  const [uiMode, setUiMode] = useState<UiMode>("auto");
  const [theme, toggleTheme] = useTheme();
  const [comparison, setComparison] = useState<ComparisonStarted | null>(null);
  const [aiRun, setAiRun] = useState<RunPayload | null>(null);
  const [aiError, setAiError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const { events, finished } = useRunEvents(runId);

  useEffect(() => {
    api
      .config()
      .then(setConfig)
      .catch((e: unknown) =>
        setConfigError(
          e instanceof ApiError
            ? e.message
            : "Could not load server configuration.",
        ),
      );
  }, []);

  // A live run's payload is fetched once its event stream ends.
  useEffect(() => {
    if (!runId || !finished) return;
    api
      .run(runId)
      .then(setRun)
      .catch((e: unknown) =>
        setError(
          e instanceof ApiError
            ? e.message
            : "Could not load the finished run.",
        ),
      );
  }, [runId, finished]);

  const recordedEvents: RunEvent[] = useMemo(
    () => (replay && run ? run.events : events),
    [replay, run, events],
  );

  const stage: Stage = run
    ? "report"
    : runId
      ? "running"
      : session || replay
        ? "ask"
        : "dataset";

  // The phase the stylesheet keys on. Derived from the state above rather
  // than stored beside it: a phase that can disagree with the run payload
  // would render the wrong thing confidently.
  const phase = phaseOf({
    config,
    configError,
    session,
    replay,
    runId,
    run,
    busy,
    error,
    events,
  });

  // Published on the body because the ambient grid is a `body::before`,
  // and CSS cannot reach React state. The grid drifts only while there is
  // nothing to read, and recedes once a report is on screen.
  useEffect(() => {
    document.body.dataset.phase = phase;
    return () => {
      delete document.body.dataset.phase;
    };
  }, [phase]);

  // A hidden tab animates nothing. CSS cannot see document visibility, so
  // it is mirrored onto the body: rendering a drifting grid for a tab
  // nobody is looking at spends a reader's battery for nothing.
  useEffect(() => {
    const sync = () => {
      document.body.dataset.hidden = document.hidden ? "true" : "false";
    };
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => {
      document.removeEventListener("visibilitychange", sync);
      delete document.body.dataset.hidden;
    };
  }, []);

  const openDemo = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setReplay(null);
      setRun(null);
      setRunId(null);
      setSession(await api.openDemo());
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message : "Could not open the demo dataset.",
      );
    } finally {
      setBusy(false);
    }
  }, []);

  const upload = useCallback(async (file: File) => {
    setBusy(true);
    setError(null);
    try {
      setReplay(null);
      setRun(null);
      setRunId(null);
      setSession(await api.upload(file));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The upload was rejected.");
    } finally {
      setBusy(false);
    }
  }, []);

  const openRecording = useCallback(async (summary: RecordingSummary) => {
    setBusy(true);
    setError(null);
    try {
      const payload = await api.recording(summary.recording_id);
      setReplay(summary);
      setSession(null);
      setRunId(null);
      setRun(payload);
      setQuestion(payload.question);
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message : "Could not load the recording.",
      );
    } finally {
      setBusy(false);
    }
  }, []);

  const ask = useCallback(async () => {
    if (!session || !question.trim()) return;
    setBusy(true);
    setError(null);
    setRun(null);
    setAiRun(null);
    setAiError(null);
    setComparison(null);
    try {
      if (uiMode === "compare") {
        const started = await api.startComparison(
          session.session_id,
          question.trim(),
        );
        setComparison(started);
        setRunId(started.deterministic_run_id);
      } else {
        const { run_id } = await api.startAnalysis(
          session.session_id,
          question.trim(),
          uiMode,
        );
        setRunId(run_id);
      }
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.message
          : "The analysis could not be started.",
      );
    } finally {
      setBusy(false);
    }
  }, [session, question, uiMode]);

  // The AI side of a comparison is polled separately, so one side failing
  // never removes the other.
  useEffect(() => {
    if (!comparison) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const payload = await api.run(comparison.ai_run_id);
        if (cancelled) return;
        setAiRun(payload);
        if (payload.status === "failed" && payload.error)
          setAiError(payload.error);
        if (payload.status === "running") window.setTimeout(poll, 1200);
      } catch (e) {
        if (!cancelled) {
          setAiError(
            e instanceof ApiError ? e.message : "The AI run could not be read.",
          );
        }
      }
    };
    void poll();
    return () => {
      cancelled = true;
    };
  }, [comparison]);

  const reset = useCallback(() => {
    setRun(null);
    setRunId(null);
    setReplay(null);
    setTarget(null);
    setComparison(null);
    setAiRun(null);
    setAiError(null);
  }, []);

  const endSession = useCallback(async () => {
    if (!session) return;
    setBusy(true);
    try {
      await api.endSession(session.session_id);
    } catch {
      // The session may already have expired; either way it is gone.
    } finally {
      setSession(null);
      setRun(null);
      setRunId(null);
      setReplay(null);
      setTarget(null);
      setQuestion("");
      setBusy(false);
    }
  }, [session]);

  // The run the target names -- never a fallback to the other one. A
  // drawer showing the wrong run's SQL is worse than a drawer showing
  // nothing, because it looks like provenance.
  const provenanceRun = target ? (target.side === "ai" ? aiRun : run) : null;
  const finding =
    provenanceRun?.findings.find((f) => f.finding_id === target?.findingId) ??
    null;
  const catalog = run?.dataset ?? session?.catalog ?? null;
  const metrics: MetricInfo[] = session?.metrics ?? [];
  const usedMetrics = useMemo(
    () => [...new Set((run?.findings ?? []).flatMap((f) => f.metric_ids))],
    [run],
  );

  return (
    // The session *handle* is exposed as an attribute so browser tests can
    // check that it stops working after the session ends. The handle is
    // public by design and authorises nothing on its own; the capability is
    // in an HttpOnly cookie and never reaches this markup.
    <div className="shell" data-session-id={session?.session_id ?? undefined}>
      <header className="topbar">
        <div className="brand">
          <Mark />
          <div>
            Agentic Analytics Engine
            <br />
            <small>
              bounded analysis · MCP tools · provenance on every number
            </small>
          </div>
        </div>
        <div className="topbar-spacer" />
        {/* Only once something has actually run. The badge says what
            produced what is on screen, and before a run nothing has -- so
            announcing "Deterministic live" on the dataset picker claims a
            result that does not exist yet. */}
        {config && (run || runId || replay) && (
          <ModeBadge
            mode={badgeMode(!!replay, config.execution_mode, uiMode)}
          />
        )}
        <ThemeToggle theme={theme} onToggle={toggleTheme} />
        {session && (
          <button
            className="btn ghost small"
            onClick={endSession}
            title="Delete this dataset and everything derived from it"
          >
            End session
          </button>
        )}
        {(run || runId) && (
          <button className="btn ghost small" onClick={reset}>
            Start over
          </button>
        )}
      </header>

      {/*
        Focusable because it scrolls horizontally on a narrow viewport. A
        scroll container that cannot be focused cannot be scrolled from the
        keyboard, which axe reports as `scrollable-region-focusable` -- and
        it became one when the overflow fix landed.
      */}
      <nav className="steps" aria-label="Progress" tabIndex={0}>
        <Step
          index={1}
          label="Dataset"
          state={stage === "dataset" ? "active" : "done"}
        />
        <Step
          index={2}
          label="Ask"
          state={
            stage === "ask" ? "active" : stage === "dataset" ? "idle" : "done"
          }
        />
        <Step
          index={3}
          label="Analyse"
          state={
            stage === "running"
              ? "active"
              : stage === "report"
                ? "done"
                : "idle"
          }
        />
        <Step
          index={4}
          label="Verify"
          state={stage === "report" ? "done" : "idle"}
        />
        <Step
          index={5}
          label="Report"
          state={stage === "report" ? "active" : "idle"}
        />
      </nav>

      <main className="main" data-rail={run || runId ? "true" : "false"}>
        <div className="column">
          {configError && <div className="notice error">{configError}</div>}
          {error && <div className="notice error">{error}</div>}

          {!run && !runId && config && (
            <DatasetPanel
              config={config}
              session={session}
              replay={replay}
              busy={busy}
              onDemo={openDemo}
              onUploadClick={() => fileInput.current?.click()}
              onFile={(file) => void upload(file)}
              onRecording={openRecording}
            />
          )}

          <input
            ref={fileInput}
            type="file"
            accept=".csv,.parquet"
            className="sr-only"
            /* Visually hidden but present in the accessibility tree, so it
               needs a name of its own: a screen reader otherwise announces
               an unlabelled file field. */
            aria-label="Upload a CSV or Parquet file"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void upload(file);
              e.target.value = "";
            }}
          />

          {session?.summary &&
            session.catalog.dataset_kind === "upload" &&
            !run &&
            !runId && (
              <DatasetSummary summary={session.summary} onAsk={setQuestion} />
            )}

          {session && !run && !runId && (
            <AskPanel
              config={config}
              question={question}
              setQuestion={setQuestion}
              onAsk={ask}
              busy={busy}
              uiMode={uiMode}
              setUiMode={setUiMode}
            />
          )}

          {(runId || run) && (
            <section className="panel">
              <div className="panel-head">
                <h2>Analysis</h2>
                <span className="spacer" />
                {replay && (
                  <span className="small dim">
                    recorded run · {replay.recording_id}
                  </span>
                )}
              </div>
              <ExecutionFlow events={recordedEvents} />
              {/*
                The branch diagram is drawn from the plan's task list, so a
                path that answers the question with one governed query draws
                one branch and looks like it did almost nothing. It did more,
                not less: it resolved a typed contract, validated it and
                checked its coverage before any SQL ran, and the diagram has
                no nodes for any of that. The lane names those stages with
                what they actually produced, so the stronger path stops
                looking like the weaker one.
              */}
              {/*
                Compare Both renders its own lane per mode, side by side.
                A third lane here would describe one of those runs again,
                in a panel that does not say which.
              */}
              {run && uiMode !== "compare" && (
                <div className="lane-grid single">
                  <ExecutionLane
                    run={run}
                    mode={uiMode === "ai" ? "ai" : "deterministic"}
                    title="Stages"
                    compared={false}
                  />
                </div>
              )}
            </section>
          )}

          {(runId || run) && (
            <ActivityLog
              events={recordedEvents}
              trace={run?.mcp_trace ?? []}
              showTrace={showTrace}
              onToggleTrace={() => setShowTrace((v) => !v)}
              running={Boolean(runId) && !finished}
            />
          )}

          {run && run.stopped_reason && (
            <div className="notice warn">
              The run stopped early: {run.stopped_reason}
            </div>
          )}

          {comparison ? (
            <ComparisonView
              question={comparison.question}
              deterministic={{
                title: "Deterministic Analytics",
                subtitle:
                  "Rule-based planning over the governed analytics engine.",
                run,
                error: null,
                pending: Boolean(runId) && !finished,
                children: run ? (
                  <ReportView
                    question={run.question}
                    report={run.report}
                    findings={run.findings}
                    rejected={run.rejected}
                    charts={run.charts}
                    results={run.results}
                    queryContract={run.query_contract}
                    presentation={run.presentation}
                    onShowWork={(id) =>
                      setTarget({ side: "deterministic", findingId: id })
                    }
                  />
                ) : null,
              }}
              ai={{
                title: "AI Analytics",
                subtitle:
                  "A cloud model plans and interprets; the engine computes and verifies.",
                run: aiRun,
                error: aiError,
                // AI off by capability, quota or configuration is not a
                // failed run, and must not be reported as one.
                unavailable:
                  !aiRun &&
                  !aiError &&
                  Boolean(
                    config?.capabilities?.modes.some(
                      (m) => m.mode === "ai" && !m.available,
                    ),
                  ),
                pending: Boolean(aiRun && aiRun.status === "running"),
                usage: aiRun?.usage,
                children:
                  aiRun && runState(aiRun).showsReport ? (
                    <ReportView
                      question={aiRun.question}
                      report={aiRun.report}
                      findings={aiRun.findings}
                      rejected={aiRun.rejected}
                      charts={aiRun.charts}
                      results={aiRun.results}
                      queryContract={aiRun.query_contract}
                      presentation={aiRun.presentation}
                      onShowWork={(id) =>
                        setTarget({ side: "ai", findingId: id })
                      }
                    />
                  ) : null,
              }}
            />
          ) : (
            run && (
              <ReportView
                question={run.question}
                report={run.report}
                findings={run.findings}
                rejected={run.rejected}
                charts={run.charts}
                results={run.results}
                queryContract={run.query_contract}
                presentation={run.presentation}
                onShowWork={(id) =>
                  setTarget({ side: "deterministic", findingId: id })
                }
              />
            )
          )}

          {run && !comparison ? <PlanningAudit run={run} /> : null}
        </div>

        {/* Run telemetry belongs behind the evidence inspector once an answer
            exists; a permanent rail made task and MCP counts compete with the
            answer. Keep dataset context on the onboarding screen only. */}
        {!run && !runId && (
          <RightRail
            catalog={catalog}
            metrics={metrics}
            usedMetrics={usedMetrics}
            results={{}}
            tasks={[]}
            runMetrics={null}
            onOpenResult={() => undefined}
          />
        )}
      </main>

      {finding && provenanceRun && (
        <ProvenanceDrawer
          finding={finding}
          results={provenanceRun.results}
          tasks={provenanceRun.tasks}
          trace={provenanceRun.mcp_trace}
          onClose={() => setTarget(null)}
        />
      )}
      {target && !finding && (
        <div
          className="drawer"
          role="dialog"
          aria-label="Provenance unavailable"
        >
          <div className="drawer-head">
            <h3>Provenance unavailable</h3>
            <button type="button" onClick={() => setTarget(null)}>
              Close
            </button>
          </div>
          <p>
            This finding is no longer part of the{" "}
            {target.side === "ai" ? "AI" : "deterministic"} run, so its working
            cannot be shown. Re-run the question to inspect it.
          </p>
        </div>
      )}
    </div>
  );
}

function DatasetPanel({
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
  onRecording: (r: RecordingSummary) => void;
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
            aria-pressed={Boolean(
              session && session.catalog.dataset_kind === "demo",
            )}
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
            aria-pressed={Boolean(
              session && session.catalog.dataset_kind === "upload",
            )}
            onDragOver={(e) => {
              if (!config.uploads_enabled) return;
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              const file = e.dataTransfer.files?.[0];
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
                    {recording.run_kind ?? "recorded run"} ·{" "}
                    {recording.findings} published, {recording.rejected}{" "}
                    withheld, {recording.mcp_tool_calls} MCP calls
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

function AskPanel({
  config,
  question,
  setQuestion,
  onAsk,
  busy,
  uiMode,
  setUiMode,
}: {
  config: ServerConfig | null;
  question: string;
  setQuestion: (q: string) => void;
  onAsk: () => void;
  busy: boolean;
  uiMode: UiMode;
  setUiMode: (m: UiMode) => void;
}) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Ask</h2>
      </div>
      <div className="panel-body stack">
        {config?.capabilities && (
          <ModeSelector
            capabilities={config.capabilities}
            value={uiMode}
            onChange={setUiMode}
            disabled={busy}
          />
        )}
        {uiMode === "auto" && (
          <p
            className="notice info small"
            data-testid="interpretation-notice"
            style={{ margin: 0 }}
          >
            Clear questions are resolved by rules without contacting a model. If
            the question is genuinely ambiguous, one governed AI planning call
            may be used; the engine still executes and verifies the calculation
            deterministically.
          </p>
        )}
        {uiMode === "deterministic" && (
          <p
            className="notice info small"
            data-testid="interpretation-notice"
            style={{ margin: 0 }}
          >
            Question interpretation is rule-based in this public demo: a
            scripted provider maps your wording onto the dataset, and says so
            when it cannot. The SQL, the statistics, the MCP tool execution, the
            verification and the provenance are real.
          </p>
        )}
        <textarea
          className="field"
          rows={3}
          value={question}
          maxLength={500}
          placeholder="Why did gross margin fall in Q3 2025?"
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) onAsk();
          }}
          aria-label="Business question"
        />
        <div className="row">
          <button
            className="btn primary"
            onClick={onAsk}
            disabled={busy || !question.trim()}
          >
            {busy
              ? "Starting…"
              : uiMode === "compare"
                ? "Run both"
                : uiMode === "ai"
                  ? "Run with AI"
                  : "Run analysis"}
          </button>
          <span className="small dim">{question.length}/500 · ⌘↵ to run</span>
        </div>
        {config && config.demo_questions.length > 0 && (
          <div className="example-list">
            {config.demo_questions.map((item) => (
              <button
                className="example"
                key={item.id}
                onClick={() => setQuestion(item.question)}
              >
                {item.question}
                <small>{item.why}</small>
              </button>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function Step({
  index,
  label,
  state,
}: {
  index: number;
  label: string;
  state: "idle" | "active" | "done";
}) {
  return (
    <div className="step" data-state={state}>
      <span className="step-index">{state === "done" ? "✓" : index}</span>
      {label}
    </div>
  );
}

function Mark() {
  return (
    <svg
      className="brand-mark"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <rect
        x="1"
        y="1"
        width="22"
        height="22"
        rx="5"
        stroke="var(--border-strong)"
      />
      <path
        d="M5 16.5 L9.5 10 L13.5 13.5 L19 6.5"
        stroke="var(--accent)"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="9.5" cy="10" r="1.6" fill="var(--supported)" />
      <circle cx="19" cy="6.5" r="1.6" fill="var(--supported)" />
    </svg>
  );
}
