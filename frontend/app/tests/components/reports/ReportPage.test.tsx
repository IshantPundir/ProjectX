import { afterEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { renderWithProviders } from '@/tests/_utils/render'

vi.mock('@/lib/auth/tokens', () => ({ getFreshSupabaseToken: vi.fn().mockResolvedValue('tok') }))
vi.mock('next/navigation', () => ({
  useParams: () => ({ sessionId: 's1' }),
  useSearchParams: () => new URLSearchParams('candidateId=c1&candidateName=Anand&title=Senior%20Python%20Engineer'),
}))
vi.mock('@/lib/hooks/use-me', () => ({ useMe: () => ({ data: { is_super_admin: false } }) }))

import ReportPage from '@/app/(dashboard)/reports/session/[sessionId]/page'

const READY = {
  verdict: 'reject', verdict_reason: 'failed', overall_score: 36, overall_coverage: 0.7,
  overall_confidence: 'medium', dimension_scores: {}, knockout_results: [], signal_scorecards: [],
  question_scorecards: [], summary: { headline: 'h', strengths: [], gaps: [], rationale: '' },
  status: 'ready', id: 'r1', session_id: 's1', version: 1, scoring_manifest: null, human_decision: null,
}

afterEach(() => vi.unstubAllGlobals())

describe('ReportPage', () => {
  it('renders the ready report', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => READY } as Response))
    renderWithProviders(<ReportPage />)
    expect((await screen.findAllByText('Reject')).length).toBeGreaterThan(0)
  })

  it('renders the empty state on 404', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404, json: async () => ({ detail: 'none' }) } as Response))
    renderWithProviders(<ReportPage />)
    await waitFor(() => expect(screen.getByText(/no evaluation yet/i)).toBeInTheDocument())
  })
})
