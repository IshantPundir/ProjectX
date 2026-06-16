import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/hooks/use-devtools-lockout', () => ({ useDevtoolsLockout: vi.fn() }))

import { PreCheckLockGate } from '@/app/interview/[token]/PreCheckLockGate'
import { useDevtoolsLockout } from '@/hooks/use-devtools-lockout'

const mockDevtools = vi.mocked(useDevtoolsLockout)

afterEach(() => vi.restoreAllMocks())

describe('PreCheckLockGate', () => {
  it('renders children and no devtools overlay when devtools is closed', () => {
    mockDevtools.mockReturnValue(false)
    render(
      <PreCheckLockGate>
        <div>pre-check body</div>
      </PreCheckLockGate>,
    )
    expect(screen.getByText('pre-check body')).toBeInTheDocument()
    expect(screen.queryByRole('alertdialog', { name: /developer tools/i })).not.toBeInTheDocument()
  })

  it('shows the devtools blocked overlay when devtools is detected', () => {
    mockDevtools.mockReturnValue(true)
    render(
      <PreCheckLockGate>
        <div>body</div>
      </PreCheckLockGate>,
    )
    expect(screen.getByRole('alertdialog', { name: /developer tools/i })).toBeInTheDocument()
  })
})
