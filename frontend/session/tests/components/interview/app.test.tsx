/**
 * Smoke test for the App entry point's welcome-view branch in start vs
 * rejoin modes. Heavy mocking — the goal is to verify our outcome-routing
 * code, not LiveKit's session lifecycle. Anything below useSession is
 * stubbed out.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

// Declared before vi.mock calls so Vitest's hoisting can reference them
// inside the factory closures at runtime.
const useSessionCalls: Array<{ tokenSource: unknown; options: unknown }> = []

vi.mock('@livekit/components-react', () => ({
  useSession: (tokenSource: unknown, options?: unknown) => {
    useSessionCalls.push({ tokenSource, options })
    return {
      start: vi.fn(),
      end: vi.fn(),
      isConnected: false,
      connectionState: 'idle',
      room: undefined,
    }
  },
  useSessionContext: () => ({
    isConnected: false,
    connectionState: 'idle',
    start: vi.fn(),
    end: vi.fn(),
  }),
  useRemoteParticipants: () => [],
  useChat: () => ({ chatMessages: [], send: vi.fn() }),
  SessionProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  RoomAudioRenderer: () => null,
}))

const roomConstructorCalls: Array<unknown> = []

vi.mock('livekit-client', () => ({
  TokenSource: { custom: () => ({}) },
  RoomEvent: { Disconnected: 'disconnected' },
  Room: class {
    constructor(options?: unknown) {
      roomConstructorCalls.push(options)
    }
  },
}))

vi.mock('@/components/agents-ui/agent-session-provider', () => ({
  AgentSessionProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

vi.mock('@/components/agents-ui/start-audio-button', () => ({
  StartAudioButton: () => null,
}))

vi.mock('@/components/agents-ui/blocks/agent-session-view-01', () => ({
  AgentSessionView_01: () => <div data-testid="session-view" />,
}))

import { App } from '@/components/interview/app/app'
import { APP_CONFIG_DEFAULTS } from '@/app-config'
import type { PreCheckResponse } from '@/lib/api/candidate-session'

const PRE_CHECK: PreCheckResponse = {
  session_id: 'sess-1',
  company_name: 'Acme',
  job_title: 'Senior Engineer',
  stage_name: 'AI Interview',
  duration_minutes: 30,
  consent_text: 'consent',
  state: 'consented',
  otp_required: false,
  otp_verified_at: null,
  otp_issued_at: null,
}

describe('App', () => {
  it('renders the welcome view in start mode when not connected', () => {
    render(
      <App
        appConfig={APP_CONFIG_DEFAULTS}
        token="tok-1"
        preCheck={PRE_CHECK}
        mode="start"
      />,
    )
    expect(
      screen.getByRole('button', { name: /start interview/i }),
    ).toBeInTheDocument()
  })

  it('renders the rejoin welcome copy in rejoin mode when not connected', () => {
    render(
      <App
        appConfig={APP_CONFIG_DEFAULTS}
        token="tok-1"
        preCheck={PRE_CHECK}
        mode="rejoin"
      />,
    )
    expect(
      screen.getByRole('button', { name: /rejoin interview/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/You were disconnected/i),
    ).toBeInTheDocument()
  })

  it('constructs the LiveKit Room with audioCaptureDefaults disabling EC/NS/AGC and passes it to useSession', () => {
    // Reset the module-level capture arrays so this test sees only its
    // own constructor / hook calls. The arrays accumulate across test
    // runs because the vi.mock factory runs once at module-load.
    roomConstructorCalls.length = 0
    useSessionCalls.length = 0

    render(
      <App
        appConfig={APP_CONFIG_DEFAULTS}
        token="tok-1"
        preCheck={PRE_CHECK}
        mode="start"
      />,
    )

    // The Room was constructed with the Phase 6 audioCaptureDefaults.
    expect(roomConstructorCalls).toHaveLength(1)
    expect(roomConstructorCalls[0]).toEqual({
      audioCaptureDefaults: {
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
      },
    })

    // useSession was invoked with the pre-constructed Room as the second
    // argument's `room` field.
    expect(useSessionCalls).toHaveLength(1)
    const optionsArg = useSessionCalls[0].options as { room?: unknown } | undefined
    expect(optionsArg).toBeDefined()
    expect(optionsArg?.room).toBeDefined()
  })
})
