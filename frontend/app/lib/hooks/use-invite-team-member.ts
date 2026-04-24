'use client'

import { useMutation, useQueryClient } from '@tanstack/react-query'

import {
  teamApi,
  type InviteTeamMemberRequest,
  type InviteTeamMemberResponse,
} from '@/lib/api/team'
import { getFreshSupabaseToken } from '@/lib/auth/tokens'

export function useInviteTeamMember() {
  const qc = useQueryClient()
  return useMutation<InviteTeamMemberResponse, Error, InviteTeamMemberRequest>({
    mutationFn: async (body) => {
      const token = await getFreshSupabaseToken()
      return teamApi.invite(token, body)
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['team', 'members'] })
    },
  })
}
