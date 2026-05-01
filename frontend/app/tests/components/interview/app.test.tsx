/**
 * Smoke test for the App entry point's welcome-view branch in start vs
 * rejoin modes. Heavy mocking — the goal is to verify our outcome-routing
 * code, not LiveKit's session lifecycle. Anything below useSession is
 * stubbed out.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@livekit/components-react', () => ({
  useSession: () => ({
    start: vi.fn(),
    end: vi.fn(),
    isConnected: false,
    connectionState: 'idle',
    room: undefined,
  }),
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

vi.mock('livekit-client', () => ({
  TokenSource: { custom: () => ({}) },
  RoomEvent: { Disconnected: 'disconnected' },
  Room: class {},
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
})
