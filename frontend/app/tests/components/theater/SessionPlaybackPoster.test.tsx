import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { SessionPlayback } from '@/components/dashboard/reports/SessionPlayback'
import type { ReportRead } from '@/lib/api/reports'

const report = { verdict: 'reject', questions: [] } as unknown as ReportRead

describe('SessionPlayback poster', () => {
  it('calls onOpen when the play poster is clicked', async () => {
    const onOpen = vi.fn()
    render(<SessionPlayback report={report} onOpen={onOpen} />)
    await userEvent.click(screen.getByRole('button', { name: /play|review/i }))
    expect(onOpen).toHaveBeenCalledTimes(1)
  })

  it('uses a question thumbnail frame as the poster image when available', () => {
    const withThumb = {
      verdict: 'advance',
      questions: [
        { thumbnail_url: null },
        { thumbnail_url: 'https://r2.example/frame-q2.jpg' },
      ],
    } as unknown as ReportRead
    const { container } = render(<SessionPlayback report={withThumb} onOpen={() => {}} />)
    const img = container.querySelector('img')
    expect(img).not.toBeNull()
    expect(img?.getAttribute('src')).toContain('frame-q2.jpg')
  })
})
