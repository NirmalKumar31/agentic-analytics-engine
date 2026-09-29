import { useEffect, useRef, useState } from 'react'

import type { RunEvent } from './types'

/**
 * Subscribes to a run's server-sent event stream.
 *
 * Events are de-duplicated by sequence number because the server replays
 * history on connect, so a reconnect does not double the timeline.
 */
export function useRunEvents(runId: string | null): {
  events: RunEvent[]
  finished: boolean
  reset: () => void
} {
  const [events, setEvents] = useState<RunEvent[]>([])
  const [finished, setFinished] = useState(false)
  const seen = useRef<Set<number>>(new Set())

  useEffect(() => {
    seen.current = new Set()
    setEvents([])
    setFinished(false)
    if (!runId) return

    const source = new EventSource(`/api/analyses/${encodeURIComponent(runId)}/events`)

    const onMessage = (event: MessageEvent<string>) => {
      try {
        const parsed = JSON.parse(event.data) as RunEvent
        if (typeof parsed.seq !== 'number' || seen.current.has(parsed.seq)) return
        seen.current.add(parsed.seq)
        setEvents((current) => [...current, parsed])
        // `run_cancelled` is terminal too: the dataset went away, so no
        // further event is coming and the stream must stop being awaited.
        if (
          parsed.type === 'run_completed' ||
          parsed.type === 'run_failed' ||
          parsed.type === 'run_cancelled'
        ) {
          setFinished(true)
        }
      } catch {
        /* a malformed frame is dropped rather than breaking the stream */
      }
    }

    source.addEventListener('message', onMessage as EventListener)
    // The server labels each frame with its event type, so every known type
    // needs its own listener; `message` alone would miss all of them.
    for (const type of EVENT_TYPES) {
      source.addEventListener(type, onMessage as EventListener)
    }
    source.addEventListener('stream_end', () => {
      setFinished(true)
      source.close()
    })
    source.onerror = () => {
      // EventSource retries on its own; the stream ends when the run does.
      if (source.readyState === EventSource.CLOSED) setFinished(true)
    }

    return () => source.close()
  }, [runId])

  return {
    events,
    finished,
    reset: () => {
      seen.current = new Set()
      setEvents([])
      setFinished(false)
    },
  }
}

const EVENT_TYPES = [
  'run_started',
  'dataset_loaded',
  'question_analyzed',
  'plan_generated',
  'analysis_task_started',
  'mcp_tool_called',
  'mcp_tool_completed',
  'mcp_tool_failed',
  'analysis_task_completed',
  'analysis_task_failed',
  'finding_proposed',
  'finding_verified',
  'finding_rejected',
  'followup_round_started',
  'chart_created',
  'chart_rejected',
  'report_started',
  'report_completed',
  'budget_exceeded',
  'run_completed',
  'run_failed',
  'run_cancelled',
] as const
