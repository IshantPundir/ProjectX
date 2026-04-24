import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/auth/tokens', () => ({
  getFreshSupabaseToken: vi.fn().mockResolvedValue('tok'),
}))
vi.mock('@/lib/api/jobs', () => ({
  jobsApi: {
    confirmSignals: vi.fn().mockResolvedValue({ id: 'job-1' }),
  },
}))

import { useConfirmSignals } from '@/lib/hooks/use-confirm-signals'

function makeWrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
  }
}

afterEach(() => vi.clearAllMocks())

describe('useConfirmSignals', () => {
  it('invalidates both the job detail and the jobs-list caches', async () => {
    const client = new QueryClient()
    const spy = vi.spyOn(client, 'invalidateQueries')

    const { result } = renderHook(() => useConfirmSignals('job-1'), {
      wrapper: makeWrapper(client),
    })

    await act(async () => {
      await result.current.mutateAsync()
    })

    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith({ queryKey: ['jobs', 'job-1'] })
      expect(spy).toHaveBeenCalledWith({ queryKey: ['jobs-list'] })
    })
  })
})
