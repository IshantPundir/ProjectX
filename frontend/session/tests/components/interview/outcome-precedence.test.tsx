/**
 * Composition test: OutcomePrecedenceController wires useSessionOutcome (LK
 * attribute path) and useSessionStateFallback (HTTP poll path) into a single
 * precedence rule that renders <SessionErrorScreen> on the first error signal
 * from either source.
 *
 * Both hooks are mocked at the module boundary so this test is fully
 * deterministic with no LiveKit, network, or timer involvement.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'

// Mock both hooks before importing the component under test.
vi.mock('@/components/interview/app/hooks/use-session-outcome', () => ({
  useSessionOutcome: vi.fn(),
}))
vi.mock('@/components/interview/app/hooks/use-session-state-fallback', () => ({
  useSessionStateFallback: vi.fn(),
}))

import { useSessionOutcome } from '@/components/interview/app/hooks/use-session-outcome'
import { useSessionStateFallback } from '@/components/interview/app/hooks/use-session-state-fallback'
import { OutcomePrecedenceController } from '@/components/interview/app/app'

const mockedOutcome = vi.mocked(useSessionOutcome)
const mockedFallback = vi.mocked(useSessionStateFallback)

beforeEach(() => {
  mockedOutcome.mockReset()
  mockedFallback.mockReset()
})

function renderController() {
  return render(
    <OutcomePrecedenceController token="tok-test" sessionId="sess-123">
      <div>live-session-ui</div>
    </OutcomePrecedenceController>,
  )
}

describe('outcome precedence', () => {
  it('LK attribute wins: renders SessionErrorScreen with null errorCode when LK surfaces error', async () => {
    mockedOutcome.mockReturnValue('error')
    mockedFallback.mockReturnValue(null)

    renderController()

    // Generic copy because errorCode=null on the LK-attribute-only path.
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument()
    // Session reference is shown for support correlation.
    expect(screen.getByText(/sess-123/)).toBeInTheDocument()
    // Live session UI is NOT shown.
    expect(screen.queryByText('live-session-ui')).not.toBeInTheDocument()
  })

  it('HTTP fallback wins: renders SessionErrorScreen with the polled error_code when LK never arrives', async () => {
    mockedOutcome.mockReturnValue(null)
    mockedFallback.mockReturnValue({
      state: 'error',
      error_code: 'engine_session_config_invalid',
      state_changed_at: '2026-05-16T12:00:00Z',
    })

    renderController()

    // Code-specific copy from session-error-messages.ts.
    expect(screen.getByText(/configuration issue/i)).toBeInTheDocument()
    expect(screen.getByText(/sess-123/)).toBeInTheDocument()
    expect(screen.queryByText('live-session-ui')).not.toBeInTheDocument()
  })

  it('prefers the polled error_code when both LK attribute and HTTP poll surface error simultaneously', () => {
    mockedOutcome.mockReturnValue('error')
    mockedFallback.mockReturnValue({
      state: 'error',
      error_code: 'engine_session_config_invalid',
      state_changed_at: '2026-05-16T12:00:00Z',
    })

    renderController()

    // Should show code-specific copy (not the generic fallback).
    expect(screen.getByText(/configuration issue/i)).toBeInTheDocument()
    expect(screen.queryByText('live-session-ui')).not.toBeInTheDocument()
  })

  it('renders children when neither path signals error', () => {
    mockedOutcome.mockReturnValue(null)
    mockedFallback.mockReturnValue(null)

    renderController()

    expect(screen.getByText('live-session-ui')).toBeInTheDocument()
    expect(screen.queryByText(/something went wrong/i)).not.toBeInTheDocument()
  })

  it('renders children when LK outcome is non-error (e.g. completed)', () => {
    mockedOutcome.mockReturnValue('completed')
    mockedFallback.mockReturnValue(null)

    renderController()

    expect(screen.getByText('live-session-ui')).toBeInTheDocument()
  })

  it('renders children when HTTP poll returns non-error state (e.g. active)', () => {
    mockedOutcome.mockReturnValue(null)
    mockedFallback.mockReturnValue({
      state: 'active',
      error_code: null,
      state_changed_at: '2026-05-16T12:00:00Z',
    })

    renderController()

    expect(screen.getByText('live-session-ui')).toBeInTheDocument()
  })
})
