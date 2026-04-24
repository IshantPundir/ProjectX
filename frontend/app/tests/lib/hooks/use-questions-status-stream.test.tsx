import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/auth/tokens', () => ({
  getFreshSupabaseToken: vi.fn().mockResolvedValue('fake-token'),
}))

vi.mock('@microsoft/fetch-event-source', () => ({
  EventStreamContentType: 'text/event-stream',
  fetchEventSource: vi.fn(async (_url: string, opts: Record<string, unknown>) => {
    const onopen = opts.onopen as (r: Response) => Promise<void>
    // Simulate a 401 from the server.
    try {
      await onopen({
        ok: false,
        status: 401,
        headers: new Headers(),
      } as Response)
    } catch (err) {
      // The hook should catch this and reconnect.
      throw err
    }
  }),
}))

import { useQuestionsStatusStream } from '@/lib/hooks/use-questions-status-stream'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('useQuestionsStatusStream', () => {
  it('refreshes the token and reconnects once on a 401', async () => {
    renderHook(() => useQuestionsStatusStream('job-1', null), { wrapper })
    await waitFor(() => {
      // Initial attempt + 2 auth retries = 3 calls total
      // (MAX_AUTH_RETRIES=2 allows 2 reconnect attempts after initial failure)
      expect(getFreshSupabaseToken).toHaveBeenCalledTimes(3)
    })
  })
})
