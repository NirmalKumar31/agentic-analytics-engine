import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { ActivityLog } from './components/ActivityLog'
import { ExecutionFlow } from './components/ExecutionFlow'
import { ProvenanceDrawer } from './components/ProvenanceDrawer'
import { ReportView } from './components/ReportView'
import { RightRail } from './components/RightRail'
import { ApiError, api } from './lib/api'
import type {
  MetricInfo,
  RecordingSummary,
  RunEvent,
  RunPayload,
  ServerConfig,
  SessionPayload,
} from './lib/types'
import { useRunEvents } from './lib/useRunEvents'

type Stage = 'dataset' | 'ask' | 'running' | 'report'

export function App() {
  const [config, setConfig] = useState<ServerConfig | null>(null)
  const [configError, setConfigError] = useState<string | null>(null)
  const [session, setSession] = useState<SessionPayload | null>(null)
  const [question, setQuestion] = useState('')
  const [runId, setRunId] = useState<string | null>(null)
  const [run, setRun] = useState<RunPayload | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showTrace, setShowTrace] = useState(false)
  const [openFinding, setOpenFinding] = useState<string | null>(null)
  const [replay, setReplay] = useState<RecordingSummary | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  const { events, finished } = useRunEvents(runId)

  useEffect(() => {
    api
      .config()
      .then(setConfig)
      .catch((e: unknown) =>
        setConfigError(e instanceof ApiError ? e.message : 'Could not load server configuration.'),
      )
  }, [])

  // A live run's payload is fetched once its event stream ends.
  useEffect(() => {
    if (!runId || !finished) return
    api
      .run(runId)
      .then(setRun)
      .catch((e: unknown) =>
        setError(e instanceof ApiError ? e.message : 'Could not load the finished run.'),
      )
  }, [runId, finished])

  const recordedEvents: RunEvent[] = useMemo(
    () => (replay && run ? run.events : events),
    [replay, run, events],
  )

  const stage: Stage = run
    ? 'report'
    : runId
      ? 'running'
      : session || replay
        ? 'ask'
        : 'dataset'

  const openDemo = useCallback(async () => {
    setBusy(true)
    setError(null)
    try {
      setReplay(null)
      setRun(null)
      setRunId(null)
      setSession(await api.openDemo())
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not open the demo dataset.')
    } finally {
      setBusy(false)
    }
  }, [])

  const upload = useCallback(async (file: File) => {
    setBusy(true)
    setError(null)
    try {
      setReplay(null)
      setRun(null)
      setRunId(null)
      setSession(await api.upload(file))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'The upload was rejected.')
    } finally {
      setBusy(false)
    }
  }, [])

  const openRecording = useCallback(async (summary: RecordingSummary) => {
    setBusy(true)
    setError(null)
    try {
      const payload = await api.recording(summary.recording_id)
      setReplay(summary)
      setSession(null)
      setRunId(null)
      setRun(payload)
      setQuestion(payload.question)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not load the recording.')
    } finally {
      setBusy(false)
    }
  }, [])

  const ask = useCallback(async () => {
    if (!session || !question.trim()) return
    setBusy(true)
    setError(null)
    setRun(null)
    try {
      const { run_id } = await api.startAnalysis(session.session_id, question.trim())
      setRunId(run_id)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'The analysis could not be started.')
    } finally {
      setBusy(false)
    }
  }, [session, question])

  const reset = useCallback(() => {
    setRun(null)
    setRunId(null)
    setReplay(null)
    setOpenFinding(null)
  }, [])

  const finding = run?.findings.find((f) => f.finding_id === openFinding) ?? null
  const catalog = run?.dataset ?? session?.catalog ?? null
  const metrics: MetricInfo[] = session?.metrics ?? []
  const usedMetrics = useMemo(
    () => [...new Set((run?.findings ?? []).flatMap((f) => f.metric_ids))],
    [run],
  )

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <Mark />
          <div>
            Agentic Analytics Engine
            <br />
            <small>bounded analysis · MCP tools · provenance on every number</small>
          </div>
        </div>
        <div className="topbar-spacer" />
        {config && (
          <span className={`mode-pill ${config.live_analytics_enabled ? 'live' : 'replay'}`}>
            <span className="dot" />
            {config.live_analytics_enabled ? 'live analysis' : 'recorded runs'} · {config.provider_mode}
          </span>
        )}
        {(run || runId) && (
          <button className="btn ghost small" onClick={reset}>
            Start over
          </button>
        )}
      </header>

      <nav className="steps" aria-label="Progress">
        <Step index={1} label="Dataset" state={stage === 'dataset' ? 'active' : 'done'} />
        <Step
          index={2}
          label="Ask"
          state={stage === 'ask' ? 'active' : stage === 'dataset' ? 'idle' : 'done'}
        />
        <Step
          index={3}
          label="Analyse"
          state={stage === 'running' ? 'active' : stage === 'report' ? 'done' : 'idle'}
        />
        <Step index={4} label="Verify" state={stage === 'report' ? 'done' : 'idle'} />
        <Step index={5} label="Report" state={stage === 'report' ? 'active' : 'idle'} />
      </nav>

      <main className="main">
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
              onRecording={openRecording}
            />
          )}

          <input
            ref={fileInput}
            type="file"
            accept=".csv,.parquet"
            className="sr-only"
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) void upload(file)
              e.target.value = ''
            }}
          />

          {session && !run && !runId && (
            <AskPanel
              config={config}
              question={question}
              setQuestion={setQuestion}
              onAsk={ask}
              busy={busy}
            />
          )}

          {(runId || run) && (
            <section className="panel">
              <div className="panel-head">
                <h2>Analysis</h2>
                <span className="spacer" />
                {replay && <span className="small dim">recorded run · {replay.recording_id}</span>}
              </div>
              <ExecutionFlow events={recordedEvents} />
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
            <div className="notice warn">The run stopped early: {run.stopped_reason}</div>
          )}

          {run && (
            <ReportView
              question={run.question}
              report={run.report}
              findings={run.findings}
              rejected={run.rejected}
              charts={run.charts}
              results={run.results}
              onShowWork={setOpenFinding}
            />
          )}
        </div>

        <RightRail
          catalog={catalog}
          metrics={metrics}
          usedMetrics={usedMetrics}
          results={run?.results ?? {}}
          tasks={run?.tasks ?? []}
          runMetrics={run?.metrics ?? null}
          onOpenResult={(resultId) => {
            const match = run?.findings.find((f) => f.result_ids.includes(resultId))
            if (match) setOpenFinding(match.finding_id)
          }}
        />
      </main>

      {finding && run && (
        <ProvenanceDrawer
          finding={finding}
          results={run.results}
          tasks={run.tasks}
          trace={run.mcp_trace}
          datasetFingerprint={run.dataset.dataset_fingerprint}
          onClose={() => setOpenFinding(null)}
        />
      )}
    </div>
  )
}

function DatasetPanel({
  config,
  session,
  replay,
  busy,
  onDemo,
  onUploadClick,
  onRecording,
}: {
  config: ServerConfig
  session: SessionPayload | null
  replay: RecordingSummary | null
  busy: boolean
  onDemo: () => void
  onUploadClick: () => void
  onRecording: (r: RecordingSummary) => void
}) {
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
            aria-pressed={Boolean(session && session.catalog.dataset_kind === 'demo')}
          >
            <strong>Commerce demo warehouse</strong>
            <span>
              Seven related tables, two years, deterministic. Generated locally with a fixed seed.
            </span>
          </button>
          <button
            className="choice"
            onClick={onUploadClick}
            disabled={busy || !config.uploads_enabled}
            aria-pressed={Boolean(session && session.catalog.dataset_kind === 'upload')}
          >
            <strong>Upload CSV or Parquet</strong>
            <span>
              {config.uploads_enabled
                ? `One file, up to ${config.max_upload_mb} MB. Held in memory for the session only.`
                : 'Disabled on this server.'}
            </span>
          </button>
        </div>

        {config.recordings.length > 0 && (
          <>
            <p className="small dim" style={{ margin: '4px 0 0' }}>
              Or open a recorded run — a real run captured end to end, with its
              queries, results and verification decisions intact.
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
                    {recording.demonstrates} · {recording.findings} published,{' '}
                    {recording.rejected} withheld, {recording.mcp_tool_calls} MCP calls
                  </small>
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    </section>
  )
}

function AskPanel({
  config,
  question,
  setQuestion,
  onAsk,
  busy,
}: {
  config: ServerConfig | null
  question: string
  setQuestion: (q: string) => void
  onAsk: () => void
  busy: boolean
}) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Ask</h2>
      </div>
      <div className="panel-body stack">
        <textarea
          className="field"
          rows={3}
          value={question}
          maxLength={500}
          placeholder="Why did gross margin fall in Q3 2025?"
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) onAsk()
          }}
          aria-label="Business question"
        />
        <div className="row">
          <button className="btn primary" onClick={onAsk} disabled={busy || !question.trim()}>
            {busy ? 'Starting…' : 'Run analysis'}
          </button>
          <span className="small dim">{question.length}/500 · ⌘↵ to run</span>
        </div>
        {config && config.demo_questions.length > 0 && (
          <div className="example-list">
            {config.demo_questions.map((item) => (
              <button className="example" key={item.id} onClick={() => setQuestion(item.question)}>
                {item.question}
                <small>{item.why}</small>
              </button>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}

function Step({
  index,
  label,
  state,
}: {
  index: number
  label: string
  state: 'idle' | 'active' | 'done'
}) {
  return (
    <div className="step" data-state={state}>
      <span className="step-index">{state === 'done' ? '✓' : index}</span>
      {label}
    </div>
  )
}

function Mark() {
  return (
    <svg className="brand-mark" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="1" y="1" width="22" height="22" rx="5" stroke="var(--border-strong)" />
      <path d="M5 16.5 L9.5 10 L13.5 13.5 L19 6.5" stroke="var(--accent)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="9.5" cy="10" r="1.6" fill="var(--supported)" />
      <circle cx="19" cy="6.5" r="1.6" fill="var(--supported)" />
    </svg>
  )
}
