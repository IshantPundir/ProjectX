'use client'

import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { reportsApi, type ProctoringAnalysis } from '@/lib/api/reports'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { usePublicPlayback } from '@/lib/hooks/public-playback-context'

/**
 * Fetch a session's post-session vision proctoring analysis. Polls every 5s
 * while still pending/running (the actor runs offline), then stops.
 *
 * On the PUBLIC /recordings/<token> page a PublicPlaybackProvider supplies the
 * already-fetched analysis; in that mode the hook returns it directly (no
 * authenticated fetch, no polling).
 */
export function useSessionProctoring(sessionId: string): UseQueryResult<ProctoringAnalysis> {
  const pub = usePublicPlayback()
  return useQuery<ProctoringAnalysis>({
    queryKey: pub ? ['public-proctoring', sessionId] : ['session-proctoring', sessionId],
    queryFn: async ({ signal }) => {
      if (pub) return pub.proctoring
      const token = await getFreshSupabaseToken()
      return reportsApi.getProctoring(token, sessionId, { signal })
    },
    enabled: !!sessionId,
    refetchInterval: (q) =>
      !pub && (q.state.data?.status === 'pending' || q.state.data?.status === 'running')
        ? 5000
        : false,
    refetchOnWindowFocus: !pub,
  })
}
