import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { OtpStep } from '@/app/interview/[token]/OtpStep'

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

describe('OtpStep', () => {
  it('renders Send code button in idle state', () => {
    renderWithClient(<OtpStep token="t" otpIssuedAt={null} />)
    expect(
      screen.getByRole('button', { name: /Send code/i }),
    ).toBeInTheDocument()
  })

  it('Verify button is disabled until 6 digits entered', async () => {
    const user = userEvent.setup()
    renderWithClient(<OtpStep token="t" otpIssuedAt={null} />)
    const verifyBtn = screen.getByRole('button', { name: /Verify/i })
    expect(verifyBtn).toBeDisabled()
    const input = screen.getByRole('textbox')
    await user.type(input, '12345')
    expect(verifyBtn).toBeDisabled()
    await user.type(input, '6')
    expect(verifyBtn).toBeEnabled()
  })
})
