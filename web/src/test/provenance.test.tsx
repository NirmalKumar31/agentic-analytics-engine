/**
 * Whose working does "Show work" show?
 *
 * Compare Both runs two independent analyses, and each assigns finding
 * ids within itself. `f1` on the AI side and `f1` on the deterministic
 * side are different claims about different results. The app kept a bare
 * id and resolved the drawer from the deterministic run, so clicking the
 * AI pane's button opened deterministic evidence for an AI claim -- or,
 * when the ids happened not to collide, opened nothing at all.
 *
 * These fixtures collide the ids deliberately. A test using distinct ids
 * would pass against the broken build.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { ReportView } from '../components/ReportView'

const SHARED_ID = 'f1'

function finding(text: string, resultId: string) {
  return {
    finding_id: SHARED_ID,
    text,
    kind: 'calculated_fact' as const,
    task_id: 't1',
    result_ids: [resultId],
    evidence_cells: [{ result_id: resultId, row: 0, column: 'value', value: 1, label: null }],
    metric_ids: [],
    claimed_change: null,
    verification_status: 'supported' as const,
    verifier_reason: 'entailed by the cited result',
    verifier_rule: 'critic',
    answers_question: true,
    numeric_check: { ok: true, reason: 'every stated number traces to a cited result', checks: [] },
  }
}

function report(text: string) {
  return {
    question: 'q',
    executive_summary: text,
    key_findings: [],
    sections: [],
    limitations: [],
    next_questions: [],
  }
}

describe('Show work, across two runs', () => {
  it('reports the id the clicked pane owns, not a global one', async () => {
    // Each pane is given its own handler; the app is what routes them to
    // the right run. If both panes shared one handler -- which is what
    // the defect was -- this test could not tell them apart.
    const deterministicClicks: string[] = []
    const aiClicks: string[] = []

    const { rerender } = render(
      <ReportView
        question="q"
        report={report('deterministic')}
        findings={[finding('Deterministic says 10.', 'res_det')]}
        rejected={[]}
        charts={[]}
        results={{}}
        onShowWork={(id) => deterministicClicks.push(id)}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: /show work/i }))
    expect(deterministicClicks).toEqual([SHARED_ID])

    rerender(
      <ReportView
        question="q"
        report={report('ai')}
        findings={[finding('AI says 20.', 'res_ai')]}
        rejected={[]}
        charts={[]}
        results={{}}
        onShowWork={(id) => aiClicks.push(id)}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: /show work/i }))
    expect(aiClicks).toEqual([SHARED_ID])

    // The id alone is identical on both sides. Only the side tells them
    // apart, which is why the app must carry it.
    expect(aiClicks[0]).toBe(deterministicClicks[0])
  })
})
