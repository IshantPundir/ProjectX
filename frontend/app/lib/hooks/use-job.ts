'use client'

import { useQuery } from '@tanstack/react-query'

import { jobsApi, type JobPostingWithSnapshot } from '@/lib/api/jobs'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useJob(jobId: string) {
  const query = useQuery<JobPostingWithSnapshot>({
    queryKey: ['jobs', jobId],
    queryFn: async ({ signal }) => {
      const token = await getFreshSupabaseToken()
      return jobsApi.get(token, jobId, { signal })
    },
    enabled: !!jobId,
    staleTime: 5_000,
    // Poll every 2s when the job is in an active processing state.
    // This is a fallback for when the SSE stream fails or disconnects:
    //   - signals_extracting: Call 1 in progress (SSE may have failed)
    //   - enrichment streaming: Call 2 in progress (SSE already closed
    //     because the job is in a terminal state)
    refetchInterval: (query) => {
      const data = query.state.data
      if (!data) return false
      if (data.status === 'signals_extracting') return 2_000
      if (data.enrichment_status === 'streaming') return 2_000
      return false
    },
  })
  return query
}
