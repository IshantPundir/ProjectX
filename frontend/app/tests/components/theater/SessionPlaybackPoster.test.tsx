import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { SessionPlayback } from '@/components/dashboard/reports/SessionPlayback'
import type { ReportRead } from '@/lib/api/reports'

const report = { verdict: 'reject' } as unknown as ReportRead

describe('SessionPlayback poster', () => {
  it('calls onOpen when the play poster is clicked', async () => {
    const onOpen = vi.fn()
    render(<SessionPlayback report={report} onOpen={onOpen} />)
    await userEvent.click(screen.getByRole('button', { name: /play|review/i }))
    expect(onOpen).toHaveBeenCalledTimes(1)
  })
})
