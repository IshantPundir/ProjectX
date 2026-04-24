'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import { teamApi, type ResendInviteResponse } from '@/lib/api/team'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useResendTeamInvite() {
  const qc = useQueryClient()
  return useMutation<ResendInviteResponse, Error, string>({
    mutationFn: async (inviteId) => {
      const token = await getFreshSupabaseToken()
      return teamApi.resend(token, inviteId)
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['team', 'members'] })
    },
  })
}
