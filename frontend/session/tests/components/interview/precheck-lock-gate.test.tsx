import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/hooks/use-devtools-lockout', () => ({ useDevtoolsLockout: vi.fn() }))
vi.mock('@/app/interview/[token]/FullscreenLockGate', () => ({
  FullscreenLockGate: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="fs-gate">{children}</div>
  ),
}))

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

  it('wraps children in the fullscreen gate by default', () => {
    mockDevtools.mockReturnValue(false)
    render(
      <PreCheckLockGate>
        <div>body</div>
      </PreCheckLockGate>,
    )
    expect(screen.getByTestId('fs-gate')).toBeInTheDocument()
  })

  it('skips the fullscreen gate when enforceFullscreen is false (intro step)', () => {
    mockDevtools.mockReturnValue(false)
    render(
      <PreCheckLockGate enforceFullscreen={false}>
        <div>intro body</div>
      </PreCheckLockGate>,
    )
    expect(screen.queryByTestId('fs-gate')).not.toBeInTheDocument()
    expect(screen.getByText('intro body')).toBeInTheDocument()
  })
})
