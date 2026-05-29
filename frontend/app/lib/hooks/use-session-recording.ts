'use client'

import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { reportsApi, type RecordingPlayback } from '@/lib/api/reports'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

/**
 * Fetch a session's recording playback. Polls every 5s while the recording is
 * still processing (the backend reconciles egress status on read), then stops
 * once it's ready/failed/absent. Refetches on window focus to refresh the
 * short-lived signed URL.
 */
export function useSessionRecording(sessionId: string): UseQueryResult<RecordingPlayback> {
  return useQuery<RecordingPlayback>({
    queryKey: ['session-recording', sessionId],
    queryFn: async ({ signal }) => {
      const token = await getFreshSupabaseToken()
      return reportsApi.getRecording(token, sessionId, { signal })
    },
    enabled: !!sessionId,
    refetchInterval: (q) => (q.state.data?.status === 'recording' ? 5000 : false),
    refetchOnWindowFocus: true,
  })
}
