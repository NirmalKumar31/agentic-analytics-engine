/** Thin fetch wrapper. Every error surfaces as a readable message. */

import type { RunPayload, ServerConfig, SessionPayload } from './types'

export class ApiError extends Error {
  readonly status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, init)
  } catch {
    throw new ApiError('Could not reach the server.', 0)
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status}).`
    try {
      const body = (await response.json()) as { detail?: unknown; error?: string }
      if (typeof body.detail === 'string') detail = body.detail
      else if (Array.isArray(body.detail) && body.detail.length > 0) {
        const first = body.detail[0] as { msg?: string }
        detail = first?.msg ?? detail
      } else if (typeof body.error === 'string') detail = body.error
    } catch {
      /* the body was not JSON; the status message stands */
    }
    throw new ApiError(detail, response.status)
  }
  return (await response.json()) as T
}

export const api = {
  config: () => request<ServerConfig>('/api/config'),
  openDemo: () => request<SessionPayload>('/api/datasets/demo', { method: 'POST' }),
  upload: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return request<SessionPayload>('/api/datasets/upload', { method: 'POST', body })
  },
  startAnalysis: (sessionId: string, question: string) =>
    request<{ run_id: string }>('/api/analyses', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, question }),
    }),
  run: (runId: string) => request<RunPayload>(`/api/analyses/${encodeURIComponent(runId)}`),
  recording: (id: string) => request<RunPayload>(`/api/recordings/${encodeURIComponent(id)}`),
}
