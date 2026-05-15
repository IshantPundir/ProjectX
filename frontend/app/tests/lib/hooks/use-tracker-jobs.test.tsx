import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import type { ReactNode } from 'react'

import type { JobPostingSummary } from '@/lib/api/jobs'

vi.mock('@/lib/api/jobs', async (orig) => {
  const actual = await orig<typeof import('@/lib/api/jobs')>()
  return {
    ...actual,
    jobsApi: {
      list: vi.fn(),
    },
  }
})
vi.mock('@/lib/auth/tokens', () => ({
  getFreshSupabaseToken: vi.fn(async () => 'tok'),
}))

import { jobsApi } from '@/lib/api/jobs'
import { useTrackerJobs } from '@/lib/hooks/use-tracker-jobs'

function makeJob(over: Partial<JobPostingSummary>): JobPostingSummary {
  return {
    id: 'id',
    title: 'Job',
    org_unit_id: 'ou',
    org_unit_name: 'Acme',
    created_by_email: null,
    updated_by_email: null,
    status: 'active',
    status_error: null,
    created_at: '2026-05-01T00:00:00Z',
    updated_at: '2026-05-15T00:00:00Z',
    signal_count: 0,
    needs_review_count: 0,
    source: 'native',
    external_id: null,
    external_status: null,
    profile_ready: true,
    ...over,
  }
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

describe('useTrackerJobs', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('returns signals_confirmed + pipeline_built + active jobs, sorted by updated_at desc', async () => {
    vi.mocked(jobsApi.list).mockResolvedValue([
      makeJob({ id: 'a', status: 'draft', updated_at: '2026-05-15T10:00:00Z' }),
      makeJob({ id: 'b', status: 'active', updated_at: '2026-05-10T10:00:00Z' }),
      makeJob({ id: 'c', status: 'pipeline_built', updated_at: '2026-05-14T10:00:00Z' }),
      makeJob({ id: 'd', status: 'archived', updated_at: '2026-05-15T11:00:00Z' }),
      makeJob({ id: 'e', status: 'signals_confirmed', updated_at: '2026-05-15T09:00:00Z' }),
    ])

    const { result } = renderHook(() => useTrackerJobs(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    // Order by updated_at desc: e (May 15 09) > c (May 14 10) > b (May 10 10).
    // a (draft) and d (archived) are filtered out.
    expect(result.current.data?.map((j) => j.id)).toEqual(['e', 'c', 'b'])
  })

  it('returns an empty array when the API returns no jobs', async () => {
    vi.mocked(jobsApi.list).mockResolvedValue([])

    const { result } = renderHook(() => useTrackerJobs(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([])
  })

  it('returns an empty array when no jobs match the live-status filter', async () => {
    vi.mocked(jobsApi.list).mockResolvedValue([
      makeJob({ id: 'x', status: 'draft' }),
      makeJob({ id: 'y', status: 'archived' }),
      makeJob({ id: 'z', status: 'signals_extracted' }),
    ])

    const { result } = renderHook(() => useTrackerJobs(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([])
  })

  it('breaks ties on updated_at by id ascending', async () => {
    vi.mocked(jobsApi.list).mockResolvedValue([
      makeJob({ id: 'm', status: 'active', updated_at: '2026-05-15T10:00:00Z' }),
      makeJob({ id: 'a', status: 'active', updated_at: '2026-05-15T10:00:00Z' }),
      makeJob({ id: 'z', status: 'pipeline_built', updated_at: '2026-05-15T10:00:00Z' }),
    ])

    const { result } = renderHook(() => useTrackerJobs(), { wrapper })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.map((j) => j.id)).toEqual(['a', 'm', 'z'])
  })
})
