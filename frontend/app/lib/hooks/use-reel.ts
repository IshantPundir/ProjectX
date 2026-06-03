'use client'

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from '@tanstack/react-query'

import { ApiError } from '@/lib/api/client'
import { reelApi, type ReelPlayback } from '@/lib/api/reels'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

const reelKey = (sessionId: string) => ['session-reel', sessionId] as const

/** Reel playback, polling every 4s while the render is in flight. */
export function useReel(sessionId: string): UseQueryResult<ReelPlayback> {
  return useQuery<ReelPlayback>({
    queryKey: reelKey(sessionId),
    queryFn: async ({ signal }) =>
      reelApi.get(await getFreshSupabaseToken(), sessionId, { signal }),
    enabled: !!sessionId,
    refetchInterval: (q) => {
      const s = q.state.data?.status
      return s === 'pending' || s === 'generating' ? 4000 : false
    },
    refetchOnWindowFocus: true,
  })
}

/** Trigger (or regenerate) the reel; optimistically flips to 'generating'. */
export function useGenerateReel(sessionId: string) {
  const qc = useQueryClient()
  return useMutation<{ status: string }, ApiError, { regenerate?: boolean } | void>({
    mutationFn: async (vars) => {
      const token = await getFreshSupabaseToken()
      return vars && 'regenerate' in vars && vars.regenerate
        ? reelApi.regenerate(token, sessionId)
        : reelApi.generate(token, sessionId)
    },
    onSuccess: () => {
      qc.setQueryData<ReelPlayback>(reelKey(sessionId), (prev) =>
        prev ? { ...prev, status: 'generating', generation_error: null } : prev,
      )
      void qc.invalidateQueries({ queryKey: reelKey(sessionId) })
    },
  })
}
