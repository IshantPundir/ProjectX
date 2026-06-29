'use client'

import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { type RecordingPlayback } from '@/components/recordings/api/reports'
import { usePublicPlayback } from '@/components/recordings/hooks/public-playback-context'

/**
 * Public /recordings/<token> variant: the recording is supplied, already
 * fetched, by PublicPlaybackProvider. No authenticated fetch, no polling, no
 * Supabase token (the session app must not import supabase packages). Mirrors the
 * recruiter app's hook signature so the theater components are drop-in.
 */
export function useSessionRecording(sessionId: string): UseQueryResult<RecordingPlayback> {
  const pub = usePublicPlayback()
  return useQuery<RecordingPlayback>({
    queryKey: ['public-recording', sessionId],
    queryFn: async () => {
      if (!pub) {
        throw new Error('useSessionRecording requires PublicPlaybackProvider on the public recordings page')
      }
      return pub.recording
    },
    enabled: !!sessionId,
    refetchInterval: false,
    refetchOnWindowFocus: false,
  })
}
