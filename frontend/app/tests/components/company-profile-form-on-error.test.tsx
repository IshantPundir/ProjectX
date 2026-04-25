import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { CompanyProfileForm } from '@/components/dashboard/company-profile-form'

const VALID_VALUES = {
  about:
    'We build distributed log processing for real-time analytics at petabyte scale.',
  industry: 'saas_enterprise_software' as const,
  company_stage: 'series_a_b' as const,
  hiring_bar:
    'Pragmatic engineers comfortable with ambiguity and operational ownership.',
}

describe('CompanyProfileForm onError prop', () => {
  it('delegates thrown errors to onError when provided (no rethrow)', async () => {
    const error = new Error('boom')
    const onSubmit = vi.fn().mockRejectedValueOnce(error)
    const onError = vi.fn()

    render(
      <CompanyProfileForm
        initialValue={VALID_VALUES}
        onSubmit={onSubmit}
        onError={onError}
        submitLabel="Save"
      />,
    )

    const button = screen.getByRole('button', { name: /Save/ })
    // RHF validates defaults asynchronously — wait for isValid → submit enabled.
    await waitFor(() => expect(button).not.toBeDisabled())
    fireEvent.click(button)

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(onError).toHaveBeenCalledTimes(1))
    expect(onError.mock.calls[0][0]).toBe(error)
    // Second arg is the form instance — at minimum has setError.
    expect(typeof onError.mock.calls[0][1].setError).toBe('function')
  })

  it('without onError, prior behaviour is preserved (onSubmit is called once)', async () => {
    const error = new Error('boom')
    const onSubmit = vi.fn().mockRejectedValueOnce(error)

    // Without onError, RHF surfaces the rejection as unhandled — swallow
    // it for this test only so vitest doesn't fail the run.
    const swallowDom = (e: PromiseRejectionEvent | Event) => {
      if ('preventDefault' in e) e.preventDefault()
    }
    const swallowNode = () => {
      // intentional no-op — see comment above
    }
    window.addEventListener('unhandledrejection', swallowDom)
    process.on('unhandledRejection', swallowNode)

    try {
      render(
        <CompanyProfileForm
          initialValue={VALID_VALUES}
          onSubmit={onSubmit}
          submitLabel="Save"
        />,
      )

      const button = screen.getByRole('button', { name: /Save/ })
      await waitFor(() => expect(button).not.toBeDisabled())
      fireEvent.click(button)
      await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
      // No assertion beyond the call — onError absent → form does not
      // intercept the rejection (RHF surfaces it internally).
    } finally {
      window.removeEventListener('unhandledrejection', swallowDom)
      process.off('unhandledRejection', swallowNode)
    }
  })
})
