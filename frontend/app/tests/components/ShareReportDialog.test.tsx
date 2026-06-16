import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const shareMock = vi.fn()
vi.mock('@/lib/hooks/use-share-report', () => ({
  useShareReport: () => ({ mutateAsync: shareMock, isPending: false }),
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

import { ShareReportDialog } from '@/components/dashboard/reports/ShareReportDialog'

describe('ShareReportDialog', () => {
  it('rejects an invalid email and does not call share', async () => {
    render(<ShareReportDialog sessionId="s1" open onOpenChange={() => {}} />)
    fireEvent.change(screen.getByLabelText(/recipient email/i), { target: { value: 'bad' } })
    fireEvent.click(screen.getByRole('button', { name: /send/i }))
    await waitFor(() => expect(screen.getByText(/valid email/i)).toBeInTheDocument())
    expect(shareMock).not.toHaveBeenCalled()
  })

  it('calls share with a valid email', async () => {
    shareMock.mockResolvedValue({ share_id: 'x', status: 'pending' })
    render(<ShareReportDialog sessionId="s1" open onOpenChange={() => {}} />)
    fireEvent.change(screen.getByLabelText(/recipient email/i), { target: { value: 'client@acme.com' } })
    fireEvent.click(screen.getByRole('button', { name: /send/i }))
    await waitFor(() => expect(shareMock).toHaveBeenCalledWith('client@acme.com'))
  })
})
