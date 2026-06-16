import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { PublicPlaybackProvider } from '@/lib/hooks/public-playback-context'
import { useSessionRecording } from '@/lib/hooks/use-session-recording'

vi.mock('@/lib/auth/tokens', () => ({
  getFreshSupabaseToken: vi.fn(() => {
    throw new Error('must NOT fetch a Supabase token on the public page')
  }),
}))

describe('context-aware playback hooks', () => {
  it('returns context-provided recording without hitting the authed path', async () => {
    const recording = {
      status: 'ready',
      signed_url: 'https://r2/x',
      offset_ms: 0,
      duration_seconds: 120,
      transcript: [],
    }
    const qc = new QueryClient()
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>
        <PublicPlaybackProvider
          value={{
            recording: recording as never,
            proctoring: { status: 'absent' } as never,
          }}
        >
          {children}
        </PublicPlaybackProvider>
      </QueryClientProvider>
    )
    const { result } = renderHook(() => useSessionRecording('sess-1'), { wrapper })
    await waitFor(() => expect(result.current.data?.status).toBe('ready'))
    expect(result.current.data?.signed_url).toBe('https://r2/x')
  })
})
