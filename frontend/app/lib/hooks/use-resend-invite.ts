'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import { schedulerApi, type InviteResponse } from '@/lib/api/scheduler'

export function useResendInvite() {
  const qc = useQueryClient()
  return useMutation<InviteResponse, Error, { sessionId: string }>({
    mutationFn: async ({ sessionId }) => {
      const token = await getFreshSupabaseToken()
      return schedulerApi.resendInvite(token, sessionId)
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['assignment-sessions'] })
    },
  })
}
