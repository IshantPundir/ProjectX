'use client'

import { useQuery } from '@tanstack/react-query'

import { jobsApi, type JobPostingWithSnapshot } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

/**
 * Job detail query.
 *
 * `isStreaming` lets the caller suppress the polling fallback while the
 * SSE stream is alive. When the stream dies the caller passes `false`
 * and polling kicks back in for active processing states.
 */
export function useJob(jobId: string, isStreaming = false) {
  return useQuery<JobPostingWithSnapshot>({
    queryKey: ['jobs', jobId],
    queryFn: async ({ signal }) => {
      const token = await getFreshSupabaseToken()
      return jobsApi.get(token, jobId, { signal })
    },
    enabled: !!jobId,
    staleTime: 5_000,
    refetchInterval: (query) => {
      if (isStreaming) return false
      const data = query.state.data
      if (!data) return false
      if (data.status === 'signals_extracting') return 2_000
      if (data.enrichment_status === 'streaming') return 2_000
      return false
    },
  })
}
