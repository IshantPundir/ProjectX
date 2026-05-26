import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

import { SessionRow } from '@/app/(dashboard)/candidates/[candidateId]/CandidateSessionsTab'
import type { SessionDetail } from '@/lib/api/scheduler'

vi.mock('@/lib/hooks/use-resend-invite', () => ({ useResendInvite: () => ({ mutate: vi.fn(), isPending: false }) }))
vi.mock('@/lib/hooks/use-revoke-invite', () => ({ useRevokeInvite: () => ({ mutate: vi.fn(), isPending: false }) }))

const make = (state: SessionDetail['state']): SessionDetail => ({
  id: 'sess1', assignment_id: 'a1', stage_id: 'st1', stage_name: 'AI Screening', state,
  state_changed_at: '', otp_required: false, consent_recorded_at: null, scheduled_for: null,
  started_at: null, completed_at: null, created_at: new Date().toISOString(),
})

function rowInTable(session: SessionDetail) {
  return render(
    <table><tbody>
      <SessionRow session={session} candidateId="c1" jobTitle="Senior Python Engineer" />
    </tbody></table>,
  )
}

describe('SessionRow report link', () => {
  it('shows a View report link for completed sessions', () => {
    rowInTable(make('completed'))
    const link = screen.getByRole('link', { name: /view report/i })
    expect(link).toHaveAttribute('href', expect.stringContaining('/reports/session/sess1'))
    expect(link).toHaveAttribute('href', expect.stringContaining('candidateId=c1'))
  })
  it('does not show a View report link before completion', () => {
    rowInTable(make('active'))
    expect(screen.queryByRole('link', { name: /view report/i })).not.toBeInTheDocument()
  })
})
