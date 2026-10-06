import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AppShell } from "./components/AppShell";
import { DatasetContextBar } from "./components/DatasetContextBar";
import { EvidenceDrawer } from "./components/EvidenceDrawer";
import { LandingView } from "./components/LandingView";
import { ProductHeader } from "./components/ProductHeader";
import { QuestionComposer } from "./components/QuestionComposer";
import { ReportWorkspace } from "./components/ReportWorkspace";
import { RunProgress } from "./components/RunProgress";
import { SchemaInspector } from "./components/SchemaInspector";
import { SideSheet } from "./components/SideSheet";
import { TerminalState } from "./components/TerminalState";
import { useTheme } from "./components/ThemeToggle";
import { ApiError, api } from "./lib/api";
import { phaseOf } from "./lib/phase";
import type {
  ComparisonStarted,
  RoleChange,
  RecordingSummary,
  RunEvent,
  RunPayload,
  ServerConfig,
  SessionPayload,
  UiMode,
} from "./lib/types";
import { useRunEvents } from "./lib/useRunEvents";


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
  const [replay, setReplay] = useState<RecordingSummary | null>(null);
  const [uiMode, setUiMode] = useState<UiMode>("auto");
  const [theme, toggleTheme] = useTheme();
  const [comparison, setComparison] = useState<ComparisonStarted | null>(null);
  const [aiRun, setAiRun] = useState<RunPayload | null>(null);
  const [aiError, setAiError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const { events, finished } = useRunEvents(runId);

  useEffect(() => {
    api.config().then(setConfig).catch((reason: unknown) => {
      setConfigError(reason instanceof ApiError ? reason.message : "Could not load server configuration.");
    });
  }, []);

  useEffect(() => {
    if (!runId || !finished) return;
    api.run(runId).then(setRun).catch((reason: unknown) => {
      setError(reason instanceof ApiError ? reason.message : "Could not load the finished run.");
    });
  }, [runId, finished]);

  const recordedEvents: RunEvent[] = useMemo(
    () => (replay && run ? run.events : events),
    [replay, run, events],
  );
  const phase = phaseOf({ config, configError, session, replay, runId, run, busy, error, events });

  useEffect(() => {
    document.body.dataset.phase = phase;
    return () => { delete document.body.dataset.phase; };
  }, [phase]);

  useEffect(() => {
    const sync = () => { document.body.dataset.hidden = document.hidden ? "true" : "false"; };
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => {
      document.removeEventListener("visibilitychange", sync);
      delete document.body.dataset.hidden;
    };
  }, []);

  const openDemo = useCallback(async () => {
    setBusy(true); setError(null);
    try {
      setReplay(null); setRun(null); setRunId(null);
      setSession(await api.openDemo());
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Could not open the demo dataset.");
    } finally { setBusy(false); }
  }, []);

  const upload = useCallback(async (file: File) => {
    setBusy(true); setError(null);
    try {
      setReplay(null); setRun(null); setRunId(null);
      setSession(await api.upload(file));
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "The upload was rejected.");
    } finally { setBusy(false); }
  }, []);

  const openRecording = useCallback(async (summary: RecordingSummary) => {
    setBusy(true); setError(null);
    try {
      const payload = await api.recording(summary.recording_id);
      setReplay(summary); setSession(null); setRunId(null); setRun(payload);
      setQuestion(payload.question);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Could not load the recording.");
    } finally { setBusy(false); }
  }, []);

  const confirmRoles = useCallback(
    async (changes: RoleChange[]) => {
      if (!session) return;
      const revision = session.summary?.schema_revision ?? 0;
      try {
        // The server's response becomes the session. Mutating local state
        // instead would let the panel report a role the engine had not
        // accepted, which is the defect this whole feature exists to avoid.
        setSession(await api.confirmSchemaRoles(session.session_id, revision, changes));
      } catch (reason) {
        if (reason instanceof ApiError && reason.status === 409) {
          // The schema moved under this view -- another tab, or this one
          // racing itself. Reload rather than retry: the column list being
          // shown is no longer the one the server has.
          try {
            setSession(await api.dataset(session.session_id));
          } catch {
            /* the reload failed; the error below still reaches the control */
          }
          throw new Error(
            "The dataset schema changed since this panel was loaded. It has been refreshed — please check it and try again.",
          );
        }
        throw new Error(
          reason instanceof ApiError ? reason.message : "the change was not saved",
        );
      }
    },
    [session],
  );

  const ask = useCallback(async () => {
    if (!session || !question.trim()) return;
    setBusy(true); setError(null); setRun(null); setAiRun(null);
    setAiError(null); setComparison(null);
    try {
      if (uiMode === "compare") {
        const started = await api.startComparison(session.session_id, question.trim());
        setComparison(started); setRunId(started.deterministic_run_id);
      } else {
        const { run_id } = await api.startAnalysis(session.session_id, question.trim(), uiMode);
        setRunId(run_id);
      }
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "The analysis could not be started.");
    } finally { setBusy(false); }
  }, [session, question, uiMode]);

  useEffect(() => {
    if (!comparison) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const payload = await api.run(comparison.ai_run_id);
        if (cancelled) return;
        setAiRun(payload);
        if (payload.status === "failed" && payload.error) setAiError(payload.error);
        if (payload.status === "running") window.setTimeout(poll, 1200);
      } catch (reason) {
        if (!cancelled) setAiError(reason instanceof ApiError ? reason.message : "The AI run could not be read.");
      }
    };
    void poll();
    return () => { cancelled = true; };
  }, [comparison]);

  const reset = useCallback(() => {
    setRun(null); setRunId(null); setReplay(null);
    setComparison(null); setAiRun(null); setAiError(null);
  }, []);

  const endSession = useCallback(async () => {
    if (!session) return;
    setBusy(true);
    try { await api.endSession(session.session_id); } catch { /* expired is already gone */ }
    finally {
      setSession(null); setRun(null); setRunId(null); setReplay(null);
      setQuestion(""); setBusy(false);
    }
  }, [session]);

  // The schema inspector is a sheet now, not a resident table. Closed by
  // default and closed again whenever the dataset changes: a sheet left
  // open across an upload would be describing the previous file.
  const [schemaOpen, setSchemaOpen] = useState(false);
  // The evidence drawer. One per report, opened by one control, closed on
  // Escape. Reset whenever a new run begins, so a drawer left open is never
  // describing the previous run.
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  useEffect(() => { setEvidenceOpen(false); }, [runId]);
  useEffect(() => { setSchemaOpen(false); }, [session?.session_id]);

  /*
   * The per-finding provenance drawer is gone with the finding cards that
   * opened it. Every published finding used to carry its own "Show work →",
   * and six identical cards meant six triggers for six drawers describing
   * one run. The evidence drawer carries the contract, verification, cited
   * cells, timings and the trace once, for the report.
   */
  const catalog = run?.dataset ?? session?.catalog ?? null;
  const hasRun = Boolean(run || runId);

  return (
    <AppShell
      sessionId={session?.session_id}
      hasRun={hasRun}
      header={<ProductHeader config={config} hasRun={hasRun} hasSession={Boolean(session)} replaying={Boolean(replay)} uiMode={uiMode} theme={theme} onToggleTheme={toggleTheme} onEndSession={() => void endSession()} onReset={reset} />}
    >
      <div className="column">
        <DatasetContextBar
          catalog={catalog}
          summary={session?.summary ?? null}
          onInspect={session?.summary ? () => setSchemaOpen(true) : undefined}
        />
        <TerminalState configError={configError} error={error} />
        {/* The landing and the composer are different states of the screen,
            not two things stacked on it. The old Dataset panel stayed
            mounted under the Ask panel for the whole session, so after an
            upload a reader saw their file's composer above a drop zone
            still inviting them to choose one. With the landing being a
            full-page hero that is not merely redundant, it is two products
            on one page. */}
        {!session && !run && !runId && config && (
          <LandingView config={config} session={session} replay={replay} busy={busy} onDemo={() => void openDemo()} onUploadClick={() => fileInput.current?.click()} onFile={(file) => void upload(file)} onRecording={(recording) => void openRecording(recording)} />
        )}
        <input ref={fileInput} type="file" accept=".csv,.parquet" className="sr-only" aria-label="Upload a CSV or Parquet file" onChange={(event) => { const file = event.target.files?.[0]; if (file) void upload(file); event.target.value = ""; }} />
        {session && !hasRun && <QuestionComposer config={config} summary={
                  // Only an uploaded file gets schema-derived examples. The
                  // demo session also carries a summary, so gating on its
                  // presence alone replaced the curated demo questions --
                  // which exist to demonstrate the governed metric registry
                  // -- with generic ones derived from its tables.
                  session?.catalog.dataset_kind === "upload"
                    ? session.summary
                    : null
                } question={question} onQuestionChange={setQuestion} onAsk={() => void ask()} busy={busy} uiMode={uiMode} onModeChange={setUiMode} />}
        {/*
          The timeline narrates a run in flight. Once the report exists it
          is no longer the thing on screen -- the answer is -- so it moves
          into the evidence drawer with the rest of the technical record.
        */}
        {hasRun && !run && <RunProgress events={recordedEvents} replay={replay} mode={uiMode} />}
        <ReportWorkspace comparison={comparison} run={run} aiRun={aiRun} aiError={aiError} config={config} deterministicPending={Boolean(runId) && !finished} onShowEvidence={() => setEvidenceOpen(true)} />
      </div>
      {evidenceOpen && run && (
        <EvidenceDrawer
          run={run}
          showTrace={showTrace}
          onToggleTrace={() => setShowTrace((value) => !value)}
          onClose={() => setEvidenceOpen(false)}
        />
      )}
      {schemaOpen && session?.summary && (
        <SideSheet
          title="Dataset schema"
          testId="schema-sheet"
          onClose={() => setSchemaOpen(false)}
        >
          <SchemaInspector
            open
            summary={session.summary}
            onConfirmRoles={
              session.catalog.dataset_kind === "upload" ? confirmRoles : undefined
            }
          />
        </SideSheet>
      )}
    </AppShell>
  );
}
