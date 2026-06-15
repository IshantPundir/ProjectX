import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { ConsentDialog } from '@/app/interview/[token]/ConsentDialog'

describe('ConsentDialog', () => {
  it('opens the full consent text on trigger click', async () => {
    const user = userEvent.setup()
    render(<ConsentDialog consentText="You consent to recording and AI evaluation." />)
    expect(screen.queryByText(/consent to recording/i)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /privacy & consent/i }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText(/consent to recording/i)).toBeInTheDocument()
  })
})
