'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import { teamApi, type RevokeInviteResponse } from '@/lib/api/team'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useRevokeTeamInvite() {
  const qc = useQueryClient()
  return useMutation<RevokeInviteResponse, Error, string>({
    mutationFn: async (inviteId) => {
      const token = await getFreshSupabaseToken()
      return teamApi.revoke(token, inviteId)
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['team', 'members'] })
    },
  })
}
