import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithProviders } from '../../_utils/render'
import { VerifyStage } from '@/app/interview/[token]/VerifyStage'
import { candidateSessionApi } from '@/lib/api/candidate-session'

afterEach(() => vi.restoreAllMocks())

describe('VerifyStage', () => {
  it('requests a code then verifies the 6-digit input', async () => {
    const user = userEvent.setup()
    const reqSpy = vi.spyOn(candidateSessionApi, 'requestOtp').mockResolvedValue(undefined)
    const verSpy = vi.spyOn(candidateSessionApi, 'verifyOtp').mockResolvedValue(undefined)
    renderWithProviders(<VerifyStage token="tok" otpIssuedAt={null} />)

    await user.click(screen.getByRole('button', { name: /send code/i }))
    await waitFor(() => expect(reqSpy).toHaveBeenCalledTimes(1))

    await user.type(screen.getByPlaceholderText('123456'), '123456')
    await user.click(screen.getByRole('button', { name: /^verify$/i }))
    await waitFor(() => expect(verSpy).toHaveBeenCalledWith('tok', { code: '123456' }))
  })

  it('disables Verify until 6 digits are entered', async () => {
    const user = userEvent.setup()
    renderWithProviders(<VerifyStage token="tok" otpIssuedAt={null} />)
    const verify = screen.getByRole('button', { name: /^verify$/i })
    expect(verify).toBeDisabled()
    await user.type(screen.getByPlaceholderText('123456'), '12345')
    expect(verify).toBeDisabled()
  })
})
