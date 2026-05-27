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
  verdict: 'reject',
  verdict_reason: 'failed',
  overall_score: 36,
  overall_coverage: 0.7,
  overall_confidence: 'medium',
  decision: {
    headline: 'Not recommended for this role.',
    why_positive: { title: 'Some positives', body: 'Met the experience bar.' },
    why_negative: { title: 'Core concerns', body: 'Technical depth was not demonstrated.' },
  },
  scores: {
    overall: { score: 36, tier_label: 'Below Bar', tone: 'danger', confidence: 'medium', coverage: 0.7 },
  },
  quick_summary: 'Candidate did not meet the requirements.',
  strengths: [],
  concerns: [],
  questions: [],
  methodology: { note: 'Interview completed normally.', charity_flags: [] },
  signal_assessments: [],
  status: 'ready',
  id: 'r1',
  session_id: 's1',
  version: 1,
  engine_version: 'v2',
  scoring_manifest: null,
  human_decision: null,
  generated_at: '2026-05-28T00:00:00Z',
}

afterEach(() => vi.unstubAllGlobals())

describe('ReportPage', () => {
  it('renders the ready report', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => READY } as Response))
    renderWithProviders(<ReportPage />)
    expect((await screen.findAllByText('Not Recommended')).length).toBeGreaterThan(0)
  })

  it('renders the empty state on 404', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404, json: async () => ({ detail: 'none' }) } as Response))
    renderWithProviders(<ReportPage />)
    await waitFor(() => expect(screen.getByText(/no evaluation yet/i)).toBeInTheDocument())
  })
})
