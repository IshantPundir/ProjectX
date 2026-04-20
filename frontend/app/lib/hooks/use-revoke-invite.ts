'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { schedulerApi } from '@/lib/api/scheduler'

export function useRevokeInvite() {
  const qc = useQueryClient()
  return useMutation<void, Error, { sessionId: string }>({
    mutationFn: async ({ sessionId }) => {
      const token = await getFreshSupabaseToken()
      return schedulerApi.revokeInvite(token, sessionId)
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['assignment-sessions'] })
      void qc.invalidateQueries({ queryKey: ['candidates-kanban'] })
    },
  })
}
