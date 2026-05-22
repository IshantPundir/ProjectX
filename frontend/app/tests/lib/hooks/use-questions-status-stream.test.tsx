import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/auth/tokens', () => ({
  getFreshSupabaseToken: vi.fn().mockResolvedValue('fake-token'),
}))

// Module-level mock — individual tests override the implementation via
// vi.mocked(fetchEventSource).mockImplementationOnce(...).
vi.mock('@microsoft/fetch-event-source', () => ({
  EventStreamContentType: 'text/event-stream',
  fetchEventSource: vi.fn(async (_url: string, opts: Record<string, unknown>) => {
    const onopen = opts.onopen as (r: Response) => Promise<void>
    const onerror = opts.onerror as (e: unknown) => void
    // Mirror @microsoft/fetch-event-source's real flow:
    // if onopen throws, the library forwards the error to onerror.
    // The hook's onerror is expected to rethrow AuthSSEError; only then
    // does the outer promise reject and the hook's catch run.
    try {
      await onopen({
        ok: false,
        status: 401,
        headers: new Headers(),
      } as Response)
    } catch (err) {
      onerror(err) // production library does this
      throw err   // production library only re-throws if onerror also throws
    }
  }),
}))

import { fetchEventSource } from '@microsoft/fetch-event-source'
import { useQuestionsStatusStream } from '@/lib/hooks/use-questions-status-stream'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
  }
  return { client, Wrapper }
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('useQuestionsStatusStream — auth retry', () => {
  it('refreshes the token and reconnects once on a 401', async () => {
    const { Wrapper } = makeWrapper()
    renderHook(() => useQuestionsStatusStream('job-1', null), { wrapper: Wrapper })
    await waitFor(() => {
      // Initial attempt + 2 auth retries = 3 calls total
      // (MAX_AUTH_RETRIES=2 allows 2 reconnect attempts after initial failure)
      expect(getFreshSupabaseToken).toHaveBeenCalledTimes(3)
    })
  })
})

describe('useQuestionsStatusStream — bank.question_added invalidation', () => {
  it('invalidates the per-stage bank query when bank.question_added fires', async () => {
    const { client, Wrapper } = makeWrapper()

    // Spy on invalidateQueries so we can assert the exact key
    const invalidateSpy = vi.spyOn(client, 'invalidateQueries')

    // Override fetchEventSource for this test: open successfully then fire
    // a bank.question_added message.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    vi.mocked(fetchEventSource).mockImplementationOnce(async (_url: any, opts: any) => {
      const onopen = opts.onopen as (r: Response) => Promise<void>
      const onmessage = opts.onmessage as (ev: { event: string; data: string }) => void

      await onopen({
        ok: true,
        status: 200,
        headers: new Headers({ 'content-type': 'text/event-stream' }),
      } as Response)

      // Fire a bank.question_added event
      onmessage({ event: 'bank.question_added', data: '{}' })
    })

    renderHook(() => useQuestionsStatusStream('job-1', 'stage-1'), {
      wrapper: Wrapper,
    })

    await waitFor(() => {
      // The generic banks overview invalidation fires on every message
      expect(invalidateSpy).toHaveBeenCalledWith(
        expect.objectContaining({ queryKey: ['banks', 'job-1'] }),
      )
      // bank.question_added must also invalidate the per-stage bank query
      expect(invalidateSpy).toHaveBeenCalledWith(
        expect.objectContaining({ queryKey: ['bank', 'job-1', 'stage-1'] }),
      )
    })
  })
})
