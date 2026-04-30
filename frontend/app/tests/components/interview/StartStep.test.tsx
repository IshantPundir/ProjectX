import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { StartSessionResponse } from '@/lib/api/candidate-session'

const stubResponse: StartSessionResponse = {
  livekit_url: 'wss://stub.example',
  livekit_token: 'tok-stub',
  room_name: 'session-stub',
  session_id: 'sess-1',
}

vi.mock('@/lib/hooks/use-start-session', () => ({
  useStartSession: () => ({
    mutate: (
      _vars: void,
      opts?: {
        onSuccess?: (resp: StartSessionResponse) => void
        onError?: (err: Error) => void
      },
    ) => {
      opts?.onSuccess?.(stubResponse)
    },
    isPending: false,
  }),
}))

import { StartStep } from '@/app/(interview)/interview/[token]/StartStep'

describe('StartStep', () => {
  it('calls onStarted with the LiveKit creds when /start succeeds', async () => {
    const user = userEvent.setup()
    const onStarted = vi.fn()

    render(<StartStep token="tok-1" onStarted={onStarted} />)

    await user.click(
      screen.getByRole('button', { name: /Start interview/i }),
    )

    expect(onStarted).toHaveBeenCalledTimes(1)
    expect(onStarted).toHaveBeenCalledWith(stubResponse)
  })
})
