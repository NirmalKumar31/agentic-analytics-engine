import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AppShell } from "./components/AppShell";
import { DatasetIdentity } from "./components/DatasetIdentity";
import { DatasetOnboarding } from "./components/DatasetOnboarding";
import { ProductHeader } from "./components/ProductHeader";
import { ProvenanceDrawer } from "./components/ProvenanceDrawer";
import { RightRail } from "./components/RightRail";
import { QuestionComposer } from "./components/QuestionComposer";
import { ReportWorkspace, type ProvenanceSide } from "./components/ReportWorkspace";
import { RunProgress } from "./components/RunProgress";
import { SchemaInspector } from "./components/SchemaInspector";
import { TerminalState } from "./components/TerminalState";
import { useTheme } from "./components/ThemeToggle";
import { WorkflowIndex } from "./components/WorkflowIndex";
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

type ProvenanceTarget = { side: ProvenanceSide; findingId: string };

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
  const [target, setTarget] = useState<ProvenanceTarget | null>(null);
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
    setRun(null); setRunId(null); setReplay(null); setTarget(null);
    setComparison(null); setAiRun(null); setAiError(null);
  }, []);

  const endSession = useCallback(async () => {
    if (!session) return;
    setBusy(true);
    try { await api.endSession(session.session_id); } catch { /* expired is already gone */ }
    finally {
      setSession(null); setRun(null); setRunId(null); setReplay(null);
      setTarget(null); setQuestion(""); setBusy(false);
    }
  }, [session]);

  const provenanceRun = target ? (target.side === "ai" ? aiRun : run) : null;
  const finding = provenanceRun?.findings.find((candidate) => candidate.finding_id === target?.findingId) ?? null;
  const catalog = run?.dataset ?? session?.catalog ?? null;
  const hasRun = Boolean(run || runId);

  return (
    <AppShell
      sessionId={session?.session_id}
      hasRun={hasRun}
      header={<ProductHeader config={config} hasRun={hasRun} hasSession={Boolean(session)} replaying={Boolean(replay)} uiMode={uiMode} theme={theme} onToggleTheme={toggleTheme} onEndSession={() => void endSession()} onReset={reset} />}
      workflow={<WorkflowIndex phase={phase} />}
    >
      <div className="column">
        <DatasetIdentity catalog={catalog} />
        <TerminalState configError={configError} error={error} />
        {!run && !runId && config && (
          <DatasetOnboarding config={config} session={session} replay={replay} busy={busy} onDemo={() => void openDemo()} onUploadClick={() => fileInput.current?.click()} onFile={(file) => void upload(file)} onRecording={(recording) => void openRecording(recording)} />
        )}
        <input ref={fileInput} type="file" accept=".csv,.parquet" className="sr-only" aria-label="Upload a CSV or Parquet file" onChange={(event) => { const file = event.target.files?.[0]; if (file) void upload(file); event.target.value = ""; }} />
        {session?.summary && session.catalog.dataset_kind === "upload" && !hasRun && <SchemaInspector summary={session.summary} onConfirmRoles={confirmRoles} />}
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
        {hasRun && <RunProgress run={run} events={recordedEvents} replay={replay} uiMode={uiMode} showTrace={showTrace} onToggleTrace={() => setShowTrace((value) => !value)} running={Boolean(runId) && !finished} />}
        <ReportWorkspace comparison={comparison} run={run} aiRun={aiRun} aiError={aiError} config={config} deterministicPending={Boolean(runId) && !finished} onShowWork={(side, findingId) => setTarget({ side, findingId })} />
      </div>
      {!hasRun && <RightRail catalog={catalog} metrics={session?.metrics ?? []} usedMetrics={[]} results={{}} tasks={[]} runMetrics={null} onOpenResult={() => undefined} />}
      {finding && provenanceRun && <ProvenanceDrawer finding={finding} results={provenanceRun.results} tasks={provenanceRun.tasks} trace={provenanceRun.mcp_trace} onClose={() => setTarget(null)} />}
      {target && !finding && (
        <div className="drawer" role="dialog" aria-label="Provenance unavailable">
          <div className="drawer-head"><h3>Provenance unavailable</h3><button type="button" onClick={() => setTarget(null)}>Close</button></div>
          <p>This finding is no longer part of the {target.side === "ai" ? "AI" : "deterministic"} run, so its working cannot be shown. Re-run the question to inspect it.</p>
        </div>
      )}
    </AppShell>
  );
}
