'use client'

import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { reportsApi, type RecordingPlayback } from '@/lib/api/reports'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { usePublicPlayback } from '@/lib/hooks/public-playback-context'

/**
 * Fetch a session's recording playback. Polls every 5s while the recording is
 * still processing (the backend reconciles egress status on read), then stops
 * once it's ready/failed/absent. Refetches on window focus to refresh the
 * short-lived signed URL.
 *
 * On the PUBLIC /recordings/<token> page a PublicPlaybackProvider supplies the
 * already-fetched recording; in that mode the hook returns it directly (no
 * authenticated fetch, no polling — the recording is final post-share).
 */
export function useSessionRecording(sessionId: string): UseQueryResult<RecordingPlayback> {
  const pub = usePublicPlayback()
  return useQuery<RecordingPlayback>({
    queryKey: pub ? ['public-recording', sessionId] : ['session-recording', sessionId],
    queryFn: async ({ signal }) => {
      if (pub) return pub.recording
      const token = await getFreshSupabaseToken()
      return reportsApi.getRecording(token, sessionId, { signal })
    },
    enabled: !!sessionId,
    refetchInterval: (q) =>
      !pub && q.state.data?.status === 'recording' ? 5000 : false,
    refetchOnWindowFocus: !pub,
  })
}
