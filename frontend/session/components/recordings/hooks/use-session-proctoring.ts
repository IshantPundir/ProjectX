'use client'

import { useQuery, type UseQueryResult } from '@tanstack/react-query'

import { type ProctoringAnalysis } from '@/components/recordings/api/reports'
import { usePublicPlayback } from '@/components/recordings/hooks/public-playback-context'

/**
 * Public /recordings/<token> variant: analysis is supplied by
 * PublicPlaybackProvider. No authenticated fetch, no polling, no Supabase.
 */
export function useSessionProctoring(sessionId: string): UseQueryResult<ProctoringAnalysis> {
  const pub = usePublicPlayback()
  return useQuery<ProctoringAnalysis>({
    queryKey: ['public-proctoring', sessionId],
    queryFn: async () => {
      if (!pub) {
        throw new Error('useSessionProctoring requires PublicPlaybackProvider on the public recordings page')
      }
      return pub.proctoring
    },
    enabled: !!sessionId,
    refetchInterval: false,
    refetchOnWindowFocus: false,
  })
}
