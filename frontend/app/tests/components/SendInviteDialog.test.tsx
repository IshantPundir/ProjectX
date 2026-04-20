import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { SendInviteDialog } from '@/app/(dashboard)/candidates/SendInviteDialog'

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

describe('SendInviteDialog', () => {
  it('renders role + stage context and OTP toggle', () => {
    renderWithClient(
      <SendInviteDialog
        open={true}
        onOpenChange={() => {}}
        candidateId="c1"
        assignmentId="a1"
        candidateName="Alice"
        jobTitle="Engineer"
        stageName="AI Interview"
      />,
    )
    expect(screen.getByText(/Alice/)).toBeInTheDocument()
    expect(screen.getByText(/Engineer/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox')).toBeInTheDocument()
  })
})
