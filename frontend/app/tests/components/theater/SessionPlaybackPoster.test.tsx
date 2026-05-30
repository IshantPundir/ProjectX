import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { SessionPlayback } from '@/components/dashboard/reports/SessionPlayback'
import type { ReportRead } from '@/lib/api/reports'

vi.mock('@/components/dashboard/reports/theater/ReviewTheater', () => ({
  ReviewTheater: ({ open }: { open: boolean }) =>
    open ? <div data-testid="theater-open" /> : null,
}))

const report = { session_id: 's1', verdict: 'reject', questions: [], scores: {},
  decision: { headline: '', why_positive: { title: '', body: '' }, why_negative: { title: '', body: '' } } } as unknown as ReportRead

describe('SessionPlayback poster', () => {
  it('renders a play button and opens the theater on click', async () => {
    render(<SessionPlayback report={report} candidateName="Aarav" subtitle="Jr. FDE" />)
    expect(screen.queryByTestId('theater-open')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /play|review/i }))
    expect(screen.getByTestId('theater-open')).toBeInTheDocument()
  })
})
