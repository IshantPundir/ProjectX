'use client'

import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { reportsApi, type ProctoringAnalysis } from '@/lib/api/reports'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

/**
 * Fetch a session's post-session vision proctoring analysis. Polls every 5s
 * while still pending/running (the actor runs offline), then stops.
 */
export function useSessionProctoring(sessionId: string): UseQueryResult<ProctoringAnalysis> {
  return useQuery<ProctoringAnalysis>({
    queryKey: ['session-proctoring', sessionId],
    queryFn: async ({ signal }) => {
      const token = await getFreshSupabaseToken()
      return reportsApi.getProctoring(token, sessionId, { signal })
    },
    enabled: !!sessionId,
    refetchInterval: (q) =>
      q.state.data?.status === 'pending' || q.state.data?.status === 'running' ? 5000 : false,
    refetchOnWindowFocus: true,
  })
}
