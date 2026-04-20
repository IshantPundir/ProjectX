'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import { getFreshSupabaseToken } from '@/lib/auth/tokens'
import {
  schedulerApi,
  type InviteCreateBody,
  type InviteResponse,
} from '@/lib/api/scheduler'

export function useSendInvite(candidateId: string) {
  const qc = useQueryClient()
  return useMutation<InviteResponse, Error, InviteCreateBody>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return schedulerApi.sendInvite(token, body)
    },
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: ['candidates', candidateId, 'assignments'],
      })
      void qc.invalidateQueries({ queryKey: ['candidates-kanban'] })
      // Will also populate Sessions tab (Task 3C.2.12)
      void qc.invalidateQueries({ queryKey: ['assignment-sessions'] })
    },
  })
}
